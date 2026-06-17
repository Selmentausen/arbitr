"""
Export ML classified cases to a clean text format with optional date filtering.

Usage:
    poetry run export-ml
    poetry run export-ml --date 2026-06-10
    poetry run export-ml --since 2026-06-09 --until 2026-06-10T18:00:00
"""

import argparse
import sys
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from src.cli.constants import DB_PATH
from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.utils.logger import setup_logging, get_logger

logger = get_logger(__name__)


def parse_date_or_datetime(s: str) -> datetime:
    """Parse a date (YYYY-MM-DD) or datetime into a timezone-aware datetime."""
    # Try parsing full ISO datetime
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Assume local timezone if naive
            dt = dt.astimezone()
        return dt
    except ValueError:
        pass

    # Try parsing date only YYYY-MM-DD
    try:
        d = date.fromisoformat(s)
        # Convert to datetime at start of day in local timezone
        dt = datetime(d.year, d.month, d.day)
        return dt.astimezone()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Not a valid date or datetime: '{s}'. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export ML-classified cases to a text file with date filters")
    parser.add_argument(
        "--output",
        type=str,
        default="data/ml_export.txt",
        help="Path to the output text file (default: data/ml_export.txt)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=999999,
        help="Max number of cases to export (default: all)",
    )
    parser.add_argument(
        "--date",
        type=parse_date_or_datetime,
        default=None,
        help="Filter for cases classified on this specific day (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--since",
        type=parse_date_or_datetime,
        default=None,
        help="Filter for cases classified after this datetime (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)",
    )
    parser.add_argument(
        "--until",
        type=parse_date_or_datetime,
        default=None,
        help="Filter for cases classified before this datetime (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)",
    )
    args = parser.parse_args()

    setup_logging()
    init_db(DB_PATH)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Establish query boundaries if --date is provided
    start_dt = None
    end_dt = None
    if args.date:
        start_dt = args.date
        end_dt = start_dt + timedelta(days=1) - timedelta(microseconds=1)

    repo = CaseRepository()
    try:
        logger.info("Fetching ML-classified cases from DB...")
        # Fetch all cases (we filter them in memory to support precise local timezone comparisons)
        raw_cases, _ = repo.get_ml_cases(
            page=1,
            page_size=999999,
            sort_by="ml_analyzed_at",
            sort_desc=True,
        )

        filtered_cases = []
        for case in raw_cases:
            ml = case.extracted_data.get("ml_classification") or {}
            analyzed_str = ml.get("analyzed_at")
            if not analyzed_str:
                continue

            try:
                analyzed_dt = datetime.fromisoformat(analyzed_str.replace("Z", "+00:00"))
                if analyzed_dt.tzinfo is None:
                    analyzed_dt = analyzed_dt.replace(tzinfo=timezone.utc)
            except Exception:
                logger.warning("Could not parse analyzed_at date for case %s: %s", case.case_number, analyzed_str)
                continue

            # Apply filters
            if start_dt and end_dt:
                # Compare in the local timezone of start_dt
                local_analyzed_dt = analyzed_dt.astimezone(start_dt.tzinfo)
                if not (start_dt <= local_analyzed_dt <= end_dt):
                    continue

            if args.since:
                if analyzed_dt < args.since.astimezone(timezone.utc):
                    continue

            if args.until:
                if analyzed_dt > args.until.astimezone(timezone.utc):
                    continue

            filtered_cases.append(case)

        # Apply limit after filtering
        filtered_cases = filtered_cases[:args.limit]

        if not filtered_cases:
            logger.info("No ML-classified cases matched the date filters.")
            print("\nNo classified cases matched the specified date filters.\n")
            return 0

        logger.info("Writing %d cases to %s...", len(filtered_cases), output_path)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"ML CLASSIFIED CASES EXPORT\n")
            f.write(f"Total Exported: {len(filtered_cases)}\n")
            if args.date:
                f.write(f"Date Filter:    {args.date.date().isoformat()}\n")
            if args.since:
                f.write(f"Since Filter:   {args.since.isoformat()}\n")
            if args.until:
                f.write(f"Until Filter:   {args.until.isoformat()}\n")
            f.write("=" * 80 + "\n\n")

            for idx, case in enumerate(filtered_cases, 1):
                ml = case.extracted_data.get("ml_classification") or {}
                category = ml.get("primary_category", "unknown")
                confidence = ml.get("confidence", 0.0)
                reasoning = ml.get("reasoning", "No reasoning provided.")
                analyzed_at = ml.get("analyzed_at", "unknown")

                f.write(f"{idx}. CASE: {case.case_number}\n")
                f.write(f"   Link:          {case.case_url or 'No URL'}\n")
                f.write(f"   Category:      {category} (confidence: {confidence * 100:.1f}%)\n")
                f.write(f"   Classified At: {analyzed_at}\n")
                f.write(f"   Reasoning:\n")

                # Format reasoning lines nicely
                for line in reasoning.strip().split("\n"):
                    f.write(f"     {line}\n")

                f.write("\n" + "-" * 80 + "\n\n")

        print(f"\nSuccessfully exported {len(filtered_cases)} cases to: {output_path.absolute()}\n")
        return 0

    except Exception as e:
        logger.exception("Failed to export ML-classified cases: %s", e)
        return 1
    finally:
        repo.close()


if __name__ == "__main__":
    sys.exit(main())
