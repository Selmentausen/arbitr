"""
Download PDFs from kad.arbitr.ru case cards via response listener interception.

kad.arbitr.ru serves PDFs behind DDOS-Guard. The browser loads them through
a redirect chain (GIF → 301 → challenge → real PDF). We capture the real
PDF bytes via a context.on("response") listener.

Priority filtering: all high priority documents or any one document with highest available are prority downloaded. 
Everything else is saved as an URL to the pdf file

Storage: flat directory `data/pdfs/` with URL-based filenames.
Metadata tracked via CaseDocument objects on the Case model.
"""

from __future__ import annotations

import asyncio
import base64
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import unquote, urljoin, urlparse

import yaml
from playwright.async_api import Page, Response, Route

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

    high_entries = [e for e in entries if e.priority == "high"]
    medium_entries = [e for e in entries if e.priority == "medium"]
    low_entries = [e for e in entries if e.priority == "low"]
    other_entries = [e for e in entries if e.priority not in ("high", "medium", "low")]

    to_download = []
    to_skip = []
    if high_entries:
        to_download = high_entries
        to_skip = medium_entries + low_entries + other_entries
    elif medium_entries:
        to_download = [medium_entries[0]]
        to_skip = medium_entries[1:] + low_entries + other_entries
    elif low_entries:
        to_download = [low_entries[0]]
        to_skip = low_entries[1:] + other_entries
    elif other_entries:
        to_download = [other_entries[0]]
        to_skip = other_entries[1:]

    downloaded_priorities = sorted(list(set(e.priority for e in to_download)))
    logger.info(
        "Case %s: %d PDF(s) found — %d to download (%s), %d to skip",
        case.case_number, len(entries), len(to_download),
        ", ".join(downloaded_priorities) if downloaded_priorities else "none", len(to_skip),
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
# Direct JS PDF download via fetch API (optimizes bandwidth)
# ---------------------------------------------------------------------------

async def _download_via_js_fetch(page: Page, url: str) -> Optional[bytes]:
    """Execute fetch() directly in browser page context to download PDF bytes."""
    logger.info("Attempting direct JS fetch for PDF: %s", url)
    try:
        # We fetch the URL in page context
        js_code = """
        async (url) => {
            const resp = await fetch(url);
            if (!resp.ok) {
                throw new Error("HTTP status " + resp.status);
            }
            const buf = await resp.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let binary = '';
            const len = bytes.byteLength;
            for (let i = 0; i < len; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            return {
                base64: btoa(binary),
                contentType: resp.headers.get("content-type") || ""
            };
        }
        """
        res = await page.evaluate(js_code, url)
        if not res or "base64" not in res:
            logger.info("Direct JS fetch returned empty or invalid payload.")
            return None

        body = base64.b64decode(res["base64"])
        if _is_pdf(body):
            logger.info("Successfully fetched %d bytes of PDF via direct JS fetch.", len(body))
            return body
        else:
            logger.info("JS fetch succeeded but bytes do not start with %%PDF.")
            return None
    except Exception as e:
        logger.info("Direct JS fetch failed: %s", e)
        return None


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
    response body via an event listener.

    Returns (path, size_bytes) or None on failure.
    """


    href_tail = urlparse(url).path.split("/")[-1] or "PdfDocument"
    link = page.locator(f'{PDF_LINK_SELECTOR}[href*="{href_tail}"]').first
    if await link.count() == 0:
        logger.warning("PDF link not on page: %s", url[-80:])
        return None

    # 2. Programmatic POST Interception Flow
    logger.info("Initiating POST Interception flow for PDF: %s", url)
    save_path = storage_dir / (_safe_filename(url) + ".pdf")
    save_path.parent.mkdir(parents=True, exist_ok=True)

    captured = {"body": None, "url": None}

    async def pdf_route_handler(route: Route) -> None:
        req = route.request
        if "Document/Pdf" in req.url and req.method == "POST":
            try:
                logger.info("Intercepted POST request to: %s", req.url)
                response = await route.fetch()
                body = await response.body()
                if _is_pdf(body):
                    captured["body"] = body
                    captured["url"] = req.url
                    logger.info("PDF captured successfully via POST intercept (%d bytes)!", len(body))
                    # Fulfill with dummy to prevent browser loading overhead
                    await route.fulfill(
                        status=200,
                        content_type="application/pdf",
                        body=b"%PDF-1.4 dummy",
                    )
                    return
                else:
                    logger.warning("POST intercept body did not start with %%PDF: %r", body[:30])
            except Exception as e:
                logger.error("Error in PDF route intercept handler: %s", e)
        await route.continue_()

    # Register context-wide route handler for any Document/Pdf URLs
    await page.context.route("**/Document/Pdf/**", pdf_route_handler)

    popup: Optional[Page] = None
    popup_future = asyncio.ensure_future(page.context.wait_for_event("page", timeout=20000))

    try:
        logger.info("Clicking PDF link to trigger challenges and POST...")
        await link.evaluate("node => node.click()")

        try:
            popup = await popup_future
        except Exception as popup_err:
            logger.error("Popup page did not open: %s", popup_err)
            popup_future.cancel()
            if popup_future.done():
                try:
                    popup_future.exception()
                except Exception:
                    pass
            return None

        # Poll the captured dict for up to 35 seconds to allow the page to load,
        # solve DDOS-Guard, and issue the POST request.
        logger.info("Waiting for PDF POST interception to capture the stream...")
        for _ in range(70):
            if captured["body"] is not None:
                break
            await asyncio.sleep(0.5)

        if captured["body"] is not None:
            save_path.write_bytes(captured["body"])
            return save_path, len(captured["body"])
            
        logger.error("Failed to intercept PDF POST request within timeout.")

    except Exception as e:
        logger.error("Interception flow failed: %s", e)
    finally:
        # Always unroute the handler to avoid affecting other navigations
        try:
            await page.context.unroute("**/Document/Pdf/**", pdf_route_handler)
        except Exception:
            pass
        if popup is not None:
            try:
                await popup.close()
            except Exception:
                pass

    return None
