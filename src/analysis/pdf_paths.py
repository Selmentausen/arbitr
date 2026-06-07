"""Resolve local PDF paths from kad.arbitr.ru URLs."""

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from src.scraper.pdf_downloader import _safe_filename as _safe_filename_from_url_impl


def safe_filename_from_url(url: str) -> str:
    """Derive the on-disk PDF stem from a document URL."""
    return _safe_filename_from_url_impl(url)


def find_local_pdf(url: str, pdf_dir: Path) -> Path | None:
    """Find a downloaded PDF on disk by matching the URL to the expected filename."""
    if not url or not pdf_dir.exists():
        return None
    safe = safe_filename_from_url(url)
    if not safe:
        return None
    candidate = pdf_dir / f"{safe}.pdf"
    if candidate.exists():
        return candidate
    return None
