"""Stats and scrape events repository — throughput, events, snapshots."""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from sqlalchemy import func

from src.storage.database import (
    CaseRecord,
    ScrapeEventRecord,
    ScrapeMetaRecord,
)
from src.storage.repository.base import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StatsRepository(BaseRepository):
    """Aggregate statistics and scrape event tracking."""

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics."""
        total = self.session.query(func.count(CaseRecord.id)).scalar() or 0

        status_counts = dict(
            self.session.query(CaseRecord.status, func.count(CaseRecord.id))
            .group_by(CaseRecord.status)
            .all()
        )
        category_counts = dict(
            self.session.query(CaseRecord.category, func.count(CaseRecord.id))
            .group_by(CaseRecord.category)
            .all()
        )
        reviewed_count = (
            self.session.query(func.count(CaseRecord.id))
            .filter(CaseRecord.reviewed == True)
            .scalar() or 0
        )
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

    # --- Scrape events ---

    def start_scrape_event(
        self,
        judge_name: str,
        worker_id,
        proxy_port: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> int:
        rec = ScrapeEventRecord(
            judge_name=judge_name,
            worker_id=str(worker_id),
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
        """Finalize stale running events (e.g. process killed via Ctrl+C)."""
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

    # --- Throughput ---

    def _get_scrape_meta(self) -> ScrapeMetaRecord:
        meta = self.session.get(ScrapeMetaRecord, 1)
        if meta is None:
            meta = ScrapeMetaRecord(id=1)
            self.session.add(meta)
            self.session.commit()
        return meta

    def reset_throughput(self) -> None:
        meta = self._get_scrape_meta()
        meta.throughput_reset_at = datetime.utcnow()
        self.session.commit()

    def get_throughput(self) -> Dict[str, Any]:
        now = datetime.utcnow()
        meta = self._get_scrape_meta()

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

        if reset_at:
            hours_elapsed = max((now - reset_at).total_seconds() / 3600, 0.001)
        else:
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

        def _sum_cases_since(minutes: int, only_success: bool = True) -> int:
            since = now - timedelta(minutes=minutes)
            q = self.session.query(
                func.coalesce(func.sum(ScrapeEventRecord.cases_collected), 0)
            ).filter(
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
            .scalar() or 0
        )

        since_24h = now - timedelta(hours=24)
        judges_done_24h = (
            self.session.query(func.count(ScrapeEventRecord.id))
            .filter(
                ScrapeEventRecord.finished_at.isnot(None),
                ScrapeEventRecord.finished_at >= since_24h,
            )
            .scalar() or 0
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
            "cases_per_hour_overall": round(cph_overall, 1),
            "total_cases_overall": total_cases_overall,
            "hours_elapsed_overall": round(hours_elapsed, 2),
            "throughput_reset_at": reset_at,
            "cases_per_hour_latest_session": round(cph_session, 1),
            "latest_session_cases": session_cases,
            "latest_session_id": session_id_display,
            "cases_last_60m": cases_60,
            "cases_last_10m": cases_10,
            "cases_per_hour_60m_window": float(cph_60),
            "cases_per_hour_10m_extrapolated": float(cph_10),
            "active_workers": int(active_workers),
            "judges_done_24h": int(judges_done_24h),
            "by_status_last_hour": by_status,
        }
