"""Shared constants, helpers, and UI components used across dashboard pages."""

import os
import sys
import subprocess
from pathlib import Path

import streamlit as st

from src.analysis.pdf_paths import find_local_pdf

# --- Paths ---
PROJECT_ROOT = Path(__file__).parent.parent
PDF_DIR = PROJECT_ROOT / "data" / "pdfs"
CLASSIFICATION_CONFIG_PATH = PROJECT_ROOT / "configs" / "classification.yaml"

# --- Status constants ---

STATUS_COLORS = {
    "high_relevant": "#22c55e",
    "uncertain": "#f59e0b",
    "insufficient_info": "#6b7280",
    "reject": "#ef4444",
}

STATUS_LABELS = {
    "high_relevant": "✅ Высокая релевантность",
    "uncertain": "⚠️ Неопределенный",
    "insufficient_info": "ℹ️ Недостаточно информации",
    "reject": "❌ Отклонён",
}

ML_CATEGORY_LABELS = {
    "construction": "Строительство",
    "intellectual_property": "Интеллектуальная собственность",
    "other": "Другое",
}

PRIORITY_BADGES = {"high": "🔴", "medium": "🟡", "low": "🟢", "uncategorized": "⚪"}
PRIORITY_TIPS = {
    "high": "Высокий — PDF скачан",
    "medium": "Средний — только ссылка",
    "low": "Низкий — только ссылка",
    "uncategorized": "Без категории",
}


# --- PDF helpers ---


def find_local_pdf_cached(url: str) -> Path | None:
    """Find a downloaded PDF on disk by matching the URL to the expected filename."""
    return find_local_pdf(url, PDF_DIR)


def collect_case_pdfs(case) -> list[dict]:
    """Unique PDF documents from instance documents (no chronology)."""
    seen: set[str] = set()
    pdfs: list[dict] = []
    for inst in case.instances or []:
        for doc in inst.documents or []:
            if not doc.url or doc.url in seen:
                continue
            seen.add(doc.url)
            pdfs.append({
                "url": doc.url,
                "label": doc.filename or doc.type or "PDF",
                "priority": doc.priority or "uncategorized",
                "downloaded": find_local_pdf_cached(doc.url) is not None,
            })
    return pdfs


# --- Shared render functions ---

_pdf_link_counter = 0


def render_pdf_link(url: str, label: str, case_id: str, priority: str | None = None):
    """Render a single PDF entry: priority badge + local download or remote link."""
    global _pdf_link_counter
    _pdf_link_counter += 1
    uid = _pdf_link_counter

    prio = priority or "uncategorized"
    badge = PRIORITY_BADGES.get(prio, "⚪")
    tip = PRIORITY_TIPS.get(prio, "")
    full_url = url if url.startswith("http") else f"https://kad.arbitr.ru{url}"

    local = find_local_pdf_cached(url)
    if local:
        c1, c2 = st.columns([5, 1.5])
        with c1:
            st.markdown(f"{badge} **{label}**", help=tip)
        with c2:
            if st.button("📄 Открыть PDF", key=f"pdf_{uid}"):
                abs_path = str(local.resolve())
                if sys.platform == "win32":
                    os.startfile(abs_path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", abs_path])
                else:
                    subprocess.Popen(["xdg-open", abs_path])
    else:
        st.markdown(
            f"  {badge} [{label}]({full_url})",
            help=tip,
        )
