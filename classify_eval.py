"""
Evaluate ML classifier against a golden labeled set.

Usage:
    poetry run classify-eval
    poetry run classify-eval --config configs/classification_eval.yaml
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from src.analysis.classifier import apply_classification_to_case, classify_case
from src.analysis.ollama_client import OllamaError, create_ollama_client
from src.config.classification import ClassificationConfig
from src.config.manager import ConfigManager
from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.utils.logger import setup_logging, get_logger

DB_PATH = str(Path("data/arbitr.db").absolute())
logger = get_logger(__name__)


def _compute_metrics(y_true: list[str], y_pred: list[str], labels: list[str]) -> dict:
    """Per-label precision/recall and overall accuracy."""
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)

    for label in labels:
        for t, p in zip(y_true, y_pred):
            if p == label and t == label:
                tp[label] += 1
            elif p == label and t != label:
                fp[label] += 1
            elif t == label and p != label:
                fn[label] += 1

    per_label = {}
    for label in labels:
        prec = tp[label] / (tp[label] + fp[label]) if (tp[label] + fp[label]) else 0.0
        rec = tp[label] / (tp[label] + fn[label]) if (tp[label] + fn[label]) else 0.0
        per_label[label] = {"precision": prec, "recall": rec, "support": tp[label] + fn[label]}

    return {
        "accuracy": correct / len(y_true) if y_true else 0.0,
        "per_label": per_label,
        "total": len(y_true),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate classifier on golden set")
    parser.add_argument(
        "--eval-config",
        default="configs/classification_eval.yaml",
        help="Golden set YAML path",
    )
    parser.add_argument(
        "--classification-config",
        default="configs/classification.yaml",
        help="Classification config path",
    )
    parser.add_argument("--fast", action="store_true", help="Use fast model")
    parser.add_argument("--skip-pdf", action="store_true", help="Skip PDF extraction")
    args = parser.parse_args()

    setup_logging()
    init_db(DB_PATH)

    eval_path = Path(args.eval_config)
    if not eval_path.exists():
        logger.error("Eval config not found: %s", eval_path)
        return 1

    with open(eval_path, encoding="utf-8") as f:
        eval_data = yaml.safe_load(f) or {}

    golden = eval_data.get("cases") or []
    if not golden:
        logger.error(
            "No cases in %s. Add case IDs with expected labels as you review in dashboard.",
            eval_path,
        )
        return 1

    clf_config = ClassificationConfig(args.classification_config)
    main_config = ConfigManager()
    pdf_dir = Path(main_config.get("scraping.pdf_storage_dir", "data/pdfs"))

    client = create_ollama_client(clf_config, use_fast=args.fast)
    if not client.ping():
        logger.error("Ollama not reachable")
        return 1

    repo = CaseRepository()
    labels = clf_config.category_ids
    y_true: list[str] = []
    y_pred: list[str] = []
    failures: list[str] = []

    try:
        for entry in golden:
            case_id = entry["id"]
            expected = entry["expected"]
            case = repo.get_case(case_id)
            if case is None:
                logger.warning("Case not found: %s", case_id)
                failures.append(case_id)
                continue

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
                y_true.append(expected)
                y_pred.append(result.primary_category)
                logger.info(
                    "%s: expected=%s predicted=%s (%.0f%%)",
                    case.case_number,
                    expected,
                    result.primary_category,
                    result.confidence * 100,
                )
            except OllamaError as e:
                logger.error("Ollama error for %s: %s", case_id, e)
                failures.append(case_id)

        if not y_true:
            logger.error("No evaluations completed.")
            return 1

        metrics = _compute_metrics(y_true, y_pred, labels)
        print("\n" + "=" * 50)
        print(f"Accuracy: {metrics['accuracy']:.1%} ({len(y_true)} cases)")
        print("=" * 50)
        for label in labels:
            m = metrics["per_label"][label]
            print(
                f"  {label:14s}  P={m['precision']:.1%}  R={m['recall']:.1%}  n={m['support']}"
            )
        if failures:
            print(f"\nFailures/missing: {len(failures)}")
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    sys.exit(main())
