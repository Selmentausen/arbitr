#!/usr/bin/env python3
"""
Seed the judge queue from judges.txt.

Run this inside the orchestrator container or as a one-off:
    docker-compose exec orchestrator python scripts/seed_judges.py

Or standalone:
    DATABASE_URL=postgresql+psycopg2://... python scripts/seed_judges.py
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper.judge_loader import load_judges_from_file
from src.storage.database import get_session, JudgeProgressRecord


def main():
    judges_file = os.environ.get("JUDGES_FILE", "configs/dictionaries/judges.txt")
    judges = load_judges_from_file(judges_file)

    session = get_session()
    try:
        added = 0
        skipped = 0

        for entry in judges:
            existing = (
                session.query(JudgeProgressRecord)
                .filter_by(judge_name=entry.search_name)
                .first()
            )
            if existing:
                skipped += 1
                continue

            session.add(
                JudgeProgressRecord(judge_name=entry.search_name, status="pending")
            )
            added += 1

        session.commit()
        print(f"Seeded {added} judges ({skipped} already existed)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
