"""
Scrape cases from kad.arbitr.ru and store in dashboard database.

End-to-end pipeline:
1. Scrape case listings via Playwright (no proxy)
2. Run through filter pipeline (keyword scoring)
3. Store in SQLite database
4. View in Streamlit dashboard

Usage:
    poetry run python scrape_to_dashboard.py
    poetry run python scrape_to_dashboard.py --judge "Солдатов Р. С." --max-cases 25
    poetry run python scrape_to_dashboard.py --headless  (run browser in background)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

from src.config.manager import ConfigManager
from src.scraper.playwright_scraper import JudgeCourtNotFoundError, PlaywrightScraper
from src.scraper.parser import parse_case_list
from src.filters.pipeline import FilterPipeline
from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.models.case import StatusEnum
from src.utils.logger import setup_logging, get_logger

DB_PATH = str(Path("data/arbitr.db").absolute())


async def scrape_and_store(
    judge_name: str = None,
    court: str = "АС города Москвы",
    max_cases: int = 25,
    headless: bool = False,
    config_path: str = "configs/main.yaml",
):
    """
    Main scraping pipeline.
    
    Args:
        judge_name: Judge name to search for, will take precedence over court
        court: Court name to search for
        max_cases: Maximum cases to scrape
        headless: Run browser in headless mode (False = visible for debugging)
        config_path: Path to config YAML
    """
    logger = get_logger(__name__)
    
    # Load config and disable proxy (expired)
    config = ConfigManager(config_path)
    config._config["scraping"]["proxy"]["enabled"] = False
    
    print("\n" + "=" * 60)
    print("⚖️  Arbitr — Scrape to Dashboard Pipeline")
    print("=" * 60)
    if judge_name:
        print(f"  Judge:     {judge_name}")
    print(f"  Court:     {court}")
    print(f"  Max cases: {max_cases}")
    print(f"  Headless:  {headless}")
    print(f"  DB:        {DB_PATH}")
    print("=" * 60)
    
    # --- Step 1: Scrape ---
    print("\n📡 Step 1: Scraping cases from kad.arbitr.ru...")
    
    scraper = PlaywrightScraper(config, headless=headless)
    
    async with scraper:
        try:
            cases = await scraper.collect_cases(court_name=court, judge_name=judge_name, max_cases=max_cases)
        except JudgeCourtNotFoundError as e:
            print(f"\n⚠️  Judge not found for target court: {e}")
            print("  - Check judge name format (Surname I. O.)")
            print("  - Judge may not sit at the configured court filter")
            return
        except Exception as e:
            print(f"\n❌ Scraping failed: {e}")
            print("\nPossible causes:")
            print("  - DDOS-Guard blocked the request (try with headless=False)")
            print("  - Network issue (check internet connection)")
            print("  - Site structure changed (parser may need updates)")
            import traceback
            traceback.print_exc()
            return
        
        if not cases:
            print("\n⚠️  No cases were scraped. Possible reasons:")
            print("  - DDOS-Guard challenge page was shown")
            print("  - Judge name not found")
            print("  - HTML structure changed")
            print("\nCheck debug_page_1.html for the raw HTML that was received.")
            return
        
        print(f"  ✅ Scraped {len(cases)} cases")
        
        # Save raw results to JSON as backup
        raw_output = Path("data/raw_cases.json")
        raw_output.parent.mkdir(parents=True, exist_ok=True)
        with open(raw_output, "w", encoding="utf-8") as f:
            data = [c.model_dump(mode="json") for c in cases]
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print(f"  💾 Raw data saved to {raw_output}")
        
        # --- Step 2: Filter ---
        print("\n🔍 Step 2: Running filter pipeline...")
        
        pipeline = FilterPipeline(config)
        processed_cases = pipeline.process_batch(cases)
        
        # Print summary
        status_counts = {}
        for c in processed_cases:
            s = c.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
        
        print("  Filter results:")
        for status, count in sorted(status_counts.items()):
            print(f"    {status}: {count}")
            
        # --- Step 2b: Stage 2 Enrichment ---
        enrich_cases = pipeline.cases_for_enrichment(processed_cases)

        if enrich_cases:
            print(f"\n🌐 Step 2b: Enriching {len(enrich_cases)} cases (Stage 2)...")
            # Perform batched page navigation to extract full participant and instance history
            try:
                await scraper.batch_enrich_cases(
                    enrich_cases, batch_size=10, judge_name=judge_name, court_name=court
                )
                pipeline.process_stage2_batch(enrich_cases)
                
                # Recalculate status counts for final reporting
                status_counts = {}
                for c in processed_cases:
                    s = c.status.value
                    status_counts[s] = status_counts.get(s, 0) + 1
                    
                print("  Final Stage 2 Filter results:")
                for status, count in sorted(status_counts.items()):
                    print(f"    {status}: {count}")
            except Exception as e:
                print(f"⚠️ Stage 2 enrichment failed: {e}")
    
    # --- Step 3: Store ---
    print("\n💾 Step 3: Storing in database...")
    
    # Clear sample data first
    init_db(DB_PATH)
    repo = CaseRepository()
    
    saved = repo.save_cases(processed_cases)
    print(f"  ✅ Saved {saved} cases to database")
    
    # --- Step 4: Stats ---
    stats = repo.get_stats()
    print("\n📊 Database Stats:")
    print(f"  Total cases:    {stats['total_cases']}")
    print(f"  By status:      {stats['by_status']}")
    print(f"  By category:    {stats['by_category']}")
    print(f"  Avg score:      {stats['avg_relevance_score']}")
    
    print("\n" + "=" * 60)
    print("✅ Pipeline complete!")
    print(f"\nView in dashboard:")
    print(f"  poetry run streamlit run dashboard/app.py")
    print("=" * 60)
    
    repo.close()


def main():
    parser = argparse.ArgumentParser(description="Scrape cases and store in dashboard DB")
    parser.add_argument("--judge", type=str, default=None, help="Judge name")
    parser.add_argument("--max-cases", type=int, default=25, help="Max cases to scrape")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--config", type=str, default="configs/main.yaml", help="Config path")
    parser.add_argument(
        "--court",
        type=str,
        default="АС города Москвы",
        help="Court name (must match target_court_filter for judge autocomplete)",
    )
    args = parser.parse_args()
    
    setup_logging(level="INFO")
    asyncio.run(scrape_and_store(
        judge_name=args.judge,
        court=args.court,
        max_cases=args.max_cases,
        headless=args.headless,
        config_path=args.config,
    ))


if __name__ == "__main__":
    main()
