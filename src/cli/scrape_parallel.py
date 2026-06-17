"""
Run parallel scrape over configs/dictionaries/judges.txt (or custom file).

Each worker uses a dedicated residential proxy port when proxy.enabled is true.

Usage:
    poetry run scrape-parallel
    poetry run scrape-parallel --workers 5 --max-cases-per-judge 50
    poetry run scrape-parallel --judges-file configs/dictionaries/judges.txt --headless
"""

import argparse
import asyncio

from src.cli.constants import DB_PATH, CONFIG_PATH
from src.config.manager import ConfigManager
from src.scraper.judge_loader import load_judges_from_file
from src.scraper.runner import ParallelScrapeRunner
from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.utils.logger import get_logger, setup_logging


async def _amain(args: argparse.Namespace) -> None:
    logger = get_logger(__name__)
    config = ConfigManager(args.config)

    init_db(DB_PATH)

    if args.reset_progress:
        repo = CaseRepository()
        try:
            cleared = repo.reset_judge_progress()
            logger.info("Reset judge progress: cleared %d entries", cleared)
        finally:
            repo.close()

    if args.reset_judge:
        repo = CaseRepository()
        try:
            cleared = repo.reset_judge_progress(judge_name=args.reset_judge)
            if cleared:
                logger.info("Reset progress for judge: %s", args.reset_judge)
            else:
                logger.warning("No progress found for judge: %s", args.reset_judge)
        finally:
            repo.close()

    if args.show_progress:
        repo = CaseRepository()
        try:
            all_prog = repo.get_all_judge_progress()
            if not all_prog:
                print("No judge progress recorded yet.")
            else:
                print(f"\n{'Judge':<40} {'Status':<12} {'Collected':>10} {'Max':>8} {'Site Total':>11} {'Error'}")
                print("-" * 110)
                for p in all_prog:
                    err = (p.error_message or "")[:40]
                    print(f"{p.judge_name:<40} {p.status:<12} {p.cases_collected:>10} {p.max_cases:>8} {p.total_count_at_start:>11} {err}")
                print(f"\nTotal: {len(all_prog)} judges")
        finally:
            repo.close()
        return

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

    if args.judge:
        from src.scraper.judge_loader import JudgeEntry
        parts = args.judge.strip().split()
        if len(parts) >= 2:
            search_name = f"{parts[0]} {' '.join(p[0] + '.' for p in parts[1:] if p)}"
        else:
            search_name = args.judge
        judges = [JudgeEntry(display_name=args.judge, search_name=search_name, full_fallback=args.judge)]
        logger.info("Single-judge mode: %s (search: %s)", args.judge, search_name)
    else:
        judges = load_judges_from_file(judges_path)

    logger.info(
        "Parallel scrape: %s judges, %s workers, court=%r, max_cases=%s, skip_new=%s",
        len(judges),
        workers,
        court,
        max_cases,
        args.skip_new,
    )

    runner = ParallelScrapeRunner(
        config=config,
        num_workers=int(workers),
        court=court,
        max_cases_per_judge=int(max_cases),
        headless=args.headless,
        config_path=args.config,
        db_path=DB_PATH,
        skip_new=args.skip_new,
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
    parser.add_argument("--config", type=str, default=CONFIG_PATH)
    parser.add_argument(
        "--reset-progress",
        action="store_true",
        help="Clear all judge progress and start fresh",
    )
    parser.add_argument(
        "--skip-new",
        action="store_true",
        help="On resume, skip newly added cases and only continue deeper",
    )
    parser.add_argument(
        "--judge",
        type=str,
        default=None,
        help='Single judge full name to scrape (e.g. "Титова Елена Владимировна")',
    )
    parser.add_argument(
        "--reset-judge",
        type=str,
        default=None,
        help='Reset progress for a specific judge (full name)',
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Print current judge progress and exit",
    )
    args = parser.parse_args()

    setup_logging(level="INFO")
    logger = get_logger(__name__)
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        logger.warning("Stopped by user (Ctrl+C).")


if __name__ == "__main__":
    main()
