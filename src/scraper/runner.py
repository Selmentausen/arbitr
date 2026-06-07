"""Parallel scrape: multiple asyncio workers, each with its own Playwright session and proxy port."""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime
from typing import List, Optional

from src.config.manager import ConfigManager
from src.filters.pipeline import FilterPipeline
from src.scraper.judge_loader import JudgeEntry
from src.scraper.playwright_scraper import JudgeCourtNotFoundError, PlaywrightScraper
from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

PAGE_SIZE = 25


class ParallelScrapeRunner:
    def __init__(
        self,
        config: ConfigManager,
        num_workers: int,
        court: str,
        max_cases_per_judge: int,
        headless: bool = True,
        config_path: str = "configs/main.yaml",
        db_path: str = "data/arbitr.db",
        skip_new: bool = False,
    ):
        self.config = config
        self.num_workers = max(1, num_workers)
        self.court = court
        self.max_cases = max_cases_per_judge
        self.headless = headless
        self.config_path = config_path
        self.db_path = db_path
        self.skip_new = skip_new
        self.collect_batch_size = config.get("scraping.collect_batch_size", 0)

        port_min = config.get("scraping.proxy.port_range.min", 10000)
        port_max = config.get("scraping.proxy.port_range.max", 10999)
        self.port_min = port_min
        self.port_max = port_max

        if config.get("scraping.proxy.enabled", False):
            if port_min + self.num_workers - 1 > port_max:
                raise ValueError(
                    f"Proxy port range {port_min}-{port_max} cannot assign "
                    f"{self.num_workers} distinct ports"
                )

    def _bump_port(self, port: Optional[int]) -> Optional[int]:
        if port is None:
            return None
        span = self.port_max - self.port_min + 1
        return self.port_min + (port - self.port_min + 1) % span

    def _initial_ports(self) -> List[Optional[int]]:
        if not self.config.get("scraping.proxy.enabled", False):
            return [None] * self.num_workers
        return [self.port_min + i for i in range(self.num_workers)]

    def _worker_config(self, forced_port: Optional[int]) -> ConfigManager:
        cm = ConfigManager(self.config_path)
        if forced_port is not None and cm.get("scraping.proxy.enabled", False):
            cm._config.setdefault("scraping", {}).setdefault("proxy", {})[
                "forced_port"
            ] = forced_port
        return cm

    # ------------------------------------------------------------------
    # Core per-judge logic
    # ------------------------------------------------------------------

    async def _process_judge(
        self,
        scraper: PlaywrightScraper,
        worker_cfg: ConfigManager,
        judge: JudgeEntry,
    ) -> int:
        repo = CaseRepository()
        try:
            progress = repo.get_judge_progress(judge.display_name)

            if progress and progress.status == "completed":
                logger.info("Judge %s already completed — skipping", judge.display_name)
                return 0

            is_resume = progress and progress.status in ("collecting", "enriching", "failed")
            prev_collected = progress.cases_collected if is_resume else 0
            prev_total = progress.total_count_at_start if is_resume else 0
        finally:
            repo.close()

        # Determine effective max (0 = unlimited → use site total)
        effective_max = self.max_cases if self.max_cases > 0 else 999_999
        batch_size = self.collect_batch_size if self.collect_batch_size > 0 else effective_max
        enrich_batch = worker_cfg.get("scraping.batch_size", 10)

        total_processed = 0

        # --- Initial UI search to warm session + get total_count ---
        first_cases, pagination = await scraper.collect_cases(
            court_name=self.court,
            judge_name=judge.search_name,
            max_cases=PAGE_SIZE,
            start_page=1,
        )
        site_total = pagination.get("total_count", 0)

        # Detect soft-block: page 1 returned 0 cases when we know there should be more
        if not first_cases and (site_total > 0 or prev_collected > 0):
            logger.warning(
                "Judge %s: page 1 returned 0 cases but site_total=%d, prev_collected=%d — likely soft-blocked",
                judge.display_name, site_total, prev_collected,
            )
            repo = CaseRepository()
            try:
                repo.upsert_judge_progress(
                    judge.display_name,
                    status="failed",
                    error_message="Soft-blocked: page 1 returned 0 cases",
                )
            finally:
                repo.close()
            raise RuntimeError(f"Soft-blocked by site for judge {judge.display_name}")

        if effective_max == 999_999 and site_total:
            effective_max = site_total
        new_cases = max(0, site_total - prev_total) if is_resume else 0

        logger.info(
            "Judge %s: site_total=%d, prev_collected=%d, new_cases=%d, max=%d, batch=%d",
            judge.display_name, site_total, prev_collected, new_cases, effective_max, batch_size,
        )

        # Mark as collecting
        repo = CaseRepository()
        try:
            repo.upsert_judge_progress(
                judge.display_name,
                court=self.court,
                status="collecting",
                total_count_at_start=prev_total if is_resume else site_total,
                max_cases=effective_max,
            )
        finally:
            repo.close()

        # --- Phase 1: Collect new cases (if resuming and there are new ones) ---
        if is_resume and new_cases > 0 and not self.skip_new:
            new_pages = math.ceil(new_cases / PAGE_SIZE)
            logger.info("Phase 1: collecting %d new cases (%d pages)", new_cases, new_pages)

            new_collected = list(first_cases)
            if new_cases > PAGE_SIZE:
                more, _ = await scraper.collect_cases(
                    court_name=self.court,
                    judge_name=judge.search_name,
                    max_cases=new_cases - PAGE_SIZE,
                    start_page=2,
                )
                new_collected.extend(more)

            if new_collected:
                new_collected = new_collected[:new_cases]
                await self._filter_enrich_save(
                    scraper, worker_cfg, judge, new_collected, enrich_batch, is_resume=False,
                )
                total_processed += len(new_collected)
                prev_collected += len(new_collected)
                logger.info("Phase 1 done: %d new cases processed", len(new_collected))
        elif is_resume and self.skip_new and new_cases > 0:
            logger.info("Skipping %d new cases (--skip-new)", new_cases)

        # --- Phase 2: Batched collection from resume point ---
        cases_remaining = effective_max - prev_collected
        if cases_remaining <= 0:
            logger.info("Judge %s: max_cases reached (%d)", judge.display_name, effective_max)
        else:
            # Calculate starting page
            if is_resume:
                skip_count = prev_collected + new_cases
                start_page = (skip_count // PAGE_SIZE) + 1
            else:
                start_page = 1

            cases_so_far = 0
            current_start = start_page
            first_batch = True

            while cases_so_far < cases_remaining:
                this_batch = min(batch_size, cases_remaining - cases_so_far)

                if first_batch and not is_resume and start_page == 1:
                    batch_cases = list(first_cases)
                    leftover = this_batch - len(batch_cases)
                    if leftover > 0:
                        more, _ = await scraper.collect_cases(
                            court_name=self.court,
                            judge_name=judge.search_name,
                            max_cases=leftover,
                            start_page=2,
                        )
                        batch_cases.extend(more)
                        current_start = 2 + math.ceil(leftover / PAGE_SIZE)
                    else:
                        batch_cases = batch_cases[:this_batch]
                        current_start = 2
                else:
                    def _on_page(page_num, count):
                        r = CaseRepository()
                        try:
                            r.upsert_judge_progress(
                                judge.display_name,
                                status="collecting",
                                cases_collected=prev_collected + cases_so_far + count,
                            )
                        finally:
                            r.close()

                    batch_cases, _ = await scraper.collect_cases(
                        court_name=self.court,
                        judge_name=judge.search_name,
                        max_cases=this_batch,
                        start_page=current_start,
                        on_page_done=_on_page,
                    )
                    pages_fetched = math.ceil(len(batch_cases) / PAGE_SIZE) if batch_cases else 0
                    current_start += pages_fetched

                first_batch = False

                if not batch_cases:
                    total_so_far = prev_collected + cases_so_far
                    if site_total > 0 and total_so_far < site_total * 0.9:
                        logger.warning(
                            "Page returned 0 cases but only %d/%d collected — likely soft-blocked",
                            total_so_far, site_total,
                        )
                        repo = CaseRepository()
                        try:
                            repo.upsert_judge_progress(
                                judge.display_name,
                                status="failed",
                                cases_collected=total_so_far,
                                error_message=f"Soft-blocked at page {current_start}: 0 cases returned ({total_so_far}/{site_total})",
                            )
                        finally:
                            repo.close()
                        raise RuntimeError(f"Soft-blocked at page {current_start} for judge {judge.display_name}")
                    logger.info("No more cases from site — collection done")
                    break

                logger.info(
                    "Batch: collected %d cases (total so far: %d + %d = %d)",
                    len(batch_cases), prev_collected, cases_so_far + len(batch_cases),
                    prev_collected + cases_so_far + len(batch_cases),
                )

                # Filter → enrich → save this batch
                await self._filter_enrich_save(
                    scraper, worker_cfg, judge, batch_cases, enrich_batch, is_resume=is_resume,
                )
                cases_so_far += len(batch_cases)
                total_processed += len(batch_cases)

                # Checkpoint progress
                repo = CaseRepository()
                try:
                    repo.upsert_judge_progress(
                        judge.display_name,
                        status="collecting",
                        cases_collected=prev_collected + cases_so_far,
                    )
                finally:
                    repo.close()

        # --- Mark completed ---
        repo = CaseRepository()
        try:
            repo.upsert_judge_progress(
                judge.display_name,
                status="completed",
                cases_collected=prev_collected + total_processed - (len(first_cases) if is_resume and new_cases > 0 and not self.skip_new else 0),
                completed_at=datetime.utcnow(),
            )
        finally:
            repo.close()

        logger.info("Judge %s completed: %d cases processed", judge.display_name, total_processed)
        return total_processed

    async def _filter_enrich_save(
        self,
        scraper: PlaywrightScraper,
        worker_cfg: ConfigManager,
        judge: JudgeEntry,
        cases,
        enrich_batch: int,
        is_resume: bool,
    ) -> None:
        """Filter a batch, enrich eligible cases, then save to DB."""
        pipeline = FilterPipeline(worker_cfg)
        processed = pipeline.process_batch(cases)
        to_enrich = pipeline.cases_for_enrichment(processed)

        if to_enrich:
            # Update progress to enriching
            repo = CaseRepository()
            try:
                repo.upsert_judge_progress(judge.display_name, status="enriching")
            finally:
                repo.close()

            await scraper.batch_enrich_cases(
                to_enrich,
                batch_size=enrich_batch,
                judge_name=judge.search_name,
                court_name=self.court,
                skip_enriched=is_resume,
            )
            pipeline.process_stage2_batch(to_enrich)

        repo = CaseRepository()
        try:
            repo.save_cases(processed)
        finally:
            repo.close()

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker(
        self,
        worker_id: int,
        port_state: List[Optional[int]],
        queue: asyncio.Queue,
        session_id: str,
    ) -> None:
        requeue_max = self.config.get("scraping.parallel.requeue_on_failure", 1)

        while True:
            task = await queue.get()
            if task is None:
                queue.task_done()
                return

            judge: JudgeEntry = task["judge"]
            attempt: int = task.get("attempt", 0)
            port = port_state[0]
            worker_cfg = self._worker_config(port)

            repo = CaseRepository()
            ev_id = repo.start_scrape_event(judge.display_name, worker_id, port, session_id=session_id)
            repo.close()

            try:
                async with PlaywrightScraper(worker_cfg, headless=self.headless) as scraper:
                    n = await self._process_judge(scraper, worker_cfg, judge)
                repo = CaseRepository()
                try:
                    repo.finish_scrape_event(ev_id, n, "success")
                finally:
                    repo.close()

            except asyncio.CancelledError:
                repo = CaseRepository()
                try:
                    progress = repo.get_judge_progress(judge.display_name)
                    actual_count = progress.cases_collected if progress else 0
                    repo.finish_scrape_event(
                        ev_id, actual_count, "interrupted", "Worker cancelled (Ctrl+C / shutdown)"
                    )
                finally:
                    repo.close()
                raise

            except JudgeCourtNotFoundError as e:
                repo = CaseRepository()
                try:
                    repo.finish_scrape_event(ev_id, 0, "no_match", str(e))
                finally:
                    repo.close()

            except Exception as e:
                logger.exception(
                    "Worker %s failed for judge %s: %s",
                    worker_id,
                    judge.display_name,
                    e,
                )
                repo = CaseRepository()
                try:
                    progress = repo.get_judge_progress(judge.display_name)
                    actual_count = progress.cases_collected if progress else 0
                    repo.finish_scrape_event(ev_id, actual_count, "error", str(e))
                    repo.upsert_judge_progress(
                        judge.display_name,
                        status="failed",
                        error_message=str(e),
                    )
                finally:
                    repo.close()

                if attempt < requeue_max:
                    port_state[0] = self._bump_port(port_state[0])
                    await queue.put({"judge": judge, "attempt": attempt + 1})

            finally:
                queue.task_done()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self, judges: List[JudgeEntry]) -> None:
        init_db(self.db_path)
        session_id = uuid.uuid4().hex[:12]
        logger.info("Session ID: %s", session_id)

        repo = CaseRepository()
        try:
            cleaned = repo.mark_running_events_interrupted(
                reason="Recovered stale running event on startup"
            )
            if cleaned:
                logger.info("Marked %s stale running event(s) as interrupted", cleaned)

            skipped = 0
            active_judges = []
            for j in judges:
                progress = repo.get_judge_progress(j.display_name)
                if progress and progress.status == "completed":
                    skipped += 1
                else:
                    active_judges.append(j)
            if skipped:
                logger.info("Skipping %d already-completed judges", skipped)
            logger.info("Queuing %d judges for processing", len(active_judges))
        finally:
            repo.close()

        q: asyncio.Queue = asyncio.Queue()
        for j in active_judges:
            await q.put({"judge": j, "attempt": 0})
        for _ in range(self.num_workers):
            await q.put(None)

        initial = self._initial_ports()
        tasks = []
        for i in range(self.num_workers):
            port_state = [initial[i]]
            tasks.append(asyncio.create_task(self._worker(i, port_state, q, session_id)))

        try:
            await asyncio.gather(*tasks)
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.warning("Shutdown requested; cancelling worker tasks...")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            repo = CaseRepository()
            try:
                repo.mark_running_events_interrupted(reason="Runner shutdown cleanup")
            finally:
                repo.close()
