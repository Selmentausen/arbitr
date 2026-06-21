"""Data models for court cases using Pydantic."""

from enum import Enum
from typing import Optional, List, Dict
from datetime import datetime

from pydantic import BaseModel, Field


class StatusEnum(str, Enum):
    """Status of case relevance after filtering."""

    HIGH_RELEVANT = "high_relevant"
    REJECT = "reject"
    INSUFFICIENT_INFO = "insufficient_info"
    UNCERTAIN = "uncertain"


# --- Sub-models ---

class PartyInfo(BaseModel):
    """Party info extracted from case list rollover (INN, address)."""
    name: str
    inn: Optional[str] = None
    address: Optional[str] = None


class CaseParticipant(BaseModel):
    """Detailed participant from case page."""
    name: str
    address: Optional[str] = None
    inn: Optional[str] = None
    ogrn: Optional[str] = None
    role: Optional[str] = None


class CaseDocument(BaseModel):
    """Document (usually PDF) associated with a case."""
    id: Optional[str] = None
    filename: Optional[str] = None
    url: Optional[str] = None
    date: Optional[str] = None
    type: Optional[str] = None
    priority: Optional[str] = None           # "high", "medium", "low", "uncategorized"
    publish_date: Optional[str] = None
    extracted_text: Optional[str] = None     # PDF text (extracted for high-priority docs)
    storage_key: Optional[str] = None        # MinIO/S3 object key (e.g., "pdfs/{case_id}/{doc_id}.pdf")


class InstanceUpdate(BaseModel):
    """Single chronology entry within a court instance."""
    date: Optional[str] = None
    update_type: Optional[str] = None        # "Определение", "Письмо", "Жалоба", etc.
    subject: Optional[str] = None            # Who filed it / judge name
    content: Optional[str] = None            # The result text description
    pdf_url: Optional[str] = None            # Link to PDF if present
    pdf_publish_date: Optional[str] = None   # Publication datetime
    additional_info: Optional[str] = None    # Barcode, claim amount, response-to, etc.
    judge_panel: Optional[str] = None        # From rollover: судебный состав
    reporting_judge: Optional[str] = None    # From rollover: судья-докладчик


class CaseInstance(BaseModel):
    """Court instance (Первая инстанция, Апелляционная инстанция, etc.)."""
    court_name: str
    case_number: Optional[str] = None
    instance_level: Optional[str] = None     # "Первая инстанция", "Апелляционная инстанция"
    incoming_number: Optional[str] = None
    date: Optional[str] = None               # Last update date on the header
    result_text: Optional[str] = None        # Header result text
    result_pdf_url: Optional[str] = None     # Header result PDF link
    updates: List[InstanceUpdate] = Field(default_factory=list)
    documents: List[CaseDocument] = Field(default_factory=list)


# --- Main models ---

class CaseBase(BaseModel):
    """Basic case data from initial scraping (search results page)."""

    id: str = Field(..., description="Unique case ID from kad.arbitr.ru")
    case_number: str = Field(..., description="Case number (e.g. A40-123/2024)")
    court: str = Field(..., description="Court name")
    judges: list[str] = Field(default_factory=list, description="List of judges")
    plaintiff: str = Field(..., description="Plaintiff party name")
    defendant: str = Field(..., description="Defendant party name")
    filing_date: Optional[datetime] = Field(None, description="Date when case was filed")
    case_url: Optional[str] = Field(None, description="URL to case card")
    case_type: Optional[str] = Field(None, description="Case type (civil, administrative, bankruptcy)")
    current_instance: Optional[str] = Field(None, description="Current instance level text")
    plaintiff_info: Optional[PartyInfo] = Field(None, description="Plaintiff details from rollover")
    defendant_info: List[PartyInfo] = Field(default_factory=list, description="Defendant details from rollover")
    scraped_at: Optional[datetime] = Field(None, description="When this data was scraped")
    third_parties: list[str] = Field(
        default_factory=list, description="Third parties involved (if any)"
    )


class Case(CaseBase):
    """
    Full case model with filtering results and extracted data.
    
    Extends CaseBase with scoring, categorization, extracted information,
    linkage data, and raw content from deeper scraping stages.
    """
    participants: Dict[str, List[CaseParticipant]] = Field(default_factory=dict)
    instances: List[CaseInstance] = Field(default_factory=list)
    judges: List[str] = Field(default_factory=list)
    is_simple_justice: bool = False
    
    # Case page metadata
    case_status_text: Optional[str] = Field(None, description="E.g. 'Рассмотрение дела завершено'")
    case_category_text: Optional[str] = Field(None, description="E.g. 'экономические споры по гражданским правоотношениям'")
    claim_amount: Optional[float] = Field(None, description="Claim amount from initial filing")
    case_page_scraped: bool = False
    last_scraped_at: Optional[datetime] = None
    
    # Filtering / scoring
    category: Optional[str] = Field(
        None, description="Legal area category (e.g., 'construction', 'bankruptcy')"
    )
    relevance_score: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Relevance score (0-100)"
    )
    status: StatusEnum = Field(
        default=StatusEnum.INSUFFICIENT_INFO, description="Current filtering status"
    )

    # Extracted data from analysis
    extracted_data: dict = Field(
        default_factory=dict,
        description="Extracted information (client_info, duration, outcome, etc.)",
    )

    # Linkage and relationships
    related_cases: list[str] = Field(
        default_factory=list, description="IDs of related cases"
    )
    aggregated_metrics: dict = Field(
        default_factory=dict,
        description="Aggregated metrics from linkage",
    )

    # Raw content (filled progressively)
    raw_html: Optional[str] = Field(None, description="HTML content from case page")
    pdf_texts: list[str] = Field(
        default_factory=list, description="Extracted text from PDFs"
    )
