"""
Timed bandwidth test: one browser worker, real kad.arbitr.ru scrape, traffic report.

Measures bytes through Playwright (what the browser loads — proxy-billable traffic).
Also snapshots system-wide NIC counters via psutil for comparison.

Usage:
    poetry run bandwidth-test
    poetry run bandwidth-test --duration-minutes 10 --max-cases-per-judge 25
    poetry run bandwidth-test --headless --proxy
    poetry run bandwidth-test --no-enrichment   # list pages only
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import List, Optional

import psutil

from src.config.manager import ConfigManager
from src.filters.pipeline import FilterPipeline
from src.scraper.judge_loader import JudgeEntry, load_judges_from_file
from src.scraper.playwright_scraper import JudgeCourtNotFoundError, PlaywrightScraper
from src.scraper.traffic_tracker import NetworkTrafficTracker, TrafficStats
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


class TrackingScraper(PlaywrightScraper):
    """PlaywrightScraper with HTTP byte counting on the browser context."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracker = NetworkTrafficTracker()
        self.pdf_stats = TrafficStats()
        self.pdf_traffic_stats = self.pdf_stats

    async def __aenter__(self):
        await super().__aenter__()
        self.tracker.attach(self.context)
        return self


def _fmt_mb(n_bytes: int) -> str:
    return f"{n_bytes / 1024 / 1024:.2f} MB"


def _net_delta(before, after) -> tuple[int, int]:
    return after.bytes_sent - before.bytes_sent, after.bytes_recv - before.bytes_recv


def _print_progress(
    elapsed_s: float,
    duration_s: float,
    judges_done: int,
    cases_listed: int,
    cases_enriched: int,
    browser: TrafficStats,
    pdf: TrafficStats | None = None,
) -> None:
    pct = min(100.0, 100.0 * elapsed_s / duration_s) if duration_s else 0
    pdf_part = ""
    if pdf and pdf.response_bytes:
        pdf_part = f" pdf↓{_fmt_mb(pdf.response_bytes)}"
    print(
        f"  [{elapsed_s:.0f}s / {duration_s:.0f}s — {pct:.0f}%] "
        f"judges={judges_done} listed={cases_listed} enriched={cases_enriched} "
        f"browser↓{_fmt_mb(browser.response_bytes)} ↑{_fmt_mb(browser.request_bytes)}"
        f"{pdf_part}"
    )


def _print_report(
    *,
    duration_s: float,
    judge_name: str,
    cases_listed: int,
    cases_enriched: int,
    browser: TrafficStats,
    pdf: TrafficStats | None,
    nic_sent: int,
    nic_recv: int,
    headless: bool,
    proxy_enabled: bool,
    enrichment: bool,
    pdf_download: bool,
) -> None:
    pdf = pdf or TrafficStats()
    total_browser = browser.total_bytes
    total_pdf = pdf.response_bytes
    total_all = total_browser + total_pdf
    minutes = max(duration_s / 60.0, 1 / 60.0)
    print("\n" + "=" * 60)
    print("BANDWIDTH TEST RESULTS")
    print("=" * 60)
    print(f"  Duration:        {duration_s:.0f}s ({duration_s / 60:.1f} min)")
    print(f"  Judge:           {judge_name}")
    print(f"  Headless:        {headless}")
    print(f"  Proxy enabled:   {proxy_enabled}")
    print(f"  Enrichment:      {enrichment}")
    print(f"  PDF download:    {pdf_download}")
    print(f"  Cases listed:    {cases_listed}")
    print(f"  Cases enriched:  {cases_enriched}")
    print("-" * 60)
    print("  Browser traffic (use for proxy billing):")
    print(f"    Download:      {_fmt_mb(browser.response_bytes)}")
    print(f"    Upload:        {_fmt_mb(browser.request_bytes)}")
    print(f"    Total:         {_fmt_mb(total_browser)}")
    print(f"    HTTP requests: {browser.request_count} req / {browser.response_count} resp")
    print("-" * 60)
    print("  PDF traffic (intercepted real PDF bytes):")
    print(f"    Download:      {_fmt_mb(total_pdf)}")
    print(f"    Files:         {pdf.response_count}")
    if total_all:
        pct = 100.0 * total_pdf / total_all
        print(f"    Share of total:{pct:.1f}% of all download bytes")
    print("-" * 60)
    print("  Combined (browser + PDF):")
    print(f"    Download:      {_fmt_mb(browser.response_bytes + total_pdf)}")
    print(f"    Total:         {_fmt_mb(total_all)}")
    print("-" * 60)
    print("  Extrapolation (1 worker, same pace):")
    mb_per_min = (total_all / 1024 / 1024) / minutes
    gb_per_hour = mb_per_min * 60 / 1024
    gb_per_day = gb_per_hour * 24
    print(f"    Per minute:    {mb_per_min:.2f} MB/min")
    print(f"    Per hour:      {gb_per_hour:.2f} GB/hour")
    print(f"    Per day (24h): {gb_per_day:.1f} GB/day")
    if cases_enriched:
        per_case = total_all / cases_enriched
        print(f"    Per enriched case (avg): {_fmt_mb(int(per_case))}")
    if cases_listed:
        per_listed = total_all / cases_listed
        print(f"    Per listed case (avg):   {_fmt_mb(int(per_listed))}")
    print("-" * 60)
    print("  System NIC (all apps — reference only):")
    print(f"    Sent:          {_fmt_mb(nic_sent)}")
    print(f"    Received:      {_fmt_mb(nic_recv)}")
    print(f"    Total:         {_fmt_mb(nic_sent + nic_recv)}")
    print("=" * 60)
    print("\nTip: proxy plans usually bill on download (received) bytes.")
    print("     Use 'Browser traffic → Download' as the primary number.\n")


async def run_test(args: argparse.Namespace) -> None:
    config = ConfigManager(args.config)
    if args.proxy:
        config._config.setdefault("scraping", {}).setdefault("proxy", {})["enabled"] = True
    elif args.no_proxy:
        config._config.setdefault("scraping", {}).setdefault("proxy", {})["enabled"] = False

    court = args.court or config.get(
        "scraping.parallel.default_court", "АС города Москвы"
    )
    judges_path = args.judges_file or config.get(
        "scraping.judges_file", "configs/dictionaries/judges.txt"
    )
    judges = load_judges_from_file(judges_path)
    if args.judge:
        judges = [j for j in judges if j.display_name.startswith(args.judge) or j.search_name.startswith(args.judge)]
        if not judges:
            raise SystemExit(f"No judge matching {args.judge!r} in {judges_path}")

    duration_s = args.duration_minutes * 60.0
    enrich = not args.no_enrichment
    pdf_download = config.get("filtering.pdf_download_enabled", False)
    max_cases = args.max_cases

    proxy_on = bool(config.get("scraping.proxy.enabled", False))

    judge = judges[0]

    print("\n" + "=" * 60)
    print("Arbitr — Bandwidth test (1 judge, deep crawl)")
    print("=" * 60)
    print(f"  Duration:          {args.duration_minutes} min")
    print(f"  Max cases:         {max_cases}")
    print(f"  Judge:             {judge.display_name} ({judge.search_name})")
    print(f"  Court:             {court}")
    print(f"  Headless:          {args.headless}")
    print(f"  Proxy:             {proxy_on}")
    print(f"  Enrichment:        {enrich}")
    print(f"  PDF download:      {pdf_download}")
    print("=" * 60 + "\n")

    nic_before = psutil.net_io_counters()
    t0 = time.monotonic()
    deadline = t0 + duration_s

    cases_listed = 0
    cases_enriched = 0
    last_progress = t0

    async with TrackingScraper(config, headless=args.headless) as scraper:
        # Phase 1: collect many cases from one judge (goes deep into older pages)
        logger.info(
            "Collecting up to %d cases for %s...", max_cases, judge.display_name
        )
        try:
            cases = await scraper.collect_cases(
                court_name=court,
                judge_name=judge.search_name,
                max_cases=max_cases,
            )
        except JudgeCourtNotFoundError as e:
            raise SystemExit(f"Judge not found: {e}")

        cases_listed = len(cases)
        logger.info("Listed %d cases (oldest: %s)", cases_listed,
                     cases[-1].case_number if cases else "–")

        await scraper.tracker.drain()
        _print_progress(
            time.monotonic() - t0, duration_s, 1, cases_listed, 0,
            scraper.tracker.stats, scraper.pdf_stats,
        )

        # Phase 2: enrich each case one-by-one (respects time limit)
        if enrich and cases:
            pipeline = FilterPipeline(config)
            processed = pipeline.process_batch(cases)
            to_enrich = pipeline.cases_for_enrichment(processed)
            logger.info(
                "%d cases eligible for enrichment (out of %d listed)",
                len(to_enrich), cases_listed,
            )

            for i, case in enumerate(to_enrich):
                if time.monotonic() >= deadline:
                    logger.info("Time limit reached after %d enrichments", i)
                    break

                try:
                    await scraper.batch_enrich_cases(
                        [case],
                        batch_size=1,
                        judge_name=judge.search_name,
                        court_name=court,
                    )
                    cases_enriched += 1
                except Exception:
                    logger.exception("Failed to enrich %s", case.case_number)

                await scraper.tracker.drain()

                now = time.monotonic()
                if now - last_progress >= 30 or i == len(to_enrich) - 1:
                    _print_progress(
                        now - t0, duration_s, 1, cases_listed, cases_enriched,
                        scraper.tracker.stats, scraper.pdf_stats,
                    )
                    last_progress = now

            pipeline.process_stage2_batch(to_enrich[:cases_enriched])

        await scraper.tracker.drain()

    elapsed = time.monotonic() - t0
    nic_after = psutil.net_io_counters()
    nic_sent, nic_recv = _net_delta(nic_before, nic_after)

    _print_report(
        duration_s=elapsed,
        judge_name=judge.display_name,
        cases_listed=cases_listed,
        cases_enriched=cases_enriched,
        browser=scraper.tracker.stats,
        pdf=scraper.pdf_stats,
        nic_sent=nic_sent,
        nic_recv=nic_recv,
        headless=args.headless,
        proxy_enabled=proxy_on,
        enrichment=enrich,
        pdf_download=pdf_download,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Timed scrape bandwidth test (1 browser worker)"
    )
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=10.0,
        help="How long to run (default: 10)",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=200,
        help="How many cases to collect from the judge (default: 200, goes deep into older pages)",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--proxy",
        action="store_true",
        help="Force proxy on (from configs/main.yaml credentials)",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Force proxy off",
    )
    parser.add_argument(
        "--no-enrichment",
        action="store_true",
        help="List/search pages only (no case card deep scrape)",
    )
    parser.add_argument("--judge", type=str, default=None, help="Filter to one judge (prefix)")
    parser.add_argument("--court", type=str, default=None)
    parser.add_argument("--judges-file", type=str, default=None)
    parser.add_argument("--config", type=str, default="configs/main.yaml")
    args = parser.parse_args()

    if args.proxy and args.no_proxy:
        parser.error("Use only one of --proxy or --no-proxy")

    setup_logging(level="INFO")
    try:
        asyncio.run(run_test(args))
    except KeyboardInterrupt:
        print("\nStopped early (Ctrl+C). Partial stats were not printed.\n")


if __name__ == "__main__":
    main()
