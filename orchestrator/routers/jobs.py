"""
Job queue API routes.

Handles job claiming, progress updates, completion, failure, and release.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from orchestrator.middleware.auth import verify_api_key
from orchestrator.models.api_schemas import (
    JobClaimResponse,
    JobCompleteRequest,
    JobFailedRequest,
    JobProgressRequest,
    JobReleaseRequest,
    JobReleaseResponse,
    StatusResponse,
)
from src.storage.database import JudgeProgressRecord, get_session
from src.storage.repository import CaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/next", response_model=JobClaimResponse)
async def claim_next_job(
    worker_id: str,
    _: str = Depends(verify_api_key),
):
    """
    Claim the next available judge for scraping.

    Uses FOR UPDATE SKIP LOCKED on PostgreSQL for safe concurrent access.
    Returns 204 if no jobs are available.
    """
    session = get_session()
    try:
        repo = CaseRepository(session)
        rec = repo.claim_next_judge(worker_id)
        if rec is None:
            raise HTTPException(status_code=204, detail="No jobs available")

        # Update worker's current judge
        repo.update_worker_status(
            worker_id, status="active", current_judge=rec.judge_name
        )

        logger.info("Worker %s claimed judge: %s", worker_id, rec.judge_name)
        return JobClaimResponse(
            judge_name=rec.judge_name,
            court=rec.court,
            status=rec.status,
            cases_collected=rec.cases_collected or 0,
            max_cases=rec.max_cases or 0,
            retry_count=rec.retry_count or 0,
        )
    finally:
        session.close()


@router.post("/{judge_name}/progress", response_model=StatusResponse)
async def update_job_progress(
    judge_name: str,
    request: JobProgressRequest,
    _: str = Depends(verify_api_key),
):
    """Worker reports scraping progress for a judge."""
    session = get_session()
    try:
        repo = CaseRepository(session)
        repo.update_judge_heartbeat(
            judge_name=judge_name,
            cases_collected=request.cases_collected,
        )
        # Update status if provided
        if request.status:
            rec = repo.get_judge_progress(judge_name)
            if rec:
                rec.status = request.status
                session.commit()

        return StatusResponse(ok=True, message="Progress updated")
    finally:
        session.close()


@router.post("/{judge_name}/complete", response_model=StatusResponse)
async def complete_job(
    judge_name: str,
    request: JobCompleteRequest,
    _: str = Depends(verify_api_key),
):
    """Worker reports judge scraping is complete."""
    session = get_session()
    try:
        repo = CaseRepository(session)
        rec = repo.get_judge_progress(judge_name)
        if rec is None:
            raise HTTPException(
                status_code=404, detail=f"Judge {judge_name} not found"
            )

        rec.status = "completed"
        rec.cases_collected = request.cases_collected
        rec.completed_at = datetime.utcnow()
        rec.updated_at = datetime.utcnow()
        session.commit()

        # Update worker stats
        if rec.claimed_by:
            worker = session.get(
                __import__(
                    "src.storage.database", fromlist=["WorkerStatusRecord"]
                ).WorkerStatusRecord,
                rec.claimed_by,
            )
            if worker:
                worker.total_judges_completed = (
                    worker.total_judges_completed or 0
                ) + 1
                worker.total_cases_scraped = (
                    worker.total_cases_scraped or 0
                ) + request.cases_collected
                worker.current_judge = None
                session.commit()

        logger.info(
            "Judge %s completed by worker %s (%d cases)",
            judge_name, rec.claimed_by, request.cases_collected,
        )
        return StatusResponse(
            ok=True, message=f"Judge {judge_name} marked as completed"
        )
    finally:
        session.close()


@router.post("/{judge_name}/failed", response_model=StatusResponse)
async def fail_job(
    judge_name: str,
    request: JobFailedRequest,
    _: str = Depends(verify_api_key),
):
    """Worker reports judge scraping failed."""
    session = get_session()
    try:
        repo = CaseRepository(session)
        rec = repo.get_judge_progress(judge_name)
        if rec is None:
            raise HTTPException(
                status_code=404, detail=f"Judge {judge_name} not found"
            )

        rec.status = "failed"
        rec.error_message = request.error_message
        rec.retry_count = (rec.retry_count or 0) + 1
        rec.updated_at = datetime.utcnow()
        session.commit()

        # Release worker's current judge
        if rec.claimed_by:
            worker = session.get(
                __import__(
                    "src.storage.database", fromlist=["WorkerStatusRecord"]
                ).WorkerStatusRecord,
                rec.claimed_by,
            )
            if worker:
                worker.current_judge = None
                session.commit()

        logger.warning(
            "Judge %s failed: %s", judge_name, request.error_message
        )
        return StatusResponse(
            ok=True, message=f"Judge {judge_name} marked as failed"
        )
    finally:
        session.close()


@router.post("/{judge_name}/release", response_model=JobReleaseResponse)
async def release_job(
    judge_name: str,
    request: JobReleaseRequest,
    _: str = Depends(verify_api_key),
):
    """
    Worker releases a claimed job back to the queue.

    Used for graceful shutdown or when the worker detects a block
    and wants to immediately free the judge for another worker.
    """
    session = get_session()
    try:
        repo = CaseRepository(session)
        rec = repo.get_judge_progress(judge_name)
        if rec is None:
            raise HTTPException(
                status_code=404, detail=f"Judge {judge_name} not found"
            )

        # Only release if it's currently claimed (collecting/enriching)
        if rec.status in ("collecting", "enriching"):
            rec.status = "pending"
            rec.claimed_by = None
            rec.heartbeat = None
            rec.error_message = f"Released: {request.reason}"
            rec.updated_at = datetime.utcnow()
            session.commit()

            # Release worker's current judge
            if rec.claimed_by:
                worker = session.get(
                    __import__(
                        "src.storage.database",
                        fromlist=["WorkerStatusRecord"],
                    ).WorkerStatusRecord,
                    rec.claimed_by,
                )
                if worker:
                    worker.current_judge = None
                    session.commit()

            logger.info(
                "Judge %s released: %s", judge_name, request.reason
            )
            return JobReleaseResponse(
                ok=True, judge_name=judge_name, status="pending"
            )

        # Job was already completed or failed — nothing to release
        return JobReleaseResponse(
            ok=True, judge_name=judge_name, status=rec.status
        )
    finally:
        session.close()
