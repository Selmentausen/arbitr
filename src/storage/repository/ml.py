"""ML classification repository — classification, review, and stats."""
import json
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from sqlalchemy.orm import subqueryload

from src.models.case import Case, StatusEnum
from src.storage.database import CaseRecord, InstanceRecord
from src.storage.repository.base import BaseRepository, _deserialize_json
from src.storage.repository.cases import _record_to_case
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MLRepository(BaseRepository):
    """ML classification and review operations."""

    # --- Review ---

    def mark_reviewed(self, case_id: str, reviewed: bool = True, notes: Optional[str] = None) -> bool:
        """Mark a case as reviewed."""
        record = self.session.get(CaseRecord, case_id)
        if record is None:
            return False
        record.reviewed = reviewed
        record.review_notes = notes
        record.reviewed_at = datetime.utcnow() if reviewed else None
        self.session.commit()
        logger.debug("Marked case %s as reviewed=%s", case_id, reviewed)
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

    # --- Classification queries ---

    def list_cases_for_classification(
        self,
        limit: int = 100,
        force: bool = False,
        case_id: Optional[str] = None,
    ) -> List[Case]:
        """List cases eligible for ML classification."""
        query = (
            self.session.query(CaseRecord)
            .options(
                subqueryload(CaseRecord.participants),
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
        cases = []
        for record in records:
            case = _record_to_case(record)
            if force or "ml_classification" not in case.extracted_data:
                cases.append(case)
            if len(cases) >= limit:
                break
        return cases

    def get_ml_stats(self) -> Dict[str, Any]:
        """Aggregate counts for ML-classified cases."""
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
        """List cases with ML classification, with ML-specific filters."""
        load_options = [
            subqueryload(CaseRecord.participants),
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
        seen = set()
        cases = []
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
