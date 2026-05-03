"""
CRUD repository for case data.

Handles conversion between Pydantic models and SQLAlchemy records,
provides search/filter/export capabilities.
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from sqlalchemy import or_, func
from sqlalchemy.orm import Session, joinedload, subqueryload

from src.models.case import (
    Case,
    CaseBase,
    CaseDocument,
    CaseInstance,
    CaseParticipant,
    InstanceUpdate,
    StatusEnum,
)
from src.storage.database import (
    CaseRecord,
    DocumentRecord,
    InstanceRecord,
    InstanceUpdateRecord,
    JudgeRecord,
    ParticipantRecord,
    CaseParticipantLink,
    get_session,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _case_to_record(case: Case, session: Session) -> CaseRecord:
    """Convert a Pydantic Case to a SQLAlchemy CaseRecord."""
    record = CaseRecord(
        id=case.id,
        case_number=case.case_number,
        court=case.court,
        filing_date=case.filing_date,
        case_url=case.case_url,
        case_type=case.case_type,
        current_instance=case.current_instance,
        is_simple_justice=case.is_simple_justice,
        case_status_text=case.case_status_text,
        case_category_text=case.case_category_text,
        claim_amount=case.claim_amount,
        category=case.category,
        relevance_score=case.relevance_score,
        status=case.status.value if isinstance(case.status, StatusEnum) else case.status,
        extracted_data_json=json.dumps(case.extracted_data, ensure_ascii=False),
        aggregated_metrics_json=json.dumps(case.aggregated_metrics, ensure_ascii=False),
        raw_html=case.raw_html,
        pdf_texts_json=json.dumps(case.pdf_texts, ensure_ascii=False),
        case_page_scraped=case.case_page_scraped,
        scraped_at=case.scraped_at or datetime.utcnow(),
        last_scraped_at=case.last_scraped_at,
    )

    # Judges
    for judge_name in case.judges:
        record.judges.append(JudgeRecord(name=judge_name))

    # Participants
    record.participants = _build_participant_links(session, case.participants)

    # Instances, their documents, and their updates
    for inst in case.instances:
        instance_record = InstanceRecord(
            court_name=inst.court_name,
            instance_level=inst.instance_level,
            case_number=inst.case_number,
            incoming_number=inst.incoming_number,
            date=inst.date,
            result_text=inst.result_text,
            result_pdf_url=inst.result_pdf_url,
        )
        record.instances.append(instance_record)

        for doc in inst.documents:
            record.documents.append(
                DocumentRecord(
                    doc_id=doc.id,
                    filename=doc.filename,
                    url=doc.url,
                    doc_type=doc.type,
                    date=doc.date,
                    priority=doc.priority,
                    publish_date=doc.publish_date,
                    extracted_text=doc.extracted_text,
                    instance=instance_record,
                )
            )

        for upd in inst.updates:
            instance_record.updates.append(
                InstanceUpdateRecord(
                    date=upd.date,
                    update_type=upd.update_type,
                    subject=upd.subject,
                    content=upd.content,
                    pdf_url=upd.pdf_url,
                    pdf_publish_date=upd.pdf_publish_date,
                    additional_info=upd.additional_info,
                    judge_panel=upd.judge_panel,
                    reporting_judge=upd.reporting_judge,
                )
            )

    return record


def _record_to_case(record: CaseRecord) -> Case:
    """Convert a SQLAlchemy CaseRecord back to a Pydantic Case."""
    # Build participants dict
    participants: Dict[str, List[CaseParticipant]] = {}
    plaintiff_names = []
    defendant_names = []

    for link in record.participants:
        role = link.role
        p = link.participant

        participants.setdefault(role, []).append(
            CaseParticipant(name=p.name, address=p.address, inn=p.inn, ogrn=p.ogrn, role=role)
        )

        if role == "plaintiff":
            plaintiff_names.append(p.name)
        elif role == "defendant":
            defendant_names.append(p.name)

    # Build instances with documents and updates
    instances = []
    for inst in record.instances:
        inst_docs = [
            CaseDocument(
                id=d.doc_id,
                filename=d.filename,
                url=d.url,
                type=d.doc_type,
                date=d.date,
                priority=d.priority,
                publish_date=d.publish_date,
                extracted_text=d.extracted_text,
            )
            for d in inst.documents
        ]

        inst_updates = [
            InstanceUpdate(
                date=u.date,
                update_type=u.update_type,
                subject=u.subject,
                content=u.content,
                pdf_url=u.pdf_url,
                pdf_publish_date=u.pdf_publish_date,
                additional_info=u.additional_info,
                judge_panel=u.judge_panel,
                reporting_judge=u.reporting_judge,
            )
            for u in inst.updates
        ]

        instances.append(
            CaseInstance(
                court_name=inst.court_name,
                instance_level=inst.instance_level,
                case_number=inst.case_number,
                incoming_number=inst.incoming_number,
                date=inst.date,
                result_text=inst.result_text,
                result_pdf_url=inst.result_pdf_url,
                updates=inst_updates,
                documents=inst_docs,
            )
        )

    # Build judges list
    judges = [j.name for j in record.judges]

    return Case(
        id=record.id,
        case_number=record.case_number,
        court=record.court,
        case_type=record.case_type,
        current_instance=record.current_instance,
        plaintiff=", ".join(plaintiff_names) if plaintiff_names else "",
        defendant=", ".join(defendant_names) if defendant_names else "",
        filing_date=record.filing_date,
        case_url=record.case_url,
        is_simple_justice=record.is_simple_justice,
        case_status_text=record.case_status_text,
        case_category_text=record.case_category_text,
        claim_amount=record.claim_amount,
        case_page_scraped=record.case_page_scraped or False,
        last_scraped_at=record.last_scraped_at,
        judges=judges,
        participants=participants,
        instances=instances,
        category=record.category,
        relevance_score=record.relevance_score,
        status=StatusEnum(record.status) if record.status else StatusEnum.INSUFFICIENT_INFO,
        extracted_data=json.loads(record.extracted_data_json or "{}"),
        aggregated_metrics=json.loads(record.aggregated_metrics_json or "{}"),
        raw_html=record.raw_html,
        pdf_texts=json.loads(record.pdf_texts_json or "[]"),
    )


def _build_participant_links(session: Session, participants_dict: dict) -> list[CaseParticipantLink]:
    """
    Helper function to deduploicate and build the association links for a CaseRecord.
    """
    links = []
    for role, participants in participants_dict.items():
        for p in participants:
            participant_record = session.query(ParticipantRecord).filter(ParticipantRecord.name == p.name).first()
            if not participant_record:
                participant_record = ParticipantRecord(
                    name=p.name,
                    address=p.address,
                    inn=p.inn,
                    ogrn=p.ogrn
                )
                session.add(participant_record)
                session.flush()
            links.append(CaseParticipantLink(role=role, participant=participant_record))
    return links


class CaseRepository:
    """Repository for case CRUD operations."""

    def __init__(self, session: Optional[Session] = None):
        """
        Initialize repository.

        Args:
            session: Optional SQLAlchemy session. If None, creates one via get_session().
        """
        self._session = session
        self._owns_session = session is None

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = get_session()
        return self._session

    def close(self):
        """Close the session if we own it."""
        if self._owns_session and self._session is not None:
            self._session.close()

    # --- Create / Update ---

    def save_case(self, case: Case) -> CaseRecord:
        """
        Save or update a case in the database.

        Args:
            case: Pydantic Case object

        Returns:
            Saved CaseRecord
        """
        existing = self.session.get(CaseRecord, case.id)

        if existing:
            # Update existing record
            existing.case_number = case.case_number
            existing.court = case.court
            existing.filing_date = case.filing_date
            existing.case_url = case.case_url
            existing.case_type = case.case_type
            existing.current_instance = case.current_instance
            existing.is_simple_justice = case.is_simple_justice
            existing.case_status_text = case.case_status_text
            existing.case_category_text = case.case_category_text
            existing.claim_amount = case.claim_amount
            existing.category = case.category
            existing.relevance_score = case.relevance_score
            existing.status = case.status.value if isinstance(case.status, StatusEnum) else case.status
            existing.extracted_data_json = json.dumps(case.extracted_data, ensure_ascii=False)
            existing.aggregated_metrics_json = json.dumps(case.aggregated_metrics, ensure_ascii=False)
            existing.raw_html = case.raw_html
            existing.pdf_texts_json = json.dumps(case.pdf_texts, ensure_ascii=False)
            existing.case_page_scraped = case.case_page_scraped
            existing.last_scraped_at = case.last_scraped_at
            existing.updated_at = datetime.utcnow()

            # Update judges
            existing.judges.clear()
            for judge_name in case.judges:
                existing.judges.append(JudgeRecord(name=judge_name))

            # Update participants
            existing.participants.clear()
            existing.participants.extend(_build_participant_links(self.session, case.participants))

            # Update instances, documents, and updates
            existing.instances.clear()
            existing.documents.clear()

            for inst in case.instances:
                instance_record = InstanceRecord(
                    court_name=inst.court_name,
                    instance_level=inst.instance_level,
                    case_number=inst.case_number,
                    incoming_number=inst.incoming_number,
                    date=inst.date,
                    result_text=inst.result_text,
                    result_pdf_url=inst.result_pdf_url,
                )
                existing.instances.append(instance_record)

                for doc in inst.documents:
                    doc_record = DocumentRecord(
                        doc_id=doc.id,
                        filename=doc.filename,
                        url=doc.url,
                        doc_type=doc.type,
                        date=doc.date,
                        priority=doc.priority,
                        publish_date=doc.publish_date,
                        extracted_text=doc.extracted_text,
                        instance=instance_record,
                    )
                    existing.documents.append(doc_record)

                for upd in inst.updates:
                    instance_record.updates.append(
                        InstanceUpdateRecord(
                            date=upd.date,
                            update_type=upd.update_type,
                            subject=upd.subject,
                            content=upd.content,
                            pdf_url=upd.pdf_url,
                            pdf_publish_date=upd.pdf_publish_date,
                            additional_info=upd.additional_info,
                            judge_panel=upd.judge_panel,
                            reporting_judge=upd.reporting_judge,
                        )
                    )

            self.session.commit()
            logger.debug(f"Updated case {case.id}")
            return existing
        else:
            record = _case_to_record(case, self.session)
            self.session.add(record)
            self.session.commit()
            logger.debug(f"Saved new case {case.id}")
            return record

    def save_cases(self, cases: List[Case]) -> int:
        """
        Save multiple cases.

        Args:
            cases: List of Case objects

        Returns:
            Number of cases saved
        """
        count = 0
        for case in cases:
            try:
                self.save_case(case)
                count += 1
            except Exception as e:
                logger.error(f"Failed to save case {case.id}: {e}")
                self.session.rollback()
        logger.info(f"Saved {count}/{len(cases)} cases")
        return count

    def save_case_base(self, case_base: CaseBase) -> CaseRecord:
        """
        Save a CaseBase (from search results) — converts to Case first.

        Args:
            case_base: CaseBase from scraper

        Returns:
            Saved CaseRecord
        """
        case = Case(**case_base.model_dump())
        if case.plaintiff and case.plaintiff != "Unknown":
            case.participants["plaintiff"] = [CaseParticipant(name=case.plaintiff)]
        if case.defendant and case.defendant != "Unknown":
            for def_name in case.defendant.split(", "):
                if def_name.strip():
                    case.participants.setdefault("defendant", []).append(CaseParticipant(name=def_name.strip()))
        return self.save_case(case)

    # --- Read ---

    def get_case(self, case_id: str) -> Optional[Case]:
        """
        Get a single case by ID.

        Args:
            case_id: Case UUID

        Returns:
            Case object or None
        """
        record = (
            self.session.query(CaseRecord)
            .options(
                subqueryload(CaseRecord.participants).joinedload(CaseParticipantLink.participant),
                subqueryload(CaseRecord.documents),
                subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.updates),
                subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.documents),
                subqueryload(CaseRecord.judges),
            )
            .filter(CaseRecord.id == case_id)
            .first()
        )
        if record is None:
            return None
        return _record_to_case(record)

    def get_all_cases(
        self,
        page: int = 1,
        page_size: int = 25,
        status: Optional[str] = None,
        category: Optional[str] = None,
        reviewed: Optional[bool] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> Tuple[List[Case], int]:
        """
        Get cases with pagination and filters.

        Args:
            page: Page number (1-indexed)
            page_size: Results per page
            status: Filter by status
            category: Filter by category
            reviewed: Filter by review state
            sort_by: Column to sort by
            sort_desc: Sort descending

        Returns:
            Tuple of (list of cases, total count)
        """
        query = self.session.query(CaseRecord)

        # Apply filters
        if status is not None:
            query = query.filter(CaseRecord.status == status)
        if category is not None:
            query = query.filter(CaseRecord.category == category)
        if reviewed is not None:
            query = query.filter(CaseRecord.reviewed == reviewed)

        # Count on the lightweight query (no joins)
        total = query.count()

        # Sort
        sort_column = getattr(CaseRecord, sort_by, CaseRecord.created_at)
        if sort_desc:
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        # Paginate
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        # Now apply eager loading only to the paginated subset
        query = query.options(
            subqueryload(CaseRecord.participants).joinedload(CaseParticipantLink.participant),
            subqueryload(CaseRecord.documents),
            subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.updates),
            subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.documents),
            subqueryload(CaseRecord.judges),
        )

        records = query.all()

        # Deduplicate (shouldn't be needed with subqueryload, but safe)
        seen = set()
        unique_records = []
        for r in records:
            if r.id not in seen:
                seen.add(r.id)
                unique_records.append(r)

        cases = [_record_to_case(r) for r in unique_records]
        return cases, total

    def search_cases(self, query: str) -> List[Case]:
        """
        Search cases by keyword in plaintiff, defendant, case_number, court.

        Args:
            query: Search string

        Returns:
            List of matching cases
        """
        pattern = f"%{query}%"
        records = (
            self.session.query(CaseRecord)
            .join(CaseRecord.participants)
            .join(CaseParticipantLink.participant)
            .options(
                joinedload(CaseRecord.participants).joinedload(CaseParticipantLink.participant),
                joinedload(CaseRecord.documents),
                joinedload(CaseRecord.instances),
                joinedload(CaseRecord.judges),
            )
            .filter(
                or_(
                    ParticipantRecord.name.ilike(pattern),
                    CaseRecord.case_number.ilike(pattern),
                    CaseRecord.court.ilike(pattern),
                )
            )
            .all()
        )

        # Deduplicate
        seen = set()
        unique = []
        for r in records:
            if r.id not in seen:
                seen.add(r.id)
                unique.append(r)

        return [_record_to_case(r) for r in unique]

    # --- Review ---

    def mark_reviewed(self, case_id: str, reviewed: bool = True, notes: Optional[str] = None) -> bool:
        """
        Mark a case as reviewed.

        Args:
            case_id: Case UUID
            reviewed: Review state
            notes: Optional review notes

        Returns:
            True if case was found and updated
        """
        record = self.session.get(CaseRecord, case_id)
        if record is None:
            return False

        record.reviewed = reviewed
        record.review_notes = notes
        record.reviewed_at = datetime.utcnow() if reviewed else None
        self.session.commit()
        logger.debug(f"Marked case {case_id} as reviewed={reviewed}")
        return True

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """
        Get aggregate statistics.

        Returns:
            Dictionary with counts by status, category, review state, etc.
        """
        total = self.session.query(func.count(CaseRecord.id)).scalar() or 0

        # By status
        status_counts = dict(
            self.session.query(CaseRecord.status, func.count(CaseRecord.id))
            .group_by(CaseRecord.status)
            .all()
        )

        # By category
        category_counts = dict(
            self.session.query(CaseRecord.category, func.count(CaseRecord.id))
            .group_by(CaseRecord.category)
            .all()
        )

        # Review state
        reviewed_count = (
            self.session.query(func.count(CaseRecord.id))
            .filter(CaseRecord.reviewed == True)
            .scalar() or 0
        )

        # Score stats
        avg_score = (
            self.session.query(func.avg(CaseRecord.relevance_score)).scalar() or 0.0
        )

        return {
            "total_cases": total,
            "by_status": status_counts,
            "by_category": category_counts,
            "reviewed": reviewed_count,
            "not_reviewed": total - reviewed_count,
            "avg_relevance_score": round(float(avg_score), 2),
        }

    # --- Export ---

    def export_cases(
        self,
        format: str = "json",
        status: Optional[str] = None,
        category: Optional[str] = None,
        reviewed: Optional[bool] = None,
    ) -> str:
        """
        Export cases as JSON or CSV string.

        Args:
            format: 'json' or 'csv'
            status: Optional status filter
            category: Optional category filter
            reviewed: Optional review filter

        Returns:
            Formatted string (JSON or CSV)
        """
        cases, _ = self.get_all_cases(
            page=1,
            page_size=100000,
            status=status,
            category=category,
            reviewed=reviewed,
        )

        if format == "json":
            data = [case.model_dump(mode="json", exclude={"raw_html"}) for case in cases]
            return json.dumps(data, ensure_ascii=False, indent=2, default=str)

        elif format == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)

            # Header
            headers = [
                "case_number", "court", "plaintiff", "defendant",
                "category", "relevance_score", "status", "filing_date",
                "reviewed", "case_url",
            ]
            writer.writerow(headers)

            for case in cases:
                writer.writerow([
                    case.case_number,
                    case.court,
                    case.plaintiff,
                    case.defendant,
                    case.category or "",
                    case.relevance_score,
                    case.status.value if isinstance(case.status, StatusEnum) else case.status,
                    case.filing_date.isoformat() if case.filing_date else "",
                    "Yes" if False else "No",  # reviewed flag
                    case.case_url or "",
                ])

            return output.getvalue()

        else:
            raise ValueError(f"Unsupported export format: {format}")

    # --- Delete ---

    def delete_case(self, case_id: str) -> bool:
        """Delete a case by ID."""
        record = self.session.get(CaseRecord, case_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.commit()
        logger.debug(f"Deleted case {case_id}")
        return True
