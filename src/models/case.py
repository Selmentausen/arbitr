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


class CaseBase(BaseModel):
    """Basic case data from initial scraping (visible data on search page)."""

    id: str = Field(..., description="Unique case ID from kad.arbitr.ru")
    case_number: str = Field(..., description="Case number (e.g. A40-123/2024)")
    court: str = Field(..., description="Court name")
    judges: list[str] = Field(default_factory=list, description="List of judges (may be empty)")
    plaintiff: str = Field(..., description="Plaintiff party name")
    defendant: str = Field(..., description="Defendant party name")
    filing_date: Optional[datetime] = Field(None, description="Date when case was filed")
    case_url: Optional[str] = Field(None, description="URL to case card")
    third_parties: list[str] = Field(
        default_factory=list, description="Third parties involved (if any)"
    )

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "id": "A40-123456/2024",
                "court": "Арбитражный суд города Москвы",
                "judges": ["Иванов И.И."],
                "plaintiff": "ООО 'Строитель'",
                "defendant": "ООО 'Заказчик'",
                "third_parties": [],
            }
        }


class CaseParticipant(BaseModel):
    name: str
    address: Optional[str] = None
    inn: Optional[str] = None
    ogrn: Optional[str] = None
    
class CaseDocument(BaseModel):
    id: Optional[str] = None
    filename: Optional[str] = None
    url: Optional[str] = None
    date: Optional[str] = None
    type: Optional[str] = None
    
class CaseInstance(BaseModel):
    court_name: str
    case_number: Optional[str] = None
    incoming_number: Optional[str] = None
    date: Optional[str] = None
    documents: List[CaseDocument] = Field(default_factory=list)

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
        default_factory=list, description="IDs of related cases (by plaintiff/defendant)"
    )
    aggregated_metrics: dict = Field(
        default_factory=dict,
        description="Aggregated metrics from linkage (dispute_count, avg_duration, mediation_rate)",
    )

    # Raw content (filled progressively through pipeline stages)
    raw_html: Optional[str] = Field(None, description="HTML content from case page (stage 2)")
    pdf_texts: list[str] = Field(
        default_factory=list, description="Extracted text from PDFs (stage 3)"
    )

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "id": "A40-123456/2024",
                "court": "Арбитражный суд города Москвы",
                "judges": ["Иванов И.И."],
                "plaintiff": "ООО 'Строитель'",
                "defendant": "ООО 'Заказчик'",
                "third_parties": [],
                "category": "construction",
                "relevance_score": 75.5,
                "status": "high_relevant",
                "extracted_data": {
                    "client_info": "ООО 'Строитель', ИНН: 1234567890",
                    "duration": 120,
                    "outcome": "settled",
                },
                "related_cases": ["A40-123457/2024", "A40-123458/2023"],
                "aggregated_metrics": {
                    "dispute_count": 5,
                    "avg_duration": 110,
                    "mediation_rate": 0.4,
                },
                "raw_html": None,
                "pdf_texts": [],
            }
        }
