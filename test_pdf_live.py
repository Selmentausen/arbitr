"""
Quick live test: warm up session via kad.arbitr.ru, open a case, download PDFs.

Usage:
    poetry run python test_pdf_live.py
    poetry run python test_pdf_live.py --case-url "https://kad.arbitr.ru/Card/c2f558eb-5c0a-4235-ba09-47cc5ceec2b7"
    poetry run python test_pdf_live.py --headless
"""

import argparse
import asyncio
import random
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

from src.scraper.parser import parse_case_card
from src.scraper.pdf_downloader import (
    PdfDownloadSummary,
    PdfEntry,
    _collect_live_pdf_hrefs,
    _download_single_pdf,
    _fmt_size,
    _is_pdf,
    collect_pdf_entries,
    classify_priority,
)
from src.models.case import Case
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)

DEFAULT_CASE_URL = "https://kad.arbitr.ru/Card/c2f558eb-5c0a-4235-ba09-47cc5ceec2b7"
DEFAULT_CASE_NUMBER = "А41-110461/2025"
BASE_URL = "https://kad.arbitr.ru"
OUT_DIR = Path("data/pdfs_test")


async def _human_delay(label: str, lo: float = 2.0, hi: float = 4.0) -> None:
    """Random pause to look human. Prints what we're waiting for."""
    secs = random.uniform(lo, hi)
    print(f"    ... waiting {secs:.1f}s ({label})")
    await asyncio.sleep(secs)


async def _warm_up_session(page: Page, case_number: str) -> None:
    """
    Navigate to kad.arbitr.ru main page, type the case number into the
    search box, click search. This establishes the DDOS-Guard tokens
    and session cookies that PDF endpoints require.
    """
    print("[1] Navigating to kad.arbitr.ru...")
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    print(f"    Page loaded: {page.url}")

    await _human_delay("page settle", 3, 5)

    print(f"[2] Searching for case {case_number}...")
    case_input = 'input[placeholder="например, А50-5568/08"]'
    await page.wait_for_selector(case_input, state="visible", timeout=10000)
    await page.click(case_input)
    await _human_delay("before typing", 1, 2)
    await page.type(case_input, case_number, delay=random.randint(70, 130))
    await _human_delay("after typing", 1.5, 3)

    search_btn = "#b-form-submit button"
    try:
        btn = await page.query_selector(search_btn)
        if btn:
            box = await btn.bounding_box()
            if box:
                await page.mouse.move(box["x"] + 10, box["y"] + 10)
                await _human_delay("hover search btn", 0.3, 0.8)
        await page.click(search_btn)
    except Exception:
        await page.keyboard.press("Enter")

    print("    Search submitted, waiting for results...")
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    await _human_delay("results settle", 3, 5)
    print(f"    Results page: {page.url}")


async def run(args: argparse.Namespace) -> None:
    case_url = args.case_url
    case_number = args.case_number
    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("PDF Download — live test")
    print(f"{'='*60}")
    print(f"  Case #:    {case_number}")
    print(f"  Case URL:  {case_url}")
    print(f"  Output:    {out_dir.resolve()}")
    print(f"  Headless:  {args.headless}")
    print(f"{'='*60}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        page = await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)

        # --- Phase 1: warm up session (DDOS-Guard + cookies) ---
        await _warm_up_session(page, case_number)

        # --- Phase 2: navigate to the case card ---
        await _human_delay("before opening case card", 2.5, 4)
        print(f"[3] Opening case card: {case_url}")
        await page.goto(case_url, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        print(f"    Page loaded: {page.url}")
        await _human_delay("case card settle", 3, 5)

        # --- Phase 3: expand chronologies ---
        print("[4] Expanding chronologies...")
        buttons = await page.query_selector_all(".b-collapse.js-collapse")
        for idx, btn in enumerate(buttons):
            try:
                await btn.click()
                await _human_delay(f"expand section {idx+1}/{len(buttons)}", 1.5, 3.0)
            except Exception:
                pass
        if buttons:
            await _human_delay("all sections settle", 2, 3.5)
        print(f"    Expanded {len(buttons)} section(s)")

        # --- Phase 4: parse case card and classify PDFs ---
        print("[5] Parsing case card and classifying PDFs...")
        html = await page.content()
        card_data = parse_case_card(html)

        # Build a temporary Case to feed into collect_pdf_entries
        temp_case = Case(
            id="test", case_number=case_number, court="test",
            plaintiff="", defendant="",
            instances=card_data.get("instances", []),
        )
        entries = collect_pdf_entries(temp_case)

        if not entries:
            # Fallback to raw DOM links (no priority info)
            live_urls = await _collect_live_pdf_hrefs(page)
            entries = [PdfEntry(url=u, priority="uncategorized") for u in live_urls]

        print(f"    Found {len(entries)} PDF(s):\n")
        for i, e in enumerate(entries, 1):
            tag = f"[{e.priority.upper():^14s}]"
            text = e.content_text or "?"
            print(f"    {i}. {tag}  {text[:70]}")
            print(f"       ...{e.url[-70:]}")

        to_download = [e for e in entries if e.priority == "high"]
        to_skip = [e for e in entries if e.priority != "high"]

        print(f"\n    → {len(to_download)} high-priority (will download)")
        print(f"    → {len(to_skip)} medium/low/uncategorized (URL only)\n")

        if not entries:
            print("    No PDF links found — nothing to test.")
            await browser.close()
            return

        # --- Phase 5: download high-priority PDFs ---
        summary = PdfDownloadSummary(urls_found=len(entries))
        summary.skipped_low_priority = len(to_skip)

        if to_download:
            print(f"[6] Downloading {len(to_download)} high-priority PDF(s) to {out_dir}/...\n")

            for i, entry in enumerate(to_download):
                if i > 0:
                    await _human_delay("between PDFs", 2, 4)

                print(f"  --- PDF {i+1}/{len(to_download)} ---")
                print(f"  [{entry.priority}] {entry.content_text or '?'}")
                print(f"  URL: ...{entry.url[-80:]}")
                try:
                    result = await _download_single_pdf(page, entry.url, out_dir, case_url)
                except Exception as e:
                    print(f"  EXCEPTION: {e}")
                    summary.failed += 1
                    continue

                if result is None:
                    print("  FAILED: no PDF bytes captured")
                    summary.failed += 1
                else:
                    path, size = result
                    summary.downloaded += 1
                    summary.bytes_downloaded += size
                    summary.saved_files.append(str(path))
                    exists = path.exists()
                    is_real = _is_pdf(path.read_bytes()[:8]) if exists else False
                    status = "OK" if (exists and is_real) else "BAD"
                    print(f"  {status}: {path.name}  {_fmt_size(size)}  disk={exists} pdf={is_real}")

                print()
        else:
            print("[6] No high-priority PDFs to download.\n")

        await browser.close()

    # --- Summary ---
    print(f"{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"  Found:      {summary.urls_found}")
    print(f"  High (downloaded): {summary.downloaded}")
    print(f"  Skipped (med/low): {summary.skipped_low_priority}")
    print(f"  Failed:     {summary.failed}")
    print(f"  Total size: {_fmt_size(summary.bytes_downloaded)}")
    if summary.saved_files:
        print(f"  Files:")
        for f in summary.saved_files:
            print(f"    {f}")
    if to_skip:
        print(f"  Recorded URLs (not downloaded):")
        for e in to_skip:
            print(f"    [{e.priority}] {e.content_text or '?'}")
    print(f"{'='*60}\n")

    if summary.failed:
        print(f"RESULT: {summary.failed} download(s) failed")
    elif summary.downloaded:
        print(f"RESULT: all {summary.downloaded} high-priority PDFs downloaded")
    else:
        print("RESULT: no high-priority PDFs on this case")


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
    args = parser.parse_args()
    setup_logging(level="DEBUG")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
