"""
Run parallel scrape over configs/dictionaries/judges.txt (or custom file).

Each worker uses a dedicated residential proxy port when proxy.enabled is true.

Usage:
    poetry run python scrape_parallel.py
    poetry run python scrape_parallel.py --workers 5 --max-cases-per-judge 50
    poetry run python scrape_parallel.py --judges-file configs/dictionaries/judges.txt --headless
"""

import argparse
import asyncio
from pathlib import Path

from src.config.manager import ConfigManager
from src.scraper.judge_loader import load_judges_from_file
from src.scraper.runner import ParallelScrapeRunner
from src.utils.logger import get_logger, setup_logging

DB_PATH = str(Path("data/arbitr.db").absolute())


async def _amain(args: argparse.Namespace) -> None:
    logger = get_logger(__name__)
    config = ConfigManager(args.config)

    workers = args.workers
    if workers is None:
        workers = config.get("scraping.parallel.workers")
        if workers is None:
            workers = config.get("scraping.max_concurrent", 3)

    max_cases = args.max_cases_per_judge
    if max_cases is None:
        max_cases = config.get("scraping.parallel.max_cases_per_judge", 100)

    judges_path = args.judges_file or config.get(
        "scraping.judges_file", "configs/dictionaries/judges.txt"
    )
    court = args.court or config.get(
        "scraping.parallel.default_court", "АС города Москвы"
    )

    judges = load_judges_from_file(judges_path)
    logger.info(
        "Parallel scrape: %s judges, %s workers, court=%r, max_cases=%s",
        len(judges),
        workers,
        court,
        max_cases,
    )

    runner = ParallelScrapeRunner(
        config=config,
        num_workers=int(workers),
        court=court,
        max_cases_per_judge=int(max_cases),
        headless=args.headless,
        config_path=args.config,
        db_path=DB_PATH,
    )
    await runner.run(judges)
    logger.info("Parallel scrape finished.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parallel scrape all judges to dashboard DB")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel browser workers (default: scraping.parallel.workers or max_concurrent)",
    )
    parser.add_argument(
        "--max-cases-per-judge",
        type=int,
        default=None,
        help="Cap listings per judge (default: scraping.parallel.max_cases_per_judge)",
    )
    parser.add_argument(
        "--judges-file",
        type=str,
        default=None,
        help="Path to judges.txt",
    )
    parser.add_argument(
        "--court",
        type=str,
        default=None,
        help='Court display name (default: АС города Москвы)',
    )
    parser.add_argument("--headless", action="store_true", help="Headless browser")
    parser.add_argument("--config", type=str, default="configs/main.yaml")
    args = parser.parse_args()

    setup_logging(level="INFO")
    logger = get_logger(__name__)
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        logger.warning("Stopped by user (Ctrl+C).")


if __name__ == "__main__":
    main()
