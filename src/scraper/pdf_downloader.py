"""
Download PDFs from kad.arbitr.ru case cards via response listener interception.

kad.arbitr.ru serves PDFs behind DDOS-Guard. The browser loads them through
a redirect chain (GIF → 301 → challenge → real PDF). We capture the real
PDF bytes via a context.on("response") listener.

Priority filtering: only high-priority documents (rulings, expert reports)
are downloaded. Medium/low/uncategorized are recorded as URLs only.

Storage: flat directory `data/pdfs/` with URL-based filenames.
Metadata tracked via CaseDocument objects on the Case model.
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import unquote, urljoin, urlparse

import yaml
from playwright.async_api import Page, Response

from src.models.case import Case, CaseDocument
from src.scraper.traffic_tracker import TrafficStats
from src.utils.logger import get_logger

logger = get_logger(__name__)

PDF_LINK_SELECTOR = 'a[href*="PdfDocument"]'
PDF_STORAGE_DIR = Path("data/pdfs")
PRIORITIES_PATH = Path("configs/dictionaries/document_priorities.yaml")


# ---------------------------------------------------------------------------
# Priority classifier
# ---------------------------------------------------------------------------

_priorities_cache: Optional[Dict[str, List[str]]] = None


def _load_priorities(path: Path = PRIORITIES_PATH) -> Dict[str, List[str]]:
    """Load {high: [...], medium: [...], low: [...]} from YAML, cached."""
    global _priorities_cache
    if _priorities_cache is not None:
        return _priorities_cache
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _priorities_cache = {
            "high": [s.lower().strip() for s in data.get("high", [])],
            "medium": [s.lower().strip() for s in data.get("medium", [])],
            "low": [s.lower().strip() for s in data.get("low", [])],
        }
    except FileNotFoundError:
        logger.warning("Priorities file not found: %s — all docs treated as uncategorized", path)
        _priorities_cache = {"high": [], "medium": [], "low": []}
    return _priorities_cache


def classify_priority(content_text: Optional[str]) -> str:
    """
    Match a document's result text against priority patterns.
    Returns 'high', 'medium', 'low', or 'uncategorized'.

    Matching is substring-based and case-insensitive so that
    "Оставить без изменения Решение; Оставить без изменения решение..."
    matches the pattern "Оставить без изменения Решение".
    """
    if not content_text:
        return "uncategorized"

    text = content_text.lower().strip()
    priorities = _load_priorities()

    for level in ("high", "medium", "low"):
        for pattern in priorities[level]:
            if pattern in text:
                return level

    return "uncategorized"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _abs_url(url: str, base_url: str = "https://kad.arbitr.ru") -> str:
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))


def _is_pdf(body: bytes) -> bool:
    return len(body) >= 4 and body[:4] == b"%PDF"


def _fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _safe_filename(url: str) -> str:
    """Human-readable name derived from the URL path tail."""
    name = unquote(urlparse(url).path.split("/")[-1] or "")
    if not name:
        return "document"
    name = re.sub(r'[<>:"|?*\\]', "_", name)
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    return name[:120]


# ---------------------------------------------------------------------------
# Collect PDF entries with priority from parsed case data
# ---------------------------------------------------------------------------

@dataclass
class PdfEntry:
    """A PDF link found on a case card, with its priority and metadata."""
    url: str
    priority: str
    content_text: Optional[str] = None
    update_type: Optional[str] = None
    date: Optional[str] = None
    doc_id: Optional[str] = None


def collect_pdf_entries(case: Case, base_url: str = "https://kad.arbitr.ru") -> List[PdfEntry]:
    """
    Build a deduplicated list of PDF entries from the parsed case,
    each tagged with a priority level.
    """
    seen: Set[str] = set()
    out: List[PdfEntry] = []

    def add(raw_url: Optional[str], content: Optional[str] = None,
            update_type: Optional[str] = None, date: Optional[str] = None,
            doc_id: Optional[str] = None) -> None:
        if not raw_url or not str(raw_url).strip():
            return
        url = _abs_url(str(raw_url).strip(), base_url)
        if url in seen:
            return
        seen.add(url)
        out.append(PdfEntry(
            url=url,
            priority=classify_priority(content),
            content_text=content,
            update_type=update_type,
            date=date,
            doc_id=doc_id,
        ))

    for inst in case.instances or []:
        add(inst.result_pdf_url, content=inst.result_text)
        for upd in inst.updates or []:
            add(upd.pdf_url, content=upd.content,
                update_type=upd.update_type, date=upd.date)
        for doc in inst.documents or []:
            add(doc.url, content=doc.filename,
                update_type=doc.type, date=doc.date, doc_id=doc.id)

    return out


# ---------------------------------------------------------------------------
# Download summary
# ---------------------------------------------------------------------------

@dataclass
class PdfDownloadSummary:
    urls_found: int = 0
    downloaded: int = 0
    failed: int = 0
    skipped_low_priority: int = 0
    bytes_downloaded: int = 0
    saved_files: List[str] = field(default_factory=list)
    recorded_urls: List[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def download_pdfs_for_case(
    page: Page,
    case: Case,
    base_url: str = "https://kad.arbitr.ru",
    *,
    storage_dir: Optional[Path] = None,
    stats: Optional[TrafficStats] = None,
    delay_seconds: tuple[float, float] = (0.8, 1.5),
    download_priorities: tuple[str, ...] = ("high",),
) -> PdfDownloadSummary:
    """
    Process PDFs on the case card:
    - High priority: download to disk via response listener
    - Medium/low/uncategorized: record the URL only (no download)

    Must be called while the page is on the case card (after chronology expansion).
    """
    out_dir = storage_dir or PDF_STORAGE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build entries from parsed case data
    entries = collect_pdf_entries(case, base_url)

    # If case hasn't been parsed yet, fall back to live DOM links (all uncategorized)
    if not entries:
        live_urls = await _collect_live_pdf_hrefs(page)
        entries = [PdfEntry(url=u, priority="uncategorized") for u in live_urls]

    summary = PdfDownloadSummary(urls_found=len(entries))
    if not entries:
        return summary

    to_download = [e for e in entries if e.priority in download_priorities]
    to_skip = [e for e in entries if e.priority not in download_priorities]

    logger.info(
        "Case %s: %d PDF(s) found — %d to download (%s), %d to skip",
        case.case_number, len(entries), len(to_download),
        ", ".join(download_priorities), len(to_skip),
    )

    # Record skipped URLs (medium/low/uncategorized)
    for entry in to_skip:
        summary.skipped_low_priority += 1
        summary.recorded_urls.append({
            "url": entry.url,
            "priority": entry.priority,
            "content": entry.content_text,
            "type": entry.update_type,
            "date": entry.date,
        })
        logger.debug(
            "  skip [%s]: %s — %s",
            entry.priority, entry.content_text or "?", entry.url[-60:],
        )

    # Download high-priority PDFs
    for i, entry in enumerate(to_download):
        if i > 0:
            await asyncio.sleep(random.uniform(*delay_seconds))

        logger.info(
            "  download [%s]: %s",
            entry.priority, entry.content_text or entry.url[-60:],
        )
        try:
            result = await _download_single_pdf(page, entry.url, out_dir, case.case_url or base_url)
        except Exception as e:
            logger.warning("PDF error for %s (case %s): %s", entry.url[-60:], case.case_number, e)
            summary.failed += 1
            continue

        if result is None:
            summary.failed += 1
            continue

        path, size = result
        summary.downloaded += 1
        summary.bytes_downloaded += size
        summary.saved_files.append(str(path))
        if stats is not None:
            stats.response_bytes += size
            stats.response_count += 1
            stats.request_count += 1
        logger.info(
            "  saved %s → %s", _fmt_size(size), path.name,
        )

    # Store results on the case object
    if hasattr(case, "extracted_data"):
        case.extracted_data["pdf_download_count"] = summary.downloaded
        case.extracted_data["pdf_download_bytes"] = summary.bytes_downloaded
        case.extracted_data["pdf_urls_found"] = summary.urls_found
        case.extracted_data["pdf_download_failed"] = summary.failed
        case.extracted_data["pdf_skipped_low_priority"] = summary.skipped_low_priority
        case.extracted_data["pdf_files"] = summary.saved_files
        case.extracted_data["pdf_recorded_urls"] = summary.recorded_urls

    logger.info(
        "Case %s: %d downloaded (%s), %d skipped, %d failed",
        case.case_number,
        summary.downloaded, _fmt_size(summary.bytes_downloaded),
        summary.skipped_low_priority, summary.failed,
    )
    return summary


# ---------------------------------------------------------------------------
# Live DOM link collection (fallback when case not yet parsed)
# ---------------------------------------------------------------------------

async def _collect_live_pdf_hrefs(page: Page) -> List[str]:
    """Read all PdfDocument links currently in the DOM."""
    raw: List[str] = await page.eval_on_selector_all(
        PDF_LINK_SELECTOR,
        "els => els.map(e => e.href || e.getAttribute('href')).filter(Boolean)",
    )
    seen: Set[str] = set()
    out: List[str] = []
    for href in raw:
        url = _abs_url(href)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


# ---------------------------------------------------------------------------
# Single PDF download via response listener
# ---------------------------------------------------------------------------

async def _download_single_pdf(
    page: Page,
    url: str,
    storage_dir: Path,
    referer: str,
    timeout_ms: int = 30000,
) -> Optional[tuple[Path, int]]:
    """
    Click a PDF link, let the browser load it naturally, capture the
    response body via an event listener.  Falls back to Ctrl+S download.

    Returns (path, size_bytes) or None on failure.
    """
    captured: dict = {"body": None}
    popup: Optional[Page] = None

    async def _on_response(response: Response) -> None:
        """Grab any response that looks like a PDF — don't filter by URL."""
        if captured["body"] is not None:
            return
        ct = response.headers.get("content-type", "")
        status = response.status
        logger.debug(
            "  resp: %s  status=%s  ct=%s",
            response.url[-70:], status, ct,
        )
        if status < 200 or status >= 300:
            return
        is_pdf_ct = "pdf" in ct.lower()
        is_octet = "octet-stream" in ct.lower()
        if not is_pdf_ct and not is_octet:
            return
        try:
            body = await response.body()
            logger.debug("  PDF candidate: size=%d  starts=%r", len(body), body[:8])
            if _is_pdf(body):
                captured["body"] = body
                logger.debug("  >>> captured %d bytes of real PDF", len(body))
        except Exception as exc:
            logger.debug("  body() failed: %s", exc)

    href_tail = urlparse(url).path.split("/")[-1] or "PdfDocument"
    link = page.locator(f'{PDF_LINK_SELECTOR}[href*="{href_tail}"]').first
    if await link.count() == 0:
        logger.warning("PDF link not on page: %s", url[-80:])
        return None

    page.context.on("response", _on_response)
    try:
        async with page.context.expect_page(timeout=timeout_ms) as page_info:
            await link.click()
        popup = await page_info.value

        try:
            await popup.wait_for_load_state("load", timeout=timeout_ms)
        except Exception:
            pass

        for _ in range(15):
            if captured["body"] is not None:
                break
            await asyncio.sleep(1)

        if captured["body"] is None:
            logger.debug("Response listener got nothing, trying Ctrl+S...")
            save_path = storage_dir / "_tmp_download.pdf"
            try:
                await popup.bring_to_front()
                async with popup.expect_download(timeout=15000) as dl_info:
                    await popup.keyboard.press("Control+s")
                download = await dl_info.value
                await download.save_as(str(save_path))
                if save_path.exists():
                    body = save_path.read_bytes()
                    if _is_pdf(body):
                        captured["body"] = body
                    save_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug("Ctrl+S fallback failed: %s", exc)
                if save_path.exists():
                    save_path.unlink(missing_ok=True)

    except Exception as e:
        logger.debug("Popup open failed for %s: %s", url[-60:], e)
    finally:
        page.context.remove_listener("response", _on_response)
        if popup is not None:
            try:
                await popup.close()
            except Exception:
                pass

    body = captured.get("body")
    if not body or not _is_pdf(body):
        logger.warning(
            "No PDF bytes captured for %s (%d bytes received)",
            url[-60:], len(body) if body else 0,
        )
        return None

    fname = _safe_filename(url) + ".pdf"
    save_path = storage_dir / fname
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(body)
    return save_path, len(body)
