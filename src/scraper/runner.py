"""Parallel scrape: multiple asyncio workers, each with its own Playwright session and proxy port."""

from __future__ import annotations

import asyncio
import uuid
from typing import List, Optional

from src.config.manager import ConfigManager
from src.filters.pipeline import FilterPipeline
from src.scraper.judge_loader import JudgeEntry
from src.scraper.playwright_scraper import JudgeCourtNotFoundError, PlaywrightScraper
from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


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
    ):
        self.config = config
        self.num_workers = max(1, num_workers)
        self.court = court
        self.max_cases = max_cases_per_judge
        self.headless = headless
        self.config_path = config_path
        self.db_path = db_path

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

    async def _process_judge(
        self,
        scraper: PlaywrightScraper,
        worker_cfg: ConfigManager,
        judge: JudgeEntry,
    ) -> int:
        cases = await scraper.collect_cases(
            court_name=self.court,
            judge_name=judge.search_name,
            max_cases=self.max_cases,
        )
        n = len(cases)
        if not cases:
            return 0

        pipeline = FilterPipeline(worker_cfg)
        processed = pipeline.process_batch(cases)
        to_enrich = pipeline.cases_for_enrichment(processed)
        if to_enrich:
            batch_size = worker_cfg.get("scraping.batch_size", 10)
            await scraper.batch_enrich_cases(
                to_enrich,
                batch_size=batch_size,
                judge_name=judge.search_name,
                court_name=self.court,
            )
            pipeline.process_stage2_batch(to_enrich)

        repo = CaseRepository()
        try:
            repo.save_cases(processed)
        finally:
            repo.close()
        return n

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
                    repo.finish_scrape_event(
                        ev_id, 0, "interrupted", "Worker cancelled (Ctrl+C / shutdown)"
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
                    repo.finish_scrape_event(ev_id, 0, "error", str(e))
                finally:
                    repo.close()

                if attempt < requeue_max:
                    port_state[0] = self._bump_port(port_state[0])
                    await queue.put({"judge": judge, "attempt": attempt + 1})

            finally:
                queue.task_done()

    async def run(self, judges: List[JudgeEntry]) -> None:
        init_db(self.db_path)
        session_id = uuid.uuid4().hex[:12]  # short unique ID per run
        logger.info("Session ID: %s", session_id)

        repo = CaseRepository()
        try:
            cleaned = repo.mark_running_events_interrupted(
                reason="Recovered stale running event on startup"
            )
            if cleaned:
                logger.info("Marked %s stale running event(s) as interrupted", cleaned)
        finally:
            repo.close()

        q: asyncio.Queue = asyncio.Queue()
        for j in judges:
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
