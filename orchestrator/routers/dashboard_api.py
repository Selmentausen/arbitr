"""
Dashboard API routes.

Provides aggregate statistics, throughput metrics, and fleet status for the dashboard.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func

from orchestrator.middleware.auth import verify_api_key
from orchestrator.models.api_schemas import DashboardStats, ThroughputMetrics
from src.storage.database import (
    CaseRecord,
    DocumentRecord,
    JudgeProgressRecord,
    ScrapeEventRecord,
    WorkerStatusRecord,
    get_session,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_stats(
    _: str = Depends(verify_api_key),
):
    """Get aggregate dashboard statistics."""
    session = get_session()
    try:
        total_cases = session.query(func.count(CaseRecord.id)).scalar() or 0
        total_documents = session.query(func.count(DocumentRecord.id)).scalar() or 0

        # Judge progress breakdown
        judge_stats = (
            session.query(JudgeProgressRecord.status, func.count(JudgeProgressRecord.id))
            .group_by(JudgeProgressRecord.status)
            .all()
        )
        status_counts = {status: count for status, count in judge_stats}

        # Worker stats
        worker_stats = (
            session.query(WorkerStatusRecord.status, func.count(WorkerStatusRecord.id))
            .group_by(WorkerStatusRecord.status)
            .all()
        )
        worker_counts = {status: count for status, count in worker_stats}

        return DashboardStats(
            total_cases=total_cases,
            total_documents=total_documents,
            total_judges_queued=sum(status_counts.values()),
            judges_completed=status_counts.get("completed", 0),
            judges_in_progress=status_counts.get("collecting", 0) + status_counts.get("enriching", 0),
            judges_pending=status_counts.get("pending", 0),
            judges_failed=status_counts.get("failed", 0),
            active_workers=worker_counts.get("active", 0),
            blocked_workers=worker_counts.get("blocked", 0),
            offline_workers=worker_counts.get("offline", 0),
        )
    finally:
        session.close()


@router.get("/throughput", response_model=ThroughputMetrics)
async def get_throughput(
    hours: int = 1,
    _: str = Depends(verify_api_key),
):
    """Get throughput metrics (cases/hour, judges/hour)."""
    session = get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        # Cases scraped in the time window
        cases_count = (
            session.query(func.count(CaseRecord.id))
            .filter(CaseRecord.scraped_at >= cutoff)
            .scalar() or 0
        )

        # Judges completed in the time window
        judges_count = (
            session.query(func.count(JudgeProgressRecord.id))
            .filter(
                JudgeProgressRecord.status == "completed",
                JudgeProgressRecord.completed_at >= cutoff,
            )
            .scalar() or 0
        )

        return ThroughputMetrics(
            cases_per_hour=cases_count / max(hours, 1),
            judges_per_hour=judges_count / max(hours, 1),
            active_since=cutoff,
        )
    finally:
        session.close()
