"""
SQLAlchemy database models and connection setup.

Supports both PostgreSQL (production via DATABASE_URL) and SQLite (local dev).

Tables:
- cases: Main case data (mirrors Case pydantic model)
- participants: Case participants (plaintiffs, defendants, third parties)
- case_participants: Association table linking cases to participants
- documents: Case documents (PDFs, etc.)
- instances: Court instances (chronology headers)
- instance_updates: Individual chronology entries within instances
- judges: Judges associated with cases
- scrape_events: Per-judge scrape attempt tracking
- judge_progress: Per-judge progress for resume support
- scrape_meta: Global scrape metadata
- worker_status: VPS fleet health tracking
"""

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

# Use JSONB on PostgreSQL, TEXT on SQLite
# We detect the dialect at table creation time
try:
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
except ImportError:
    PG_JSONB = None


def _json_column(**kwargs):
    """
    Create a column type that uses JSONB on PostgreSQL and TEXT on SQLite.

    We use TypeDecorator-free approach: the column is created as Text,
    and we rely on SQLAlchemy's type adaptation. For PostgreSQL, we override
    with JSONB in the __table_args__ or use a conditional type.

    Simpler approach: always use Text, and use JSONB only when running on PG.
    """
    # We'll use a custom type that switches based on dialect
    from sqlalchemy import types

    class JSONBOrText(types.TypeDecorator):
        """Use JSONB on PostgreSQL, TEXT on other dialects."""
        impl = types.Text
        cache_ok = True

        def load_dialect_impl(self, dialect):
            if dialect.name == "postgresql" and PG_JSONB is not None:
                return dialect.type_descriptor(PG_JSONB())
            return dialect.type_descriptor(types.Text())

    return Column(JSONBOrText(), **kwargs)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


class CaseRecord(Base):
    """Case database record."""
    __tablename__ = "cases"

    # Primary key — case UUID from kad.arbitr.ru
    id = Column(String, primary_key=True)
    case_number = Column(String, nullable=False, index=True)
    court = Column(String, nullable=False)
    filing_date = Column(DateTime, nullable=True)
    case_url = Column(String, nullable=True)
    case_type = Column(String, nullable=True, index=True)
    current_instance = Column(String, nullable=True)
    is_simple_justice = Column(Boolean, default=False)

    # Case page metadata
    case_status_text = Column(String, nullable=True)
    case_category_text = Column(String, nullable=True)
    claim_amount = Column(Float, nullable=True)

    # Filtering / scoring
    category = Column(String, nullable=True, index=True)
    relevance_score = Column(Float, default=0.0)
    status = Column(String, default="insufficient_info", index=True)

    # Extracted data — JSONB on PostgreSQL, TEXT on SQLite
    extracted_data_json = _json_column(default="{}")
    aggregated_metrics_json = _json_column(default="{}")

    # Raw content
    raw_html = Column(Text, nullable=True)
    pdf_texts_json = Column(Text, default="[]")

    # Scraping state
    case_page_scraped = Column(Boolean, default=False)
    documents_scraped = Column(Boolean, default=False)
    scraped_at = Column(DateTime, nullable=True)
    last_scraped_at = Column(DateTime, nullable=True)

    # Review state
    reviewed = Column(Boolean, default=False, index=True)
    review_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    participants = relationship("CaseParticipantLink", back_populates="case", cascade="all, delete-orphan")
    documents = relationship("DocumentRecord", back_populates="case", cascade="all, delete-orphan")
    instances = relationship("InstanceRecord", back_populates="case", cascade="all, delete-orphan")
    judges = relationship("JudgeRecord", back_populates="case", cascade="all, delete-orphan")

    # Composite indexes for dashboard performance at scale
    __table_args__ = (
        Index("idx_cases_status_category", "status", "category"),
    )

    def __repr__(self):
        return f"<CaseRecord(id={self.id}, case_number={self.case_number}, status={self.status})>"


class ParticipantRecord(Base):
    """Participant in a case (plaintiff, defendant, third party)."""
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    address = Column(Text, nullable=True)
    inn = Column(String, nullable=True, index=True)
    ogrn = Column(String, nullable=True)
    postal_code = Column(String, nullable=True)

    cases = relationship("CaseParticipantLink", back_populates="participant", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ParticipantRecord(name={self.name}, inn={self.inn})>"


class CaseParticipantLink(Base):
    """Association table linking cases to participants with role."""
    __tablename__ = "case_participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)  # 'plaintiff', 'defendant', 'third_party', 'other_party'

    case = relationship("CaseRecord", back_populates="participants")
    participant = relationship("ParticipantRecord", back_populates="cases")


class DocumentRecord(Base):
    """Document associated with a case."""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    instance_id = Column(Integer, ForeignKey("instances.id", ondelete="CASCADE"), nullable=True, index=True)
    doc_id = Column(String, nullable=True)
    filename = Column(String, nullable=True)
    url = Column(String, nullable=True)
    date = Column(String, nullable=True)
    doc_type = Column(String, nullable=True)
    priority = Column(String, nullable=True)          # high/medium/low/uncategorized
    publish_date = Column(String, nullable=True)
    local_path = Column(String, nullable=True)         # Legacy: local filesystem path
    storage_key = Column(String(512), nullable=True)   # MinIO/S3 object key
    extracted_text = Column(Text, nullable=True)

    case = relationship("CaseRecord", back_populates="documents")
    instance = relationship("InstanceRecord", back_populates="documents")

    def __repr__(self):
        return f"<DocumentRecord(filename={self.filename}, priority={self.priority})>"


class InstanceRecord(Base):
    """Court instance record (from chronology header)."""
    __tablename__ = "instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    court_name = Column(String, nullable=False)
    instance_level = Column(String, nullable=True)
    case_number = Column(String, nullable=True)
    incoming_number = Column(String, nullable=True)
    date = Column(String, nullable=True)
    result_text = Column(Text, nullable=True)
    result_pdf_url = Column(String, nullable=True)

    case = relationship("CaseRecord", back_populates="instances")
    documents = relationship("DocumentRecord", back_populates="instance", cascade="all, delete-orphan")
    updates = relationship("InstanceUpdateRecord", back_populates="instance", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<InstanceRecord(court={self.court_name}, level={self.instance_level})>"


class InstanceUpdateRecord(Base):
    """Single chronology entry within a court instance."""
    __tablename__ = "instance_updates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(String, nullable=True)
    update_type = Column(String, nullable=True)        # "Определение", "Письмо", "Жалоба"
    subject = Column(String, nullable=True)            # Who filed / judge name
    content = Column(Text, nullable=True)              # Result text
    pdf_url = Column(String, nullable=True)
    pdf_publish_date = Column(String, nullable=True)
    additional_info = Column(Text, nullable=True)      # Barcode, claim amount, etc.
    judge_panel = Column(String, nullable=True)
    reporting_judge = Column(String, nullable=True)

    instance = relationship("InstanceRecord", back_populates="updates")

    def __repr__(self):
        return f"<InstanceUpdateRecord(date={self.date}, type={self.update_type})>"


class JudgeRecord(Base):
    """Judge associated with a case."""
    __tablename__ = "judges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)

    case = relationship("CaseRecord", back_populates="judges")

    def __repr__(self):
        return f"<JudgeRecord(name={self.name})>"


class ScrapeEventRecord(Base):
    """One parallel-scrape attempt (per judge / worker) for live dashboard metrics."""

    __tablename__ = "scrape_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=True, index=True)  # groups events by scrape_parallel run
    judge_name = Column(String, nullable=False, index=True)
    worker_id = Column(String, nullable=False, index=True)   # Changed to String for VPS worker IDs
    proxy_port = Column(Integer, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True)
    cases_collected = Column(Integer, default=0)
    status = Column(String, nullable=False, index=True)  # running | success | no_match | error | blocked
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_scrape_events_session_started", "session_id", "started_at"),
    )

    def __repr__(self):
        return f"<ScrapeEventRecord(judge={self.judge_name}, status={self.status})>"


class JudgeProgressRecord(Base):
    """Per-judge scraping progress for cross-run resume and job claiming."""

    __tablename__ = "judge_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    judge_name = Column(String, nullable=False, unique=True, index=True)
    court = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending|collecting|enriching|completed|failed
    cases_collected = Column(Integer, default=0)
    total_count_at_start = Column(Integer, default=0)
    max_cases = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Distributed worker support
    claimed_by = Column(String(64), nullable=True)     # Worker ID that claimed this judge
    heartbeat = Column(DateTime, nullable=True)        # Last heartbeat from the claiming worker
    retry_count = Column(Integer, default=0)           # Number of times this judge was reclaimed

    __table_args__ = (
        Index("idx_judge_progress_status_claimed", "status", "claimed_by"),
    )

    def __repr__(self):
        return f"<JudgeProgress({self.judge_name}, {self.status}, {self.cases_collected}/{self.max_cases})>"


class ScrapeMetaRecord(Base):
    """Single-row table for global scrape dashboard metadata."""

    __tablename__ = "scrape_meta"

    id = Column(Integer, primary_key=True, default=1)
    throughput_reset_at = Column(DateTime, nullable=True)  # only count events after this


class WorkerStatusRecord(Base):
    """Track VPS fleet health and IP assignment."""

    __tablename__ = "worker_status"

    id = Column(String(64), primary_key=True)            # e.g., 'vps-tw-01-w3' (VPS + worker slot)
    vps_id = Column(String(64), nullable=True)           # Logical VPS group (e.g., 'vps-tw-01')
    ip_address = Column(String(45), nullable=True)       # Public IPv4 assigned to this worker
    provider = Column(String(32), nullable=True)         # 'timeweb'
    vm_id = Column(String(128), nullable=True)           # Timeweb Cloud server ID
    status = Column(String(20), default="active")        # active, blocked, offline, rotating
    current_judge = Column(String(256), nullable=True)   # Judge currently being scraped
    proxy_port = Column(Integer, nullable=True)          # Local proxy port for this IP
    last_heartbeat = Column(DateTime, nullable=True)
    registered_at = Column(DateTime, default=datetime.utcnow)
    blocked_at = Column(DateTime, nullable=True)
    total_cases_scraped = Column(Integer, default=0)
    total_judges_completed = Column(Integer, default=0)

    __table_args__ = (
        Index("idx_worker_status_status", "status"),
    )

    def __repr__(self):
        return f"<WorkerStatus(id={self.id}, ip={self.ip_address}, status={self.status})>"


# --- Database connection setup ---

_engine = None
_SessionFactory = None


def _build_engine_url(db_path: Optional[str] = None) -> str:
    """
    Determine the database URL.

    Priority:
    1. DATABASE_URL environment variable (for production PostgreSQL)
    2. db_path argument (for local SQLite)
    3. Default SQLite path
    """
    # Check for DATABASE_URL first (production PostgreSQL)
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return database_url

    # Fall back to SQLite
    if db_path == ":memory:":
        return "sqlite:///:memory:"

    path = db_path or "data/arbitr.db"
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def init_db(db_path: str = "data/arbitr.db") -> None:
    """
    Initialize the database connection and create tables.

    For PostgreSQL: set DATABASE_URL env var (e.g., postgresql+psycopg2://user:pass@host:5432/arbitr).
    For SQLite: pass db_path (default: data/arbitr.db).
    Use ":memory:" for in-memory SQLite database (testing).
    """
    global _engine, _SessionFactory

    url = _build_engine_url(db_path)
    is_postgres = url.startswith("postgresql")

    engine_kwargs = {"echo": False}

    if is_postgres:
        # PostgreSQL-specific: connection pooling optimized for concurrent workers
        engine_kwargs.update({
            "pool_pre_ping": True,      # Detect stale connections
            "pool_size": 10,            # Base pool size
            "max_overflow": 20,         # Extra connections under load
            "pool_recycle": 1800,       # Recycle connections every 30 min
        })

    _engine = create_engine(url, **engine_kwargs)
    Base.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(bind=_engine)


def get_session() -> Session:
    """
    Get a new database session.

    Returns:
        SQLAlchemy Session

    Raises:
        RuntimeError: If database not initialized
    """
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionFactory()


def get_engine():
    """Get the current engine (useful for testing)."""
    return _engine


def is_postgres() -> bool:
    """Check if the current engine is connected to PostgreSQL."""
    if _engine is None:
        return False
    return _engine.dialect.name == "postgresql"
