"""
Pydantic schemas for orchestrator API requests and responses.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --- Worker Schemas ---

class WorkerRegisterRequest(BaseModel):
    """Worker registration payload."""
    worker_id: str
    ip_address: Optional[str] = None
    vps_id: Optional[str] = None
    provider: str = "timeweb"
    vm_id: Optional[str] = None
    proxy_port: Optional[int] = None


class WorkerHeartbeatRequest(BaseModel):
    """Worker heartbeat payload."""
    worker_id: str
    timestamp: Optional[str] = None
    proxy_running: Optional[bool] = None


class WorkerHeartbeatResponse(BaseModel):
    """Heartbeat response — may include a command for the worker."""
    ok: bool
    message: str = "Heartbeat received"
    command: Optional[Dict[str, Any]] = None


class WorkerBlockedRequest(BaseModel):
    """Worker reports IP block."""
    reason: Optional[str] = None
    blocked_url: Optional[str] = None


class WorkerStatusResponse(BaseModel):
    """Worker status response."""
    id: str
    vps_id: Optional[str] = None
    ip_address: Optional[str] = None
    provider: Optional[str] = None
    status: str
    current_judge: Optional[str] = None
    proxy_port: Optional[int] = None
    last_heartbeat: Optional[datetime] = None
    registered_at: Optional[datetime] = None
    blocked_at: Optional[datetime] = None
    total_cases_scraped: int = 0
    total_judges_completed: int = 0


# --- Job Schemas ---

class JobClaimResponse(BaseModel):
    """Response when a worker claims a judge job."""
    judge_name: str
    court: Optional[str] = None
    status: str
    cases_collected: int = 0
    max_cases: int = 0
    total_count_at_start: int = 0
    retry_count: int = 0


class JobProgressRequest(BaseModel):
    """Worker reports scraping progress."""
    cases_collected: Optional[int] = None
    status: Optional[str] = None  # collecting, enriching


class JobCompleteRequest(BaseModel):
    """Worker reports judge scraping complete."""
    cases_collected: int = 0
    status: str = "completed"


class JobFailedRequest(BaseModel):
    """Worker reports judge scraping failed."""
    error_message: Optional[str] = None


class JobReleaseRequest(BaseModel):
    """Worker releases a claimed job (graceful shutdown or block)."""
    reason: str = ""


class JobReleaseResponse(BaseModel):
    """Response after releasing a job."""
    ok: bool
    judge_name: str
    status: str = "pending"


# --- Case Schemas ---

class CaseSubmission(BaseModel):
    """Single case submitted by a worker."""
    id: str
    case_number: str
    court: str
    case_url: Optional[str] = None
    case_type: Optional[str] = None
    current_instance: Optional[str] = None
    is_simple_justice: bool = False
    filing_date: Optional[datetime] = None
    judges: List[str] = Field(default_factory=list)
    participants: List[Dict[str, Any]] = Field(default_factory=list)
    instances: List[Dict[str, Any]] = Field(default_factory=list)
    # Filtering results
    category: Optional[str] = None
    relevance_score: float = 0.0
    status: str = "insufficient_info"
    extracted_data: Dict[str, Any] = Field(default_factory=dict)
    # Case page data
    case_status_text: Optional[str] = None
    case_category_text: Optional[str] = None
    claim_amount: Optional[float] = None
    case_page_scraped: bool = False
    raw_html: Optional[str] = None
    pdf_texts: List[str] = Field(default_factory=list)


class BatchCaseSubmission(BaseModel):
    """Batch of cases submitted by a worker."""
    worker_id: str
    vps_id: Optional[str] = None
    judge_name: str
    batch_id: str = ""  # UUID for idempotency
    cases: List[CaseSubmission]
    documents: List[Dict[str, Any]] = Field(default_factory=list)
    # List of {case_id, doc_id, filename, url} for which the worker
    # has PDF bytes and needs a presigned upload URL


class BatchCaseResponse(BaseModel):
    """Response after submitting a batch of cases — includes presigned S3 URLs."""
    ok: bool
    message: str = ""
    saved: int = 0
    errors: int = 0
    upload_urls: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    # {case_id: {doc_id: presigned_url}}


class UploadCompleteRequest(BaseModel):
    """Worker confirms PDF uploads were completed."""
    uploads: List[Dict[str, Any]] = Field(default_factory=list)


# --- Document Upload ---

class DocumentUploadRequest(BaseModel):
    """Worker requests a presigned URL for a specific PDF."""
    case_id: str
    doc_id: Optional[str] = None
    filename: str


class DocumentUploadResponse(BaseModel):
    """Response after uploading a document."""
    storage_key: str
    filename: str
    size_bytes: int


# --- Dashboard Schemas ---

class DashboardStats(BaseModel):
    """Aggregate dashboard statistics."""
    total_cases: int = 0
    total_documents: int = 0
    total_judges_queued: int = 0
    judges_completed: int = 0
    judges_in_progress: int = 0
    judges_pending: int = 0
    judges_failed: int = 0
    active_workers: int = 0
    blocked_workers: int = 0
    offline_workers: int = 0


class ThroughputMetrics(BaseModel):
    """Throughput metrics for dashboard."""
    cases_per_hour: float = 0.0
    judges_per_hour: float = 0.0
    active_since: Optional[datetime] = None


# --- Fleet Management ---

class RotateRequest(BaseModel):
    """Request to rotate a worker's IP."""
    strategy: str = "new_ip"  # new_ip or cooldown


class RotationStatus(BaseModel):
    """Status of an IP rotation."""
    worker_id: str
    old_ip: Optional[str] = None
    new_ip: Optional[str] = None
    status: str = "pending"  # pending, in_progress, completed, failed
    reason: str = ""


# --- Common ---

class StatusResponse(BaseModel):
    """Generic status response."""
    ok: bool
    message: str = ""
    data: Optional[Dict[str, Any]] = None
