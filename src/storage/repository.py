"""
CRUD repository for case data.

Handles conversion between Pydantic models and SQLAlchemy records,
provides search/filter/export capabilities.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta
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
    JudgeProgressRecord,
    JudgeRecord,
    ParticipantRecord,
    CaseParticipantLink,
    ScrapeEventRecord,
    ScrapeMetaRecord,
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

    def save_ml_review(
        self,
        case_id: str,
        verdict: str,
        correct_category: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """Save human review of ML classification."""
        record = self.session.get(CaseRecord, case_id)
        if record is None:
            return False

        extracted = json.loads(record.extracted_data_json or "{}")
        extracted["ml_review"] = {
            "verdict": verdict,
            "correct_category": correct_category,
            "notes": notes,
            "reviewed_at": datetime.utcnow().isoformat(),
        }
        record.extracted_data_json = json.dumps(extracted, ensure_ascii=False)
        record.updated_at = datetime.utcnow()
        self.session.commit()
        logger.debug("Saved ML review for case %s: %s", case_id, verdict)
        return True

    def list_cases_for_classification(
        self,
        limit: int = 100,
        force: bool = False,
        case_id: Optional[str] = None,
    ) -> List[Case]:
        """
        List cases eligible for ML classification.

        By default skips cases that already have ml_classification in extracted_data.
        """
        query = (
            self.session.query(CaseRecord)
            .options(
                subqueryload(CaseRecord.participants).joinedload(CaseParticipantLink.participant),
                subqueryload(CaseRecord.documents),
                subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.updates),
                subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.documents),
                subqueryload(CaseRecord.judges),
            )
            .order_by(CaseRecord.created_at.desc())
        )
        if case_id:
            query = query.filter(CaseRecord.id == case_id)

        scan_limit = limit if case_id else max(limit * 5, limit)
        records = query.limit(scan_limit).all()

        cases: List[Case] = []
        for record in records:
            case = _record_to_case(record)
            if force or "ml_classification" not in case.extracted_data:
                cases.append(case)
            if len(cases) >= limit:
                break
        return cases

    def get_ml_stats(self) -> Dict[str, Any]:
        """Aggregate counts for ML-classified cases (lightweight JSON scan)."""
        rows = (
            self.session.query(
                CaseRecord.id,
                CaseRecord.extracted_data_json,
                CaseRecord.category,
            )
            .filter(
                CaseRecord.extracted_data_json.isnot(None),
                CaseRecord.extracted_data_json.contains('"ml_classification"'),
            )
            .all()
        )

        by_ml_category: Dict[str, int] = {}
        by_verdict: Dict[str, int] = {"correct": 0, "wrong": 0, "unreviewed": 0}
        disagreements = 0

        for _id, extracted_json, keyword_category in rows:
            extracted = json.loads(extracted_json or "{}")
            ml = extracted.get("ml_classification") or {}
            primary = ml.get("primary_category") or "unknown"
            by_ml_category[primary] = by_ml_category.get(primary, 0) + 1

            review = extracted.get("ml_review") or {}
            verdict = review.get("verdict")
            if verdict == "correct":
                by_verdict["correct"] += 1
            elif verdict == "wrong":
                by_verdict["wrong"] += 1
            else:
                by_verdict["unreviewed"] += 1

            if keyword_category and primary and primary != keyword_category:
                disagreements += 1

        total_ml = len(rows)
        human_reviewed = by_verdict["correct"] + by_verdict["wrong"]
        return {
            "total_ml_classified": total_ml,
            "human_reviewed": human_reviewed,
            "by_verdict": by_verdict,
            "by_ml_category": by_ml_category,
            "disagreements": disagreements,
        }

    def get_ml_cases(
        self,
        page: int = 1,
        page_size: int = 20,
        human_review: Optional[str] = None,
        ml_review_verdict: Optional[str] = None,
        ml_category: Optional[str] = None,
        disagreement_only: bool = False,
        uncertainty: Optional[str] = None,
        sort_by: str = "ml_analyzed_at",
        sort_desc: bool = True,
        lite: bool = False,
    ) -> Tuple[List[Case], int]:
        """
        List cases that have ML classification, with ML-specific filters.

        Args:
            human_review: None=all, "reviewed"=has ml_review, "unreviewed"=no ml_review
            ml_review_verdict: "correct", "wrong", or None
            ml_category: filter by ML primary_category
            disagreement_only: ML primary != keyword category
            uncertainty: "low", "medium", "high"
            sort_by: ml_analyzed_at, ml_confidence, case_number, created_at
            lite: skip chronology (instance updates) when loading cases
        """
        load_options = [
            subqueryload(CaseRecord.participants).joinedload(CaseParticipantLink.participant),
            subqueryload(CaseRecord.judges),
        ]
        if lite:
            load_options.append(
                subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.documents)
            )
        else:
            load_options.extend([
                subqueryload(CaseRecord.documents),
                subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.updates),
                subqueryload(CaseRecord.instances).subqueryload(InstanceRecord.documents),
            ])

        query = (
            self.session.query(CaseRecord)
            .filter(
                CaseRecord.extracted_data_json.isnot(None),
                CaseRecord.extracted_data_json.contains('"ml_classification"'),
            )
            .options(*load_options)
        )

        if ml_review_verdict == "correct":
            query = query.filter(CaseRecord.extracted_data_json.contains('"verdict": "correct"'))
        elif ml_review_verdict == "wrong":
            query = query.filter(CaseRecord.extracted_data_json.contains('"verdict": "wrong"'))
        elif human_review == "reviewed":
            query = query.filter(CaseRecord.extracted_data_json.contains('"ml_review"'))
        elif human_review == "unreviewed":
            query = query.filter(~CaseRecord.extracted_data_json.contains('"ml_review"'))

        records = query.all()
        seen: set[str] = set()
        cases: List[Case] = []

        for record in records:
            if record.id in seen:
                continue
            seen.add(record.id)
            case = _record_to_case(record)
            ml = case.extracted_data.get("ml_classification") or {}
            review = case.extracted_data.get("ml_review") or {}

            if ml_category and ml.get("primary_category") != ml_category:
                continue
            if uncertainty and ml.get("uncertainty") != uncertainty:
                continue
            if disagreement_only:
                primary = ml.get("primary_category")
                if not primary or not case.category or primary == case.category:
                    continue
            if human_review == "unreviewed" and review.get("verdict"):
                continue
            if human_review == "reviewed" and not review.get("verdict"):
                continue

            cases.append(case)

        def _sort_key(c: Case):
            ml = c.extracted_data.get("ml_classification") or {}
            if sort_by == "ml_confidence":
                return ml.get("confidence") or 0.0
            if sort_by == "ml_analyzed_at":
                return ml.get("analyzed_at") or ""
            if sort_by == "case_number":
                return c.case_number or ""
            return c.created_at.isoformat() if getattr(c, "created_at", None) else ""

        cases.sort(key=_sort_key, reverse=sort_desc)
        total = len(cases)
        offset = (page - 1) * page_size
        return cases[offset : offset + page_size], total

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

    # --- Scrape events (parallel runner / live dashboard) ---

    def start_scrape_event(
        self,
        judge_name: str,
        worker_id: int,
        proxy_port: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> int:
        rec = ScrapeEventRecord(
            judge_name=judge_name,
            worker_id=worker_id,
            proxy_port=proxy_port,
            session_id=session_id,
            status="running",
            cases_collected=0,
        )
        self.session.add(rec)
        self.session.commit()
        self.session.refresh(rec)
        return rec.id

    def finish_scrape_event(
        self,
        event_id: int,
        cases_collected: int,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        rec = self.session.get(ScrapeEventRecord, event_id)
        if rec is None:
            return
        rec.finished_at = datetime.utcnow()
        rec.cases_collected = cases_collected
        rec.status = status
        rec.error_message = error
        self.session.commit()

    def mark_running_events_interrupted(self, reason: str = "Interrupted by user") -> int:
        """
        Finalize stale running events (e.g. process killed via Ctrl+C).
        """
        now = datetime.utcnow()
        rows = (
            self.session.query(ScrapeEventRecord)
            .filter(
                ScrapeEventRecord.status == "running",
                ScrapeEventRecord.finished_at.is_(None),
            )
            .all()
        )
        for r in rows:
            r.status = "interrupted"
            r.finished_at = now
            if not r.error_message:
                r.error_message = reason
        self.session.commit()
        return len(rows)

    def _get_scrape_meta(self) -> ScrapeMetaRecord:
        """Get or create the single scrape_meta row."""
        meta = self.session.get(ScrapeMetaRecord, 1)
        if meta is None:
            meta = ScrapeMetaRecord(id=1)
            self.session.add(meta)
            self.session.commit()
        return meta

    def reset_throughput(self) -> None:
        """Reset the 'overall' cases/hour counter by recording current timestamp."""
        meta = self._get_scrape_meta()
        meta.throughput_reset_at = datetime.utcnow()
        self.session.commit()

    def get_throughput(self) -> Dict[str, Any]:
        """
        Cases/hour estimates for the live dashboard.
        
        Returns:
            - cases_per_hour_overall: total cases / hours elapsed since last reset
            - cases_per_hour_latest_session: cases / hours for the most recent session_id
            - Existing window metrics (60m, 10m) for backward compat
        """
        now = datetime.utcnow()
        meta = self._get_scrape_meta()

        # --- Overall since reset ---
        reset_at = meta.throughput_reset_at
        overall_q = self.session.query(
            func.coalesce(func.sum(ScrapeEventRecord.cases_collected), 0)
        ).filter(
            ScrapeEventRecord.finished_at.isnot(None),
            ScrapeEventRecord.status == "success",
        )
        if reset_at:
            overall_q = overall_q.filter(ScrapeEventRecord.finished_at >= reset_at)
        total_cases_overall = int(overall_q.scalar() or 0)

        # Time span for overall rate
        if reset_at:
            hours_elapsed = max((now - reset_at).total_seconds() / 3600, 0.001)
        else:
            # No reset ever — use earliest event
            earliest = (
                self.session.query(func.min(ScrapeEventRecord.started_at))
                .filter(ScrapeEventRecord.status == "success")
                .scalar()
            )
            if earliest:
                hours_elapsed = max((now - earliest).total_seconds() / 3600, 0.001)
            else:
                hours_elapsed = 1.0
        cph_overall = total_cases_overall / hours_elapsed

        # --- Latest session ---
        latest_session_id = (
            self.session.query(ScrapeEventRecord.session_id)
            .filter(ScrapeEventRecord.session_id.isnot(None))
            .order_by(ScrapeEventRecord.started_at.desc())
            .limit(1)
            .scalar()
        )
        cph_session = 0.0
        session_cases = 0
        session_id_display = latest_session_id or "—"
        if latest_session_id:
            session_cases = int(
                self.session.query(
                    func.coalesce(func.sum(ScrapeEventRecord.cases_collected), 0)
                ).filter(
                    ScrapeEventRecord.session_id == latest_session_id,
                    ScrapeEventRecord.status == "success",
                ).scalar() or 0
            )
            session_start = (
                self.session.query(func.min(ScrapeEventRecord.started_at))
                .filter(ScrapeEventRecord.session_id == latest_session_id)
                .scalar()
            )
            session_end = (
                self.session.query(func.max(ScrapeEventRecord.finished_at))
                .filter(
                    ScrapeEventRecord.session_id == latest_session_id,
                    ScrapeEventRecord.finished_at.isnot(None),
                )
                .scalar()
            ) or now
            session_hours = max((session_end - session_start).total_seconds() / 3600, 0.001) if session_start else 1.0
            cph_session = session_cases / session_hours

        # --- Window metrics (backward compat) ---
        def _sum_cases_since(minutes: int, only_success: bool = True) -> int:
            since = now - timedelta(minutes=minutes)
            q = self.session.query(func.coalesce(func.sum(ScrapeEventRecord.cases_collected), 0)).filter(
                ScrapeEventRecord.finished_at.isnot(None),
                ScrapeEventRecord.finished_at >= since,
            )
            if only_success:
                q = q.filter(ScrapeEventRecord.status == "success")
            return int(q.scalar() or 0)

        cases_60 = _sum_cases_since(60)
        cases_10 = _sum_cases_since(10)
        cph_60 = cases_60
        cph_10 = cases_10 * 6.0 if cases_10 else 0.0

        active_workers = (
            self.session.query(func.count(ScrapeEventRecord.id))
            .filter(
                ScrapeEventRecord.status == "running",
                ScrapeEventRecord.finished_at.is_(None),
            )
            .scalar()
            or 0
        )

        since_24h = now - timedelta(hours=24)
        judges_done_24h = (
            self.session.query(func.count(ScrapeEventRecord.id))
            .filter(
                ScrapeEventRecord.finished_at.isnot(None),
                ScrapeEventRecord.finished_at >= since_24h,
            )
            .scalar()
            or 0
        )

        since_1h = now - timedelta(hours=1)
        by_status_rows = (
            self.session.query(ScrapeEventRecord.status, func.count(ScrapeEventRecord.id))
            .filter(
                ScrapeEventRecord.finished_at.isnot(None),
                ScrapeEventRecord.finished_at >= since_1h,
            )
            .group_by(ScrapeEventRecord.status)
            .all()
        )
        by_status = dict(by_status_rows)

        return {
            # New metrics
            "cases_per_hour_overall": round(cph_overall, 1),
            "total_cases_overall": total_cases_overall,
            "hours_elapsed_overall": round(hours_elapsed, 2),
            "throughput_reset_at": reset_at,
            "cases_per_hour_latest_session": round(cph_session, 1),
            "latest_session_cases": session_cases,
            "latest_session_id": session_id_display,
            # Legacy window metrics
            "cases_last_60m": cases_60,
            "cases_last_10m": cases_10,
            "cases_per_hour_60m_window": float(cph_60),
            "cases_per_hour_10m_extrapolated": float(cph_10),
            "active_workers": int(active_workers),
            "judges_done_24h": int(judges_done_24h),
            "by_status_last_hour": by_status,
        }

    def get_scrape_events_recent(self, limit: int = 30) -> List[ScrapeEventRecord]:
        return (
            self.session.query(ScrapeEventRecord)
            .order_by(ScrapeEventRecord.started_at.desc())
            .limit(limit)
            .all()
        )

    def get_worker_snapshots(self, max_workers: int = 32) -> List[Dict[str, Any]]:
        """Latest event per worker (for status table)."""
        wids = [
            r[0]
            for r in self.session.query(ScrapeEventRecord.worker_id)
            .distinct()
            .order_by(ScrapeEventRecord.worker_id)
            .limit(max_workers)
            .all()
        ]
        out: List[Dict[str, Any]] = []
        for wid in wids:
            rec = (
                self.session.query(ScrapeEventRecord)
                .filter(ScrapeEventRecord.worker_id == wid)
                .order_by(ScrapeEventRecord.started_at.desc())
                .first()
            )
            if not rec:
                continue
            age_s = None
            if rec.started_at:
                age_s = (datetime.utcnow() - rec.started_at).total_seconds()
            out.append(
                {
                    "worker_id": rec.worker_id,
                    "proxy_port": rec.proxy_port,
                    "judge_name": rec.judge_name,
                    "status": rec.status,
                    "cases_collected": rec.cases_collected,
                    "started_at": rec.started_at,
                    "finished_at": rec.finished_at,
                    "error_message": rec.error_message,
                    "started_ago_seconds": age_s,
                }
            )
        return out

    def get_scrape_case_buckets(
        self, hours: int = 6, bucket_minutes: int = 5
    ) -> List[Dict[str, Any]]:
        since = datetime.utcnow() - timedelta(hours=hours)
        rows = (
            self.session.query(ScrapeEventRecord)
            .filter(
                ScrapeEventRecord.finished_at.isnot(None),
                ScrapeEventRecord.finished_at >= since,
                ScrapeEventRecord.status == "success",
            )
            .all()
        )
        buckets: Dict[datetime, int] = defaultdict(int)
        for r in rows:
            if not r.finished_at:
                continue
            ts = r.finished_at
            m = (ts.minute // bucket_minutes) * bucket_minutes
            slot = ts.replace(minute=m, second=0, microsecond=0)
            buckets[slot] += r.cases_collected or 0
        return [
            {"bucket_start": k, "cases": buckets[k]}
            for k in sorted(buckets.keys())
        ]

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

    # --- Judge progress (resume support) ---

    def get_judge_progress(self, judge_name: str) -> Optional[JudgeProgressRecord]:
        return (
            self.session.query(JudgeProgressRecord)
            .filter(JudgeProgressRecord.judge_name == judge_name)
            .first()
        )

    def get_all_judge_progress(self) -> List[JudgeProgressRecord]:
        return self.session.query(JudgeProgressRecord).order_by(JudgeProgressRecord.judge_name).all()

    def upsert_judge_progress(
        self,
        judge_name: str,
        *,
        court: Optional[str] = None,
        status: Optional[str] = None,
        cases_collected: Optional[int] = None,
        total_count_at_start: Optional[int] = None,
        max_cases: Optional[int] = None,
        error_message: Optional[str] = None,
        completed_at: Optional[datetime] = None,
    ) -> JudgeProgressRecord:
        rec = self.get_judge_progress(judge_name)
        if rec is None:
            rec = JudgeProgressRecord(
                judge_name=judge_name,
                court=court or "",
                status=status or "pending",
                cases_collected=cases_collected or 0,
                total_count_at_start=total_count_at_start or 0,
                max_cases=max_cases or 0,
                started_at=datetime.utcnow(),
            )
            self.session.add(rec)
        else:
            if court is not None:
                rec.court = court
            if status is not None:
                rec.status = status
            if cases_collected is not None:
                rec.cases_collected = cases_collected
            if total_count_at_start is not None:
                rec.total_count_at_start = total_count_at_start
            if max_cases is not None:
                rec.max_cases = max_cases
            if error_message is not None:
                rec.error_message = error_message
            if completed_at is not None:
                rec.completed_at = completed_at
            rec.updated_at = datetime.utcnow()
        self.session.commit()
        return rec

    def reset_judge_progress(self, judge_name: Optional[str] = None) -> int:
        """Clear progress. If judge_name given, reset only that judge; else reset all."""
        q = self.session.query(JudgeProgressRecord)
        if judge_name:
            q = q.filter(JudgeProgressRecord.judge_name == judge_name)
        count = q.delete()
        self.session.commit()
        return count

    def mark_collecting_as_failed(self, reason: str = "Interrupted") -> int:
        """Mark any in-progress judges as failed (crash recovery)."""
        rows = (
            self.session.query(JudgeProgressRecord)
            .filter(JudgeProgressRecord.status.in_(["collecting", "enriching"]))
            .all()
        )
        for r in rows:
            r.status = "failed"
            r.error_message = reason
            r.updated_at = datetime.utcnow()
        self.session.commit()
        return len(rows)

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
