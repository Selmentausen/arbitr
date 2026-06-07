"""Extract text from downloaded PDF files."""

from pathlib import Path
from typing import Optional

import fitz

from src.analysis.pdf_paths import find_local_pdf
from src.config.classification import ClassificationConfig
from src.models.case import Case, CaseDocument
from src.utils.logger import get_logger

logger = get_logger(__name__)

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2, "uncategorized": 3}


def extract_text_from_pdf(path: Path, max_chars: Optional[int] = None) -> str:
    """Extract plain text from a PDF file using PyMuPDF."""
    text_parts: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            page_text = page.get_text("text").strip()
            if page_text:
                text_parts.append(page_text)
    text = "\n".join(text_parts).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n[... обрезано ...]"
    return text


def _collect_pdf_entries(case: Case) -> list[tuple[str, str, str, Optional[str]]]:
    """Return (url, label, priority, existing_text) sorted by priority."""
    entries: list[tuple[str, str, str, Optional[str]]] = []
    seen_urls: set[str] = set()

    for inst in case.instances:
        for doc in inst.documents or []:
            if doc.url and doc.url not in seen_urls:
                seen_urls.add(doc.url)
                entries.append(
                    (
                        doc.url,
                        doc.filename or doc.type or "Документ",
                        doc.priority or "uncategorized",
                        doc.extracted_text,
                    )
                )
        for upd in inst.updates or []:
            if upd.pdf_url and upd.pdf_url not in seen_urls:
                seen_urls.add(upd.pdf_url)
                prio = "uncategorized"
                for d in inst.documents or []:
                    if d.url == upd.pdf_url and d.priority:
                        prio = d.priority
                        break
                entries.append(
                    (
                        upd.pdf_url,
                        upd.content or upd.update_type or "Документ",
                        prio,
                        None,
                    )
                )
        if inst.result_pdf_url and inst.result_pdf_url not in seen_urls:
            seen_urls.add(inst.result_pdf_url)
            entries.append(
                (
                    inst.result_pdf_url,
                    inst.result_text or "Результат инстанции",
                    "high",
                    None,
                )
            )

    entries.sort(key=lambda e: _PRIORITY_RANK.get(e[2], 99))
    return entries


def enrich_case_with_pdf_text(
    case: Case,
    pdf_dir: Path,
    config: ClassificationConfig,
    skip_pdf: bool = False,
) -> Case:
    """
    Populate CaseDocument.extracted_text and case.pdf_texts from local PDFs.

    Skips files already extracted. Returns the same case object (mutated).
    """
    if skip_pdf:
        return case

    max_chars = int(config.get("limits.max_pdf_chars", 6000))
    pdf_texts: list[str] = list(case.pdf_texts or [])
    existing_count = len(pdf_texts)

    for url, label, _priority, existing in _collect_pdf_entries(case):
        if existing:
            continue
        local = find_local_pdf(url, pdf_dir)
        if not local:
            continue
        try:
            text = extract_text_from_pdf(local, max_chars=max_chars)
        except Exception as e:
            logger.warning("PDF extract failed %s: %s", local.name, e)
            continue
        if not text:
            continue

        header = f"=== PDF: {label} ===\n{text}"
        pdf_texts.append(header)

        for inst in case.instances:
            for doc in inst.documents or []:
                if doc.url == url:
                    doc.extracted_text = text

    if len(pdf_texts) > existing_count:
        case.pdf_texts = pdf_texts
        logger.debug(
            "Extracted PDF text for case %s (%d docs)",
            case.case_number,
            len(pdf_texts) - existing_count,
        )
    return case
