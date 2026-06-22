"""Judge progress repository — judge queue, resume support, and job claiming."""
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import or_, text

from src.storage.database import JudgeProgressRecord, is_postgres
from src.storage.repository.base import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class JudgeProgressRepository(BaseRepository):
    """Judge progress and resume tracking."""

    # --- Read ---

    def get_judge_progress(self, judge_name: str) -> Optional[JudgeProgressRecord]:
        return (
            self.session.query(JudgeProgressRecord)
            .filter(JudgeProgressRecord.judge_name == judge_name)
            .first()
        )

    def get_all_judge_progress(self) -> List[JudgeProgressRecord]:
        return (
            self.session.query(JudgeProgressRecord)
            .order_by(JudgeProgressRecord.judge_name)
            .all()
        )

    # --- Upsert ---

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

    # --- Distributed job claiming ---

    def claim_next_judge(
        self,
        worker_id: str,
        stale_timeout_minutes: int = 10,
    ) -> Optional[JudgeProgressRecord]:
        """Atomically claim the next available judge for a worker."""
        now = datetime.utcnow()
        stale_cutoff = now - timedelta(minutes=stale_timeout_minutes)

        if is_postgres():
            result = self.session.execute(
                text("""
                    UPDATE judge_progress
                    SET status = 'collecting',
                        claimed_by = :worker_id,
                        heartbeat = :now,
                        started_at = COALESCE(started_at, :now),
                        updated_at = :now
                    WHERE id = (
                        SELECT id FROM judge_progress
                        WHERE status IN ('pending', 'failed')
                           OR (status IN ('collecting', 'enriching')
                               AND (heartbeat IS NULL OR heartbeat < :stale_cutoff))
                        ORDER BY
                            CASE WHEN status = 'pending' THEN 0
                                 WHEN status = 'failed' THEN 1
                                 ELSE 2 END,
                            updated_at ASC NULLS FIRST
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                """),
                {"worker_id": worker_id, "now": now, "stale_cutoff": stale_cutoff},
            )
            row = result.fetchone()
            self.session.commit()
            if row is None:
                return None
            return self.get_judge_progress(row.judge_name)
        else:
            rec = (
                self.session.query(JudgeProgressRecord)
                .filter(
                    or_(
                        JudgeProgressRecord.status.in_(["pending", "failed"]),
                        JudgeProgressRecord.status.in_(["collecting", "enriching"])
                        & (
                            (JudgeProgressRecord.heartbeat.is_(None))
                            | (JudgeProgressRecord.heartbeat < stale_cutoff)
                        ),
                    )
                )
                .order_by(JudgeProgressRecord.updated_at.asc())
                .first()
            )
            if rec is None:
                return None
            rec.status = "collecting"
            rec.claimed_by = worker_id
            rec.heartbeat = now
            rec.started_at = rec.started_at or now
            rec.updated_at = now
            self.session.commit()
            return rec

    def update_judge_heartbeat(
        self,
        judge_name: str,
        cases_collected: Optional[int] = None,
    ) -> None:
        """Update heartbeat and optionally cases_collected for a claimed judge."""
        rec = self.get_judge_progress(judge_name)
        if rec:
            rec.heartbeat = datetime.utcnow()
            if cases_collected is not None:
                rec.cases_collected = cases_collected
            rec.updated_at = datetime.utcnow()
            self.session.commit()

    def get_stale_claims(
        self,
        stale_timeout_minutes: int = 10,
    ) -> List[JudgeProgressRecord]:
        """Find judges with stale heartbeats (worker likely crashed)."""
        stale_cutoff = datetime.utcnow() - timedelta(minutes=stale_timeout_minutes)
        return (
            self.session.query(JudgeProgressRecord)
            .filter(
                JudgeProgressRecord.status.in_(["collecting", "enriching"]),
                or_(
                    JudgeProgressRecord.heartbeat.is_(None),
                    JudgeProgressRecord.heartbeat < stale_cutoff,
                ),
            )
            .all()
        )

    def reclaim_stale_judges(self, stale_timeout_minutes: int = 10) -> int:
        """Reset stale judges back to pending for re-claiming."""
        stale = self.get_stale_claims(stale_timeout_minutes)
        for rec in stale:
            logger.warning(
                "Reclaiming stale judge %s (was claimed by %s, last heartbeat %s)",
                rec.judge_name, rec.claimed_by, rec.heartbeat,
            )
            rec.status = "pending"
            rec.claimed_by = None
            rec.heartbeat = None
            rec.retry_count = (rec.retry_count or 0) + 1
            rec.error_message = f"Reclaimed: stale heartbeat (was {rec.claimed_by})"
            rec.updated_at = datetime.utcnow()
        self.session.commit()
        return len(stale)
