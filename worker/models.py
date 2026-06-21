"""
Internal data models for the worker.
No external dependencies except Pydantic.
"""
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field


class PdfAttachment(BaseModel):
    """A PDF file collected during scraping, ready for S3 upload."""

    case_id: str
    doc_id: Optional[str] = None
    filename: str
    url: str
    bytes: Optional[bytes] = None
    extracted_text: Optional[str] = None
    priority: str = "uncategorized"
    content_text: Optional[str] = None
    doc_type: Optional[str] = None
    date: Optional[str] = None
    size_bytes: int = 0


class ScrapeResult(BaseModel):
    """Result of scraping a single judge."""

    judge_name: str
    cases: List[Dict[str, Any]] = Field(default_factory=list)
    pdfs: List[PdfAttachment] = Field(default_factory=list)
    total_cases_found: int = 0
    cases_after_filter: int = 0
    is_blocked: bool = False
    block_reason: str = ""
    error: Optional[str] = None
    success: bool = False

    @property
    def cases_uploaded(self) -> int:
        return len(self.cases)

    @property
    def pdfs_uploaded(self) -> int:
        return len([p for p in self.pdfs if p.bytes is not None])
