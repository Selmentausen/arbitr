"""
Scraper wrapper for the distributed worker.

Ports the battle-tested logic from src/scraper/runner.py into the
stateless worker context. Uses an on_batch_ready callback to submit
cases incrementally to the orchestrator after each filter+enrich batch,
so a crash mid-scrape loses at most one batch (~10-25 cases).

This is the only file in worker/ that depends on src/.
"""
import asyncio
import math
from typing import Optional, List, Dict, Any, Callable, Awaitable

from worker.models import ScrapeResult
from worker.block import detect_block, is_content_suspicious

# Existing project imports
from src.scraper.playwright_scraper import PlaywrightScraper
from src.filters.pipeline import FilterPipeline
from src.config.manager import ConfigManager
from src.models.case import Case
from src.utils.logger import get_logger

logger = get_logger(__name__)

PAGE_SIZE = 25


class WorkerScraper:
    """
    Wraps the existing PlaywrightScraper for use in the distributed worker.

    Mirrors the logic from runner.py._process_judge() but:
      - Reports results via ScrapeResult instead of DB writes
      - Uses SOCKS5 proxy via microsocks instead of residential proxy
      - Returns on block detection instead of raising exceptions
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self.filter_pipeline = FilterPipeline(config)

    async def scrape_judge(
        self,
        judge_name: str,
        proxy_port: Optional[int] = None,
        proxy_bind_ip: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
        # Resume info from orchestrator job claim
        prev_collected: int = 0,
        prev_total: int = 0,
        max_cases: int = 0,
        # Incremental submission callback — called after each enrichment batch
        on_batch_ready: Optional[
            Callable[[str, List[Dict[str, Any]], int], Awaitable[None]]
        ] = None,
    ) -> ScrapeResult:
        """
        Scrape all cases for a judge.

        Mirrors runner.py._process_judge() logic:
          1. Initial UI search → warm session + get site_total
          2. Soft-block check
          3. If resuming: Phase 1 (new cases) then Phase 2 (continue from checkpoint)
          4. If fresh: collect in batches from page 1
          5. Filter → enrich → submit via callback → accumulate totals

        Args:
            judge_name: Full judge name (e.g., "Титова О. А.")
            proxy_port: Local SOCKS proxy port
            proxy_bind_ip: IP the proxy binds to (for logging)
            stop_event: Shutdown signal
            prev_collected: Cases already collected (from orchestrator)
            prev_total: Site total at last scrape start (for new-case detection)
            max_cases: Max cases to collect (0 = unlimited)
            on_batch_ready: Async callback(judge_name, batch_dicts, total_submitted)
                            called after each enrichment batch so main.py can
                            submit to the orchestrator incrementally.

        Returns:
            ScrapeResult with totals and block status.
        """
        result = ScrapeResult(judge_name=judge_name)
        is_resume = prev_collected > 0

        socks_proxy = (
            f"socks5://127.0.0.1:{proxy_port}" if proxy_port else None
        )
        effective_max = max_cases if max_cases > 0 else 999_999
        enrich_batch = self.config.get("scraping.batch_size", 10)
        court = self.config.get(
            "scraping.parallel.default_court", "АС города Москвы"
        )
        collect_batch_size = self.config.get("scraping.collect_batch_size", 0)
        if collect_batch_size <= 0:
            collect_batch_size = effective_max

        total_submitted = 0

        try:
            async with PlaywrightScraper(
                self.config, headless=False, socks_proxy=socks_proxy
            ) as scraper:

                # ── Step 1: Initial UI search (page 1) ──────────────
                logger.info("Collecting cases for judge: %s", judge_name)
                first_cases, pagination = await scraper.collect_cases(
                    court_name=court,
                    judge_name=judge_name,
                    max_cases=PAGE_SIZE,
                    start_page=1,
                )
                site_total = pagination.get("total_count", 0)
                result.site_total = site_total
                result.total_cases_found = site_total

                # ── Step 2: Block / empty check ─────────────────────
                if not first_cases:
                    page_html = await scraper.page.content()
                    is_blocked, reason = detect_block(
                        response_html=page_html,
                        cases_found=0,
                        expected_cases=(
                            site_total if site_total > 0 else prev_collected
                        ),
                    )
                    if is_blocked or (
                        site_total > 0 or prev_collected > 0
                    ):
                        result.is_blocked = True
                        result.block_reason = reason or (
                            f"Soft-blocked: page 1 returned 0 cases "
                            f"(site_total={site_total}, "
                            f"prev_collected={prev_collected})"
                        )
                        logger.warning(
                            "Block detected for judge %s: %s",
                            judge_name,
                            result.block_reason,
                        )
                        return result

                    # Legitimately 0 cases
                    logger.info(
                        "No cases found for judge %s", judge_name
                    )
                    result.success = True
                    return result

                # Update effective_max from site_total if unlimited
                if effective_max == 999_999 and site_total:
                    effective_max = site_total

                new_cases = (
                    max(0, site_total - prev_total) if is_resume else 0
                )

                logger.info(
                    "Judge %s: site_total=%d, prev_collected=%d, "
                    "new_cases=%d, max=%d",
                    judge_name,
                    site_total,
                    prev_collected,
                    new_cases,
                    effective_max,
                )

                # ── Phase 1: Collect new cases (resume only) ────────
                if is_resume and new_cases > 0:
                    new_pages = math.ceil(new_cases / PAGE_SIZE)
                    logger.info(
                        "Phase 1: collecting %d new cases (%d pages)",
                        new_cases,
                        new_pages,
                    )

                    new_collected = list(first_cases)
                    if new_cases > PAGE_SIZE:
                        more, _ = await scraper.collect_cases(
                            court_name=court,
                            judge_name=judge_name,
                            max_cases=new_cases - PAGE_SIZE,
                            start_page=2,
                        )
                        new_collected.extend(more)

                    new_collected = new_collected[:new_cases]
                    if new_collected:
                        batch_dicts = await self._filter_enrich_convert(
                            scraper,
                            new_collected,
                            enrich_batch,
                            judge_name,
                            court,
                            is_resume=False,
                        )
                        total_submitted += len(batch_dicts)

                        # Submit incrementally
                        if on_batch_ready and batch_dicts:
                            await on_batch_ready(
                                judge_name, batch_dicts, total_submitted
                            )

                        # Check for block after enrichment
                        if await self._check_block(scraper, result):
                            result.cases_after_filter = total_submitted
                            return result

                        prev_collected += len(new_collected)
                        logger.info(
                            "Phase 1 done: %d new cases processed",
                            len(new_collected),
                        )

                # ── Phase 2: Batched collection from resume point ───
                cases_remaining = effective_max - prev_collected
                if cases_remaining <= 0:
                    logger.info(
                        "Judge %s: max_cases reached (%d)",
                        judge_name,
                        effective_max,
                    )
                else:
                    if is_resume:
                        skip_count = prev_collected + new_cases
                        start_page = (skip_count // PAGE_SIZE) + 1
                    else:
                        start_page = 1

                    cases_so_far = 0
                    current_start = start_page
                    first_batch = True

                    while cases_so_far < cases_remaining:
                        if stop_event and stop_event.is_set():
                            logger.info(
                                "Shutdown requested during collection"
                            )
                            break

                        this_batch = min(
                            collect_batch_size,
                            cases_remaining - cases_so_far,
                        )

                        if (
                            first_batch
                            and not is_resume
                            and start_page == 1
                        ):
                            # Reuse page 1 results from initial search
                            batch_cases = list(first_cases)
                            leftover = this_batch - len(batch_cases)
                            if leftover > 0:
                                more, _ = await scraper.collect_cases(
                                    court_name=court,
                                    judge_name=judge_name,
                                    max_cases=leftover,
                                    start_page=2,
                                )
                                batch_cases.extend(more)
                                current_start = 2 + math.ceil(
                                    leftover / PAGE_SIZE
                                )
                            else:
                                batch_cases = batch_cases[:this_batch]
                                current_start = 2
                        else:
                            batch_cases, _ = await scraper.collect_cases(
                                court_name=court,
                                judge_name=judge_name,
                                max_cases=this_batch,
                                start_page=current_start,
                            )
                            pages_fetched = (
                                math.ceil(len(batch_cases) / PAGE_SIZE)
                                if batch_cases
                                else 0
                            )
                            current_start += pages_fetched

                        first_batch = False

                        # Soft-block detection mid-collection
                        if not batch_cases:
                            total_so_far = (
                                prev_collected + cases_so_far
                            )
                            if (
                                site_total > 0
                                and total_so_far < site_total * 0.9
                            ):
                                result.is_blocked = True
                                result.block_reason = (
                                    f"Soft-blocked at page "
                                    f"{current_start}: 0 cases "
                                    f"returned ({total_so_far}"
                                    f"/{site_total})"
                                )
                                logger.warning(
                                    "Soft-block: %s",
                                    result.block_reason,
                                )
                                result.cases_after_filter = total_submitted
                                return result
                            logger.info(
                                "No more cases from site — "
                                "collection done"
                            )
                            break

                        logger.info(
                            "Batch: collected %d cases "
                            "(total so far: %d + %d = %d)",
                            len(batch_cases),
                            prev_collected,
                            cases_so_far + len(batch_cases),
                            prev_collected
                            + cases_so_far
                            + len(batch_cases),
                        )

                        # Filter → enrich → convert
                        batch_dicts = await self._filter_enrich_convert(
                            scraper,
                            batch_cases,
                            enrich_batch,
                            judge_name,
                            court,
                            is_resume=is_resume,
                        )
                        total_submitted += len(batch_dicts)
                        cases_so_far += len(batch_cases)

                        # Submit incrementally
                        if on_batch_ready and batch_dicts:
                            await on_batch_ready(
                                judge_name, batch_dicts, total_submitted
                            )

                        # Check for block after enrichment
                        if await self._check_block(scraper, result):
                            result.cases_after_filter = total_submitted
                            return result

                result.cases_after_filter = total_submitted
                result.success = True
                logger.info(
                    "Judge %s complete: %d cases submitted",
                    judge_name,
                    total_submitted,
                )

        except Exception as e:
            logger.error(
                "Scraper error for judge %s: %s",
                judge_name,
                e,
                exc_info=True,
            )
            result.error = str(e)
            result.cases_after_filter = total_submitted

        return result

    async def _filter_enrich_convert(
        self,
        scraper: PlaywrightScraper,
        cases: list,
        enrich_batch: int,
        judge_name: str,
        court: str,
        is_resume: bool,
    ) -> List[Dict[str, Any]]:
        """
        Filter a batch, enrich eligible cases, run Stage 2,
        then convert to dicts.

        Mirrors runner.py._filter_enrich_save() but returns dicts
        instead of saving to DB.
        """
        # Stage 1 filter
        processed = self.filter_pipeline.process_batch(cases)
        to_enrich = self.filter_pipeline.cases_for_enrichment(processed)

        if to_enrich:
            logger.info(
                "Enriching %d/%d cases (batch_size=%d)",
                len(to_enrich),
                len(processed),
                enrich_batch,
            )
            await scraper.batch_enrich_cases(
                to_enrich,
                batch_size=enrich_batch,
                judge_name=judge_name,
                court_name=court,
                skip_enriched=is_resume,
            )
            # Stage 2 filter (keyword matching on enriched HTML)
            self.filter_pipeline.process_stage2_batch(to_enrich)

        return [self._case_to_dict(c) for c in processed]

    async def _check_block(
        self,
        scraper: PlaywrightScraper,
        result: ScrapeResult,
    ) -> bool:
        """
        Check the current page for block indicators.
        If blocked, sets result.is_blocked and returns True.
        """
        try:
            page_html = await scraper.page.content()
            if is_content_suspicious(page_html):
                result.is_blocked = True
                result.block_reason = (
                    "Block detected: suspicious page content "
                    "after enrichment"
                )
                logger.warning(
                    "Block detected during scraping: %s",
                    result.block_reason,
                )
                return True
        except Exception:
            pass  # Can't read page — keep going
        return False

    def _case_to_dict(self, case: Case) -> Dict[str, Any]:
        """
        Convert a Pydantic Case to a flat dict for JSON serialization.
        """
        data = case.model_dump(mode="json", exclude_none=True)

        # Flatten participants from dict-of-lists → flat list with role
        flat_participants = []
        for role, participants in (
            getattr(case, "participants", None) or {}
        ).items():
            for p in participants:
                flat_participants.append(
                    {
                        "name": p.name,
                        "role": p.role or role,
                        "inn": p.inn,
                        "address": p.address,
                        "ogrn": p.ogrn,
                    }
                )
        data["participants"] = flat_participants

        return data
