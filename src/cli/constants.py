"""Shared constants for CLI entry points — single source of truth for paths."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = str(PROJECT_ROOT / "data" / "arbitr.db")
CONFIG_PATH = str(PROJECT_ROOT / "configs" / "main.yaml")
CLASSIFICATION_CONFIG_PATH = str(PROJECT_ROOT / "configs" / "classification.yaml")
PDF_DIR = PROJECT_ROOT / "data" / "pdfs"
