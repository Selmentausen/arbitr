"""
Scraper wrapper for the distributed worker.

Imports existing PlaywrightScraper and FilterPipeline from src/.
Downloads PDFs into memory, extracts text, and returns serializable data.

This is the only file in worker/ that depends on src/.
"""
import asyncio
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from worker.models import ScrapeResult, PdfAttachment
from worker.block import detect_block, is_content_suspicious

# Existing project imports
from src.scraper.playwright_scraper import PlaywrightScraper
from src.filters.pipeline import FilterPipeline
from src.config.manager import ConfigManager
from src.scraper.pdf_downloader import download_pdfs_for_case
from src.models.case import Case
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Try to import existing PDF extractor; fall back to direct PyMuPDF if not available
try:
    from src.analysis.pdf_extractor import extract_text_from_pdf
except ImportError:
    def extract_text_from_pdf(path: str) -> Optional[str]:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text.strip() if text.strip() else None
        except Exception:
            return None


class WorkerScraper:
    """
    Wraps the existing PlaywrightScraper for use in the distributed worker.
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self.filter_pipeline = FilterPipeline(config)

    async def scrape_judge(
        self,
        judge_name: str,
        proxy_port: Optional[int] = None,
        proxy_bind_ip: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> ScrapeResult:
        """
        Scrape all cases for a judge.

        Args:
            judge_name: Full judge name (e.g., "Титова О. А.")
            proxy_port: Local SOCKS proxy port (if any)
            proxy_bind_ip: IP address the proxy binds to (for microsocks)

        Returns:
            ScrapeResult with cases, PDFs, and block status.
        """
        result = ScrapeResult(judge_name=judge_name)

        try:
            async with PlaywrightScraper(
                self.config, headless=True
            ) as scraper:
                # Configure proxy if provided
                if proxy_port:
                    scraper.proxy = {
                        "server": f"socks5://127.0.0.1:{proxy_port}",
                    }

                # Stage 1: Collect cases
                logger.info("Collecting cases for judge: %s", judge_name)
                cases = await scraper.search_by_judge(judge_name)
                result.total_cases_found = len(cases)

                # Check for block after search
                if not cases:
                    last_html = getattr(
                        scraper, "last_response_html", None
                    ) or getattr(scraper, "_last_html", None)
                    is_blocked, reason = detect_block(
                        response_html=last_html, cases_found=0
                    )
                    if is_blocked:
                        result.is_blocked = True
                        result.block_reason = reason
                        logger.warning(
                            "Block detected after search: %s", reason
                        )
                        return result

                    logger.info(
                        "No cases found for judge %s", judge_name
                    )
                    result.success = True
                    return result

                # Apply Stage 1 filter
                filtered_cases = self.filter_pipeline.process_batch(cases)
                result.cases_after_filter = len(filtered_cases)
                logger.info(
                    "Stage 1: %d cases → %d after filter",
                    len(cases),
                    len(filtered_cases),
                )

                # Stage 2: Enrich cases and collect PDFs
                enriched_cases = []
                pdfs = []

                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_path = Path(tmpdir)

                    for i, case in enumerate(filtered_cases):
                        if stop_event and stop_event.is_set():
                            logger.info(
                                "Shutdown requested during enrichment"
                            )
                            break

                        if case.case_url and not case.case_page_scraped:
                            try:
                                enriched = await scraper.scrape_case_page(
                                    case
                                )
                                if enriched:
                                    case = enriched
                            except Exception as e:
                                logger.warning(
                                    "Failed to enrich case %s: %s",
                                    case.case_number,
                                    e,
                                )
                                continue

                        # Check for block during enrichment
                        try:
                            page_html = await scraper.page.content()
                            if is_content_suspicious(page_html):
                                result.is_blocked = True
                                result.block_reason = (
                                    "Suspicious page content during "
                                    "enrichment"
                                )
                                logger.warning(
                                    "Block detected during enrichment "
                                    "of case %s",
                                    case.case_number,
                                )
                                return result
                        except Exception:
                            pass  # Can't read page content, keep going

                        # Download PDFs for this case
                        if case.instances and self.config.get(
                            "filtering.pdf_download_enabled", True
                        ):
                            try:
                                pdf_summary = await download_pdfs_for_case(
                                    page=scraper.page,
                                    case=case,
                                    storage_dir=tmp_path,
                                )

                                # Read downloaded PDFs back into bytes
                                for file_path in pdf_summary.saved_files:
                                    path = Path(file_path)
                                    if not path.exists():
                                        continue

                                    pdf_bytes = path.read_bytes()
                                    text = extract_text_from_pdf(str(path))

                                    # Add extracted text to the case for submission
                                    # (even when S3 is disabled, we want the text)
                                    if text and text.strip():
                                        case.pdf_texts.append(text.strip())
                                    doc_meta = self._find_doc_meta(
                                        case, str(path)
                                    )

                                    pdfs.append(
                                        PdfAttachment(
                                            case_id=case.id,
                                            doc_id=doc_meta.get("id"),
                                            filename=path.name,
                                            url=doc_meta.get(
                                                "url", ""
                                            ),
                                            bytes=pdf_bytes,
                                            extracted_text=text,
                                            priority=doc_meta.get(
                                                "priority",
                                                "uncategorized",
                                            ),
                                            content_text=doc_meta.get(
                                                "content_text"
                                            ),
                                            doc_type=doc_meta.get(
                                                "type"
                                            ),
                                            date=doc_meta.get("date"),
                                            size_bytes=len(pdf_bytes),
                                        )
                                    )
                            except Exception as e:
                                logger.warning(
                                    "PDF download failed for case %s: %s",
                                    case.case_number,
                                    e,
                                )

                        # Convert to serializable dict
                        enriched_cases.append(self._case_to_dict(case))

                        # Periodic progress logging
                        if (i + 1) % 5 == 0:
                            logger.info(
                                "Enriched %d/%d cases for judge %s",
                                i + 1,
                                len(filtered_cases),
                                judge_name,
                            )

                result.cases = enriched_cases
                result.pdfs = pdfs
                result.success = True
                logger.info(
                    "Judge %s complete: %d cases, %d PDFs",
                    judge_name,
                    len(enriched_cases),
                    len(pdfs),
                )

        except Exception as e:
            logger.error(
                "Scraper error for judge %s: %s",
                judge_name,
                e,
                exc_info=True,
            )
            result.error = str(e)

        return result

    def _find_doc_meta(
        self, case: Case, file_path: str
    ) -> Dict[str, Any]:
        """Find document metadata matching a downloaded file path."""
        for inst in case.instances or []:
            for doc in inst.documents or []:
                if doc.filename and doc.filename in file_path:
                    return {
                        "id": doc.id,
                        "url": doc.url,
                        "priority": doc.priority,
                        "content_text": doc.filename,
                        "type": doc.type,
                        "date": doc.date,
                    }
        return {}

    def _case_to_dict(self, case: Case) -> Dict[str, Any]:
        """
        Convert a Pydantic Case to a flat dict for JSON serialization.
        """
        # Start with base serialization
        data = case.model_dump(mode="json", exclude_none=True)

        # Flatten participants from dict-of-lists → flat list with role
        flat_participants = []
        for role, participants in (
            getattr(case, "participants", None) or {}
        ).items():
            for p in participants:
                flat_participants.append(
                    {
                        "name": p.name,
                        "role": p.role or role,
                        "inn": p.inn,
                        "address": p.address,
                        "ogrn": p.ogrn,
                    }
                )
        data["participants"] = flat_participants

        # Ensure instances are properly serialized (they should be already)
        # but make sure documents have their extracted_text and storage_key
        return data
