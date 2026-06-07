"""
Batch ML classification of court cases via local Ollama.

Usage:
    poetry run classify
    poetry run classify --limit 50
    poetry run classify --case-id <uuid>
    poetry run classify --force
    poetry run classify --skip-pdf
    poetry run classify --dry-run
    poetry run classify --fast
"""

import argparse
import sys
import time
from pathlib import Path

from src.analysis.classifier import apply_classification_to_case, classify_case
from src.analysis.ollama_client import OllamaError, create_ollama_client
from src.config.classification import ClassificationConfig
from src.config.manager import ConfigManager
from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.utils.logger import setup_logging, get_logger

DB_PATH = str(Path("data/arbitr.db").absolute())
logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify cases by legal area using Ollama")
    parser.add_argument("--limit", type=int, default=100, help="Max cases to classify")
    parser.add_argument("--case-id", type=str, default=None, help="Classify a single case by ID")
    parser.add_argument("--force", action="store_true", help="Re-classify even if already done")
    parser.add_argument("--skip-pdf", action="store_true", help="Skip PDF text extraction")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt only, no Ollama call")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Use fast_model (qwen2.5:7b) instead of primary model",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="configs/classification.yaml",
        help="Path to classification config",
    )
    args = parser.parse_args()

    setup_logging()
    init_db(DB_PATH)

    try:
        clf_config = ClassificationConfig(args.config)
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1

    main_config = ConfigManager()
    pdf_dir = Path(main_config.get("scraping.pdf_storage_dir", "data/pdfs"))

    if not args.dry_run:
        client = create_ollama_client(clf_config, use_fast=args.fast)
        if not client.ping():
            logger.error(
                "Ollama is not reachable at %s. Start Ollama and ensure model is pulled.",
                clf_config.get("ollama.base_url"),
            )
            return 1
        logger.info("Using model: %s (prompt v%s)", client.model, clf_config.prompt_version)

    repo = CaseRepository()
    try:
        cases = repo.list_cases_for_classification(
            limit=args.limit,
            force=args.force,
            case_id=args.case_id,
        )
        if not cases:
            logger.info("No cases to classify.")
            return 0

        logger.info("Classifying %d case(s)...", len(cases))

        if args.dry_run and cases:
            case = cases[0]
            _, _, prompt = classify_case(
                case,
                clf_config,
                pdf_dir,
                skip_pdf=args.skip_pdf,
                dry_run=True,
            )
            # Windows console may not support UTF-8; reconfigure if possible
            if hasattr(sys.stdout, "reconfigure"):
                try:
                    sys.stdout.reconfigure(encoding="utf-8")
                except Exception:
                    pass
            print("=" * 60)
            print("DRY RUN — SYSTEM PROMPT")
            print("=" * 60)
            print(prompt.system)
            print("\n" + "=" * 60)
            print("DRY RUN — USER PROMPT")
            print("=" * 60)
            print(prompt.user)
            return 0

        client = create_ollama_client(clf_config, use_fast=args.fast)
        ok = 0
        failed = 0
        by_category: dict[str, int] = {}
        latencies: list[float] = []

        for case in cases:
            t0 = time.perf_counter()
            try:
                updated_case, result, _ = classify_case(
                    case,
                    clf_config,
                    pdf_dir,
                    skip_pdf=args.skip_pdf,
                    use_fast=args.fast,
                    client=client,
                )
                if result is None:
                    continue
                updated_case = apply_classification_to_case(updated_case, result, clf_config, prompt)
                repo.save_case(updated_case)
                ok += 1
                by_category[result.primary_category] = by_category.get(result.primary_category, 0) + 1
                latencies.append(time.perf_counter() - t0)
            except OllamaError as e:
                logger.error("Ollama error for %s: %s", case.case_number, e)
                failed += 1
            except Exception as e:
                logger.error("Failed to classify %s: %s", case.case_number, e)
                failed += 1

        avg_lat = sum(latencies) / len(latencies) if latencies else 0
        logger.info("Done: %d classified, %d failed, avg %.1fs/case", ok, failed, avg_lat)
        for cat, count in sorted(by_category.items()):
            logger.info("  %s: %d", cat, count)

        return 0 if failed == 0 else 1
    finally:
        repo.close()


if __name__ == "__main__":
    sys.exit(main())
