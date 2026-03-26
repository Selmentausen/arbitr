"""
SQLAlchemy database models and connection setup for SQLite storage.

Tables:
- cases: Main case data (mirrors Case pydantic model)
- participants: Case participants (plaintiffs, defendants, third parties)
- documents: Case documents (PDFs, etc.)
- instances: Court instances (chronology)
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker


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
    plaintiff = Column(String, nullable=False)
    defendant = Column(String, nullable=False)
    filing_date = Column(DateTime, nullable=True)
    case_url = Column(String, nullable=True)
    is_simple_justice = Column(Boolean, default=False)

    # Filtering / scoring
    category = Column(String, nullable=True, index=True)
    relevance_score = Column(Float, default=0.0)
    status = Column(String, default="insufficient_info", index=True)

    # Extracted data (stored as JSON string)
    extracted_data_json = Column(Text, default="{}")
    aggregated_metrics_json = Column(Text, default="{}")

    # Raw content
    raw_html = Column(Text, nullable=True)
    pdf_texts_json = Column(Text, default="[]")

    # Scraping state
    case_page_scraped = Column(Boolean, default=False)
    documents_scraped = Column(Boolean, default=False)
    scraped_at = Column(DateTime, nullable=True)

    # Review state
    reviewed = Column(Boolean, default=False, index=True)
    review_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    participants = relationship("ParticipantRecord", back_populates="case", cascade="all, delete-orphan")
    documents = relationship("DocumentRecord", back_populates="case", cascade="all, delete-orphan")
    instances = relationship("InstanceRecord", back_populates="case", cascade="all, delete-orphan")
    judges = relationship("JudgeRecord", back_populates="case", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<CaseRecord(id={self.id}, case_number={self.case_number}, status={self.status})>"


class ParticipantRecord(Base):
    """Participant in a case (plaintiff, defendant, third party)."""
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)  # 'plaintiff', 'defendant', 'third_party', 'other'
    name = Column(String, nullable=False)
    address = Column(Text, nullable=True)
    inn = Column(String, nullable=True)
    ogrn = Column(String, nullable=True)

    case = relationship("CaseRecord", back_populates="participants")

    def __repr__(self):
        return f"<ParticipantRecord(name={self.name}, role={self.role})>"


class DocumentRecord(Base):
    """Document associated with a case."""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    doc_id = Column(String, nullable=True)  # ID from kad.arbitr.ru
    filename = Column(String, nullable=True)
    url = Column(String, nullable=True)
    date = Column(String, nullable=True)
    doc_type = Column(String, nullable=True)
    local_path = Column(String, nullable=True)  # Path to downloaded file
    extracted_text = Column(Text, nullable=True)  # Extracted PDF text

    case = relationship("CaseRecord", back_populates="documents")

    def __repr__(self):
        return f"<DocumentRecord(filename={self.filename}, type={self.doc_type})>"


class InstanceRecord(Base):
    """Court instance record (from chronology)."""
    __tablename__ = "instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    court_name = Column(String, nullable=False)
    case_number = Column(String, nullable=True)
    incoming_number = Column(String, nullable=True)
    date = Column(String, nullable=True)

    case = relationship("CaseRecord", back_populates="instances")

    def __repr__(self):
        return f"<InstanceRecord(court={self.court_name})>"


class JudgeRecord(Base):
    """Judge associated with a case."""
    __tablename__ = "judges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)

    case = relationship("CaseRecord", back_populates="judges")

    def __repr__(self):
        return f"<JudgeRecord(name={self.name})>"


# --- Database connection setup ---

_engine = None
_SessionFactory = None


def init_db(db_path: str = "data/arbitr.db") -> None:
    """
    Initialize the database connection and create tables.

    Args:
        db_path: Path to SQLite database file.
                 Use ":memory:" for in-memory database (testing).
    """
    global _engine, _SessionFactory

    if db_path == ":memory:":
        url = "sqlite:///:memory:"
    else:
        from pathlib import Path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"

    _engine = create_engine(url, echo=False)
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
