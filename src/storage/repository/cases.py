"""Case CRUD repository — save, retrieve, search, delete, export."""
import csv
import io
import json
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, subqueryload

from src.models.case import (
    Case,
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
)
from src.storage.repository.base import BaseRepository, _serialize_json, _deserialize_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Pydantic ↔ SQLAlchemy conversion helpers
# ------------------------------------------------------------------

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
        extracted_data_json=_serialize_json(case.extracted_data),
        aggregated_metrics_json=_serialize_json(case.aggregated_metrics),
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

    # Instances, documents, updates
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
                    storage_key=doc.storage_key,
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
    participants: Dict[str, List[CaseParticipant]] = {}
    plaintiff_names = []
    defendant_names = []

    for link in record.participants:
        role = link.role
        p = link.participant
        participants.setdefault(role, []).append(
            CaseParticipant(name=p.name, address=p.address, inn=p.inn, ogrn=p.ogrn, role=role)
        )
        if role in ("plaintiff", "plaintiffs"):
            plaintiff_names.append(p.name)
        elif role in ("defendant", "defendants"):
            defendant_names.append(p.name)

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
                storage_key=d.storage_key,
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

    judges = [j.name for j in record.judges]

    pdf_texts = (
        json.loads(record.pdf_texts_json or "[]")
        if isinstance(record.pdf_texts_json, str)
        else (record.pdf_texts_json or [])
    )

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
        extracted_data=_deserialize_json(record.extracted_data_json, {}),
        aggregated_metrics=_deserialize_json(record.aggregated_metrics_json, {}),
        raw_html=record.raw_html,
        pdf_texts=pdf_texts,
    )


def _build_participant_links(session: Session, participants_dict: dict) -> List[CaseParticipantLink]:
    """Deduplicate participants and build association links for a CaseRecord."""
    links = []
    for role, participants in participants_dict.items():
        for p in participants:
            participant_record = (
                session.query(ParticipantRecord)
                .filter(ParticipantRecord.name == p.name)
                .first()
            )
            if not participant_record:
                participant_record = ParticipantRecord(
                    name=p.name, address=p.address, inn=p.inn, ogrn=p.ogrn
                )
                session.add(participant_record)
                session.flush()
            links.append(CaseParticipantLink(role=role, participant=participant_record))
    return links


# ------------------------------------------------------------------
# CaseRepository
# ------------------------------------------------------------------

class CaseRepository(BaseRepository):
    """CRUD operations for cases."""

    # --- Create / Update ---

    def save_case(self, case: Case) -> CaseRecord:
        """Save or update a case in the database."""
        existing = self.session.get(CaseRecord, case.id)
        if existing:
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
            existing.extracted_data_json = _serialize_json(case.extracted_data)
            existing.aggregated_metrics_json = _serialize_json(case.aggregated_metrics)
            existing.raw_html = case.raw_html
            existing.pdf_texts_json = json.dumps(case.pdf_texts, ensure_ascii=False)
            existing.case_page_scraped = case.case_page_scraped
            existing.last_scraped_at = case.last_scraped_at
            existing.updated_at = datetime.utcnow()

            existing.judges.clear()
            for judge_name in case.judges:
                existing.judges.append(JudgeRecord(name=judge_name))

            existing.participants.clear()
            existing.participants.extend(_build_participant_links(self.session, case.participants))

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
                    existing.documents.append(
                        DocumentRecord(
                            doc_id=doc.id,
                            filename=doc.filename,
                            url=doc.url,
                            doc_type=doc.type,
                            date=doc.date,
                            priority=doc.priority,
                            publish_date=doc.publish_date,
                            extracted_text=doc.extracted_text,
                            storage_key=doc.storage_key,
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

            self.session.commit()
            logger.debug("Updated case %s", case.id)
            return existing
        else:
            record = _case_to_record(case, self.session)
            self.session.add(record)
            self.session.commit()
            logger.debug("Saved new case %s", case.id)
            return record

    def save_cases(self, cases: List[Case]) -> int:
        """Save multiple cases."""
        count = 0
        for case in cases:
            try:
                self.save_case(case)
                count += 1
            except Exception as e:
                logger.error("Failed to save case %s: %s", case.id, e)
                self.session.rollback()
        logger.info("Saved %d/%d cases", count, len(cases))
        return count

    # --- Read ---

    def get_case(self, case_id: str) -> Optional[Case]:
        """Get a single case by ID."""
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
        """Get cases with pagination and filters."""
        query = self.session.query(CaseRecord)
        if status is not None:
            query = query.filter(CaseRecord.status == status)
        if category is not None:
            query = query.filter(CaseRecord.category == category)
        if reviewed is not None:
            query = query.filter(CaseRecord.reviewed == reviewed)

        total = query.count()
        sort_column = getattr(CaseRecord, sort_by, CaseRecord.created_at)
        query = query.order_by(sort_column.desc() if sort_desc else sort_column.asc())
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        query = query.options(
            subqueryload(CaseRecord.participants).joinedload(CaseParticipantLink.participant),
            subqueryload(CaseRecord.documents),
            subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.updates),
            subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.documents),
            subqueryload(CaseRecord.judges),
        )
        records = query.all()
        seen = set()
        unique = []
        for r in records:
            if r.id not in seen:
                seen.add(r.id)
                unique.append(r)
        return [_record_to_case(r) for r in unique], total

    def search_cases(self, query: str) -> List[Case]:
        """Search cases by keyword in plaintiff, defendant, case_number, court."""
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
        seen = set()
        unique = []
        for r in records:
            if r.id not in seen:
                seen.add(r.id)
                unique.append(r)
        return [_record_to_case(r) for r in unique]

    # --- Delete ---

    def delete_case(self, case_id: str) -> bool:
        """Delete a case by ID."""
        record = self.session.get(CaseRecord, case_id)
        if record is None:
            return False
        self.session.delete(record)
        self.session.commit()
        logger.debug("Deleted case %s", case_id)
        return True

    # --- Export ---

    def export_cases(
        self,
        format: str = "json",
        status: Optional[str] = None,
        category: Optional[str] = None,
        reviewed: Optional[bool] = None,
    ) -> str:
        """Export cases as JSON or CSV string."""
        cases, _ = self.get_all_cases(
            page=1, page_size=100000,
            status=status, category=category, reviewed=reviewed,
        )
        if format == "json":
            data = [case.model_dump(mode="json", exclude={"raw_html"}) for case in cases]
            return json.dumps(data, ensure_ascii=False, indent=2, default=str)
        elif format == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
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
