"""
Quick live test: warm up session via kad.arbitr.ru, open a case, download PDFs.

Useful for debugging PDF download issues — establishes DDOS-Guard session
and tests the full download pipeline against a real case.

Usage:
    poetry run test-pdf
    poetry run test-pdf --case-url "https://kad.arbitr.ru/Card/c2f558eb-5c0a-4235-ba09-47cc5ceec2b7"
    poetry run test-pdf --headless
"""

import argparse
import asyncio
import random
from pathlib import Path

from src.scraper.parser import parse_case_card
from src.scraper.pdf_downloader import (
    download_pdfs_for_case,
    _fmt_size,
)
from src.models.case import Case
from src.utils.logger import get_logger, setup_logging

# For identical warmup, proxy, optimization, and traffic tracking
from src.scraper.playwright_scraper import PlaywrightScraper
from src.scraper.traffic_tracker import NetworkTrafficTracker, TrafficStats
from src.config.manager import ConfigManager
from src.cli.constants import CONFIG_PATH

logger = get_logger(__name__)

DEFAULT_CASE_URL = "https://kad.arbitr.ru/Card/c2f558eb-5c0a-4235-ba09-47cc5ceec2b7"
DEFAULT_CASE_NUMBER = "А41-110461/2025"
OUT_DIR = Path("data/pdfs_test")


class TrackingScraper(PlaywrightScraper):
    """PlaywrightScraper with HTTP byte counting on the browser context."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracker = NetworkTrafficTracker()
        self.pdf_stats = TrafficStats()

    async def __aenter__(self):
        await super().__aenter__()
        self.tracker.attach(self.context)
        return self


async def run(args: argparse.Namespace) -> None:
    case_url = args.case_url
    case_number = args.case_number
    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    config = ConfigManager(args.config)
    if args.no_stealth:
        config._config.setdefault("scraping", {})["stealth_enabled"] = False

    print(f"\n{'='*60}")
    print("PDF Download — live test")
    print(f"{'='*60}")
    print(f"  Case #:        {case_number}")
    print(f"  Case URL:      {case_url}")
    print(f"  Output:        {out_dir.resolve()}")
    print(f"  Headless:      {args.headless}")
    print(f"  Stealth:       {not args.no_stealth}")
    print(f"  Proxy:         {config.get('scraping.proxy.enabled', False)}")
    print(f"  Bandwidth Opt: {config.get('scraping.bandwidth_optimization.enabled', False)}")
    print(f"{'='*60}\n")

    async with TrackingScraper(config, headless=args.headless) as scraper:
        # --- Phase 1: identical session warmup ---
        print("[1] Warming up session (using identical scraper autocomplete search)...")
        court = args.court or config.get("scraping.parallel.default_court", "АС города Москвы")
        judge_name = args.judge or "Титова Е. В."
        
        await scraper._init_session(scraper.page, court, judge_name)
        await scraper.tracker.drain()
        warmup_bytes = scraper.tracker.stats.total_bytes
        print(f"    -> Warmup completed. Traffic used: {_fmt_size(warmup_bytes)}")

        # --- Phase 2: navigate to the case card ---
        print(f"\n[2] Opening case card: {case_url}")
        await scraper._delay("before_case_page")
        await scraper.page.goto(case_url, wait_until="domcontentloaded", timeout=60000)
        try:
            await scraper.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        print(f"    Page loaded: {scraper.page.url}")
        
        await scraper.tracker.drain()
        card_nav_bytes = scraper.tracker.stats.total_bytes - warmup_bytes
        print(f"    -> Card page loaded. Traffic used: {_fmt_size(card_nav_bytes)}")

        # --- Phase 3: expand chronologies ---
        print("\n[3] Expanding chronologies...")
        try:
            await scraper.page.wait_for_selector(".b-chrono-item", timeout=10000)
        except Exception:
            pass

        buttons = await scraper.page.query_selector_all(".b-collapse.js-collapse")
        expanded_count = 0
        for idx, btn in enumerate(buttons):
            try:
                min_s, max_s = scraper.delays.get("between_instance_expand", (1.0, 2.2))
                await asyncio.sleep(random.uniform(min_s, max_s))
                await btn.evaluate("node => node.click()")
                expanded_count += 1
            except Exception:
                pass
        
        min_s, max_s = scraper.delays.get("after_all_expanded", (1.2, 2.0))
        await asyncio.sleep(random.uniform(min_s, max_s))
        
        await scraper.tracker.drain()
        expansion_bytes = scraper.tracker.stats.total_bytes - (warmup_bytes + card_nav_bytes)
        print(f"    Expanded {expanded_count} section(s). Traffic used: {_fmt_size(expansion_bytes)}")

        # --- Phase 4: parse case card and download PDFs ---
        print("\n[4] Downloading PDFs via download_pdfs_for_case...")
        html = await scraper.page.content()
        card_data = parse_case_card(html)

        # Build a temporary Case to feed into download_pdfs_for_case
        temp_case = Case(
            id="test", case_number=case_number, court="test",
            plaintiff="", defendant="",
            instances=card_data.get("instances", []),
        )

        summary = await download_pdfs_for_case(
            scraper.page,
            temp_case,
            scraper.base_url,
            storage_dir=out_dir,
            stats=scraper.pdf_stats,
        )

        await scraper.tracker.drain()
        total_traffic = scraper.tracker.stats.total_bytes + scraper.pdf_stats.response_bytes
        pdf_traffic = scraper.pdf_stats.response_bytes

        # --- Summary ---
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")
        print(f"  Found:      {summary.urls_found}")
        print(f"  Downloaded: {summary.downloaded}")
        print(f"  Skipped:    {summary.skipped_low_priority}")
        print(f"  Failed:     {summary.failed}")
        print(f"  Total size: {_fmt_size(summary.bytes_downloaded)}")
        if summary.saved_files:
            print(f"  Files:")
            for f in summary.saved_files:
                print(f"    {f}")
        if summary.recorded_urls:
            print(f"  Recorded URLs (not downloaded):")
            for e in summary.recorded_urls:
                print(f"    [{e.get('priority')}] {e.get('content') or '?'}")
        print(f"{'='*60}\n")

        print(f"{'='*60}")
        print("TRAFFIC BREAKDOWN SUMMARY")
        print(f"{'='*60}")
        print(f"  Warmup session:      {_fmt_size(warmup_bytes)}")
        print(f"  Case card load:      {_fmt_size(card_nav_bytes)}")
        print(f"  Chronology expand:   {_fmt_size(expansion_bytes)}")
        print(f"  PDF downloads:       {_fmt_size(pdf_traffic)}")
        print(f"  Other/API traffic:   {_fmt_size(total_traffic - (warmup_bytes + card_nav_bytes + expansion_bytes + pdf_traffic))}")
        print("-" * 60)
        print(f"  Total Traffic used:  {_fmt_size(total_traffic)}")
        print(f"{'='*60}\n")

        # Save CSV report and print the breakdown
        csv_path = Path("data/traffic_log.csv")
        scraper.tracker.save_csv_report(csv_path)
        print(f"Detailed traffic log saved to: {csv_path.absolute()}")
        print("\n" + scraper.tracker.get_summary_tables() + "\n")

        if summary.failed:
            print(f"RESULT: {summary.failed} download(s) failed")
        elif summary.downloaded:
            print(f"RESULT: {summary.downloaded} eligible PDFs downloaded")
        else:
            print("RESULT: no PDFs downloaded")


def main():
    parser = argparse.ArgumentParser(description="Quick PDF download test")
    parser.add_argument(
        "--case-url", default=DEFAULT_CASE_URL,
        help="Full case card URL (default: A41-110461/2025)",
    )
    parser.add_argument(
        "--case-number", default=DEFAULT_CASE_NUMBER,
        help="Case number to search for (warms up the session)",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--no-stealth",
        action="store_true",
        help="Disable playwright-stealth (fixes gray canvas captchas)",
    )
    parser.add_argument(
        "--judge", default="Титова Е. В.",
        help="Judge name to use for session warmup autocomplete",
    )
    parser.add_argument(
        "--court", default="АС города Москвы",
        help="Court name to use for session warmup autocomplete",
    )
    parser.add_argument(
        "--config", default=CONFIG_PATH,
        help="Path to system config file",
    )
    args = parser.parse_args()
    setup_logging(level="INFO")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
