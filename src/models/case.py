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

class CaseParticipant(BaseModel):
    """Participant (party or judge) in a case."""

    name: str
    role: Optional[str] = None
    address: Optional[str] = None
    inn: Optional[str] = None
    ogrn: Optional[str] = None


class CaseDocument(BaseModel):
    """Document (usually PDF) associated with a case."""

    id: Optional[str] = None
    filename: Optional[str] = None
    url: Optional[str] = None
    date: Optional[str] = None
    type: Optional[str] = None
    priority: Optional[str] = None
    publish_date: Optional[str] = None
    extracted_text: Optional[str] = None
    storage_key: Optional[str] = None


class InstanceUpdate(BaseModel):
    """Single chronology entry within a court instance."""

    date: Optional[str] = None
    update_type: Optional[str] = None
    subject: Optional[str] = None
    content: Optional[str] = None
    pdf_url: Optional[str] = None
    pdf_publish_date: Optional[str] = None
    additional_info: Optional[str] = None
    judge_panel: Optional[str] = None
    reporting_judge: Optional[str] = None


class CaseInstance(BaseModel):
    """Court instance (Первая инстанция, Апелляционная инстанция, etc.)."""

    court_name: str
    case_number: Optional[str] = None
    instance_level: Optional[str] = None
    incoming_number: Optional[str] = None
    date: Optional[str] = None
    result_text: Optional[str] = None
    result_pdf_url: Optional[str] = None
    updates: List[InstanceUpdate] = Field(default_factory=list)
    documents: List[CaseDocument] = Field(default_factory=list)


class Case(BaseModel):
    """Full case model with all fields. Populated incrementally during scraping."""

    # --- Fields from initial list-page scraping ---
    id: str = Field(..., description="Unique case ID from kad.arbitr.ru")
    case_number: str = Field(..., description="Case number (e.g. A40-123/2024)")
    court: str = Field(..., description="Court name")
    judges: List[str] = Field(default_factory=list, description="List of judges")
    plaintiff: str = Field(default="", description="Plaintiff party name")
    defendant: str = Field(default="", description="Defendant party name")
    filing_date: Optional[datetime] = Field(None, description="Date when case was filed")
    case_url: Optional[str] = Field(None, description="URL to case card")
    case_type: Optional[str] = Field(None, description="Case type (civil, administrative, bankruptcy)")
    current_instance: Optional[str] = Field(None, description="Current instance level text")
    scraped_at: Optional[datetime] = Field(None, description="When this data was scraped")
    third_parties: List[str] = Field(default_factory=list, description="Third parties involved (if any)")

    # --- Fields populated during case-page enrichment ---
    participants: Dict[str, List[CaseParticipant]] = Field(default_factory=dict)
    instances: List[CaseInstance] = Field(default_factory=list)
    is_simple_justice: bool = False
    case_status_text: Optional[str] = Field(None, description="E.g. 'Рассмотрение дела завершено'")
    case_category_text: Optional[str] = Field(None, description="E.g. 'экономические споры по гражданским правоотношениям'")
    claim_amount: Optional[float] = Field(None, description="Claim amount from initial filing")
    case_page_scraped: bool = False
    last_scraped_at: Optional[datetime] = None
    raw_html: Optional[str] = Field(None, description="HTML content from case page")
    pdf_texts: List[str] = Field(default_factory=list, description="Extracted text from PDFs")

    # --- Fields populated by the filter pipeline ---
    category: Optional[str] = Field(None, description="Legal area category (e.g., 'construction', 'bankruptcy')")
    relevance_score: float = Field(default=0.0, ge=0.0, le=100.0, description="Relevance score (0-100)")
    status: StatusEnum = Field(default=StatusEnum.INSUFFICIENT_INFO, description="Current filtering status")

    # --- Fields populated by analysis / linkage ---
    extracted_data: dict = Field(default_factory=dict, description="Extracted information (client_info, duration, outcome, etc.)")
    related_cases: List[str] = Field(default_factory=list, description="IDs of related cases")
    aggregated_metrics: dict = Field(default_factory=dict, description="Aggregated metrics from linkage")
