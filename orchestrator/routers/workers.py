"""
Worker management API routes.

Handles worker registration, heartbeat (with command piggyback), and block reporting.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List

from orchestrator.middleware.auth import verify_api_key
from orchestrator.models.api_schemas import (
    StatusResponse,
    WorkerBlockedRequest,
    WorkerHeartbeatResponse,
    WorkerRegisterRequest,
    WorkerStatusResponse,
)
from orchestrator.services.rotation_service import RotationService
from src.storage.database import get_session
from src.storage.repository import CaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/workers", tags=["workers"])


@router.post("/register", response_model=WorkerStatusResponse)
async def register_worker(
    request: WorkerRegisterRequest,
    _: str = Depends(verify_api_key),
):
    """Register a new worker or re-register an existing one."""
    session = get_session()
    try:
        repo = CaseRepository(session)
        rec = repo.register_worker(
            worker_id=request.worker_id,
            ip_address=request.ip_address,
            vps_id=request.vps_id,
            provider=request.provider,
            vm_id=request.vm_id,
            proxy_port=request.proxy_port,
        )
        logger.info("Worker registered: %s (IP: %s)", rec.id, rec.ip_address)
        return WorkerStatusResponse(
            id=rec.id,
            vps_id=rec.vps_id,
            ip_address=rec.ip_address,
            provider=rec.provider,
            status=rec.status,
            current_judge=rec.current_judge,
            proxy_port=rec.proxy_port,
            last_heartbeat=rec.last_heartbeat,
            registered_at=rec.registered_at,
            total_cases_scraped=rec.total_cases_scraped or 0,
            total_judges_completed=rec.total_judges_completed or 0,
        )
    finally:
        session.close()


@router.post("/{worker_id}/heartbeat", response_model=WorkerHeartbeatResponse)
async def worker_heartbeat(
    worker_id: str,
    request: Request,
    _: str = Depends(verify_api_key),
):
    """
    Worker sends heartbeat to confirm it's alive.

    The orchestrator checks its command queue for this worker and returns
    any pending command in the response (e.g., rotate_ip, shutdown).
    """
    session = get_session()
    try:
        repo = CaseRepository(session)
        ok = repo.update_worker_heartbeat(worker_id)
        if not ok:
            raise HTTPException(
                status_code=404, detail=f"Worker {worker_id} not found"
            )

        # Check for pending commands via the rotation service
        rotation_service: RotationService = request.app.state.rotation_service
        command = rotation_service.get_pending_command(worker_id)
        if command:
            rotation_service.clear_command(worker_id)
            logger.info(
                "Sent command %s to worker %s via heartbeat",
                command.get("type"), worker_id,
            )

        # If no higher-priority command, inject pause/resume signals
        if not command:
            is_paused = getattr(request.app.state, "scraping_paused", False)
            if is_paused:
                command = {"type": "pause"}

        return WorkerHeartbeatResponse(
            ok=True,
            message="Heartbeat received",
            command=command,
        )
    finally:
        session.close()


@router.post("/{worker_id}/blocked", response_model=StatusResponse)
async def worker_blocked(
    worker_id: str,
    request: Request,
    body: WorkerBlockedRequest,
    _: str = Depends(verify_api_key),
):
    """
    Worker reports that its IP has been blocked.

    The orchestrator:
      1. Marks the worker as blocked and releases its judge
      2. Decides whether to rotate the IP (rate limits)
      3. If rotation is allowed, calls the Timeweb API and queues a rotate_ip command
      4. The worker will receive the command on its next heartbeat
    """
    session = get_session()
    try:
        repo = CaseRepository(session)
        worker = session.get(
            __import__(
                "src.storage.database", fromlist=["WorkerStatusRecord"]
            ).WorkerStatusRecord,
            worker_id,
        )

        released_judge = repo.mark_worker_blocked(
            worker_id, release_judge=True
        )

        # Log the block event
        if released_judge:
            repo.start_scrape_event(
                judge_name=released_judge,
                worker_id=worker_id,
                status="blocked",
                session_id=None,
            )

        # Decide whether to rotate
        rotation_service: RotationService = request.app.state.rotation_service
        if worker and worker.vps_id and worker.ip_address:
            should_rotate, reason = rotation_service.should_rotate(
                worker_id, worker.vps_id
            )
            if should_rotate:
                logger.info(
                    "Initiating rotation for worker %s (%s -> ...)",
                    worker_id, worker.ip_address,
                )
                # Async rotation — we don't wait for it here,
                # it will queue the command when done
                asyncio.create_task(
                    rotation_service.rotate_worker_ip(
                        worker_id=worker_id,
                        vps_id=worker.vps_id,
                        server_id=worker.vm_id or worker.vps_id,
                        old_ip=worker.ip_address,
                        proxy_port=worker.proxy_port or 0,
                        reason=body.reason or "Worker reported block",
                    )
                )
            else:
                logger.warning(
                    "Rotation not allowed for worker %s: %s",
                    worker_id, reason,
                )

        logger.warning(
            "Worker %s reported blocked. Reason: %s. Released judge: %s",
            worker_id, body.reason, released_judge,
        )
        return StatusResponse(
            ok=True,
            message=f"Worker marked as blocked. Released judge: {released_judge}",
            data={"released_judge": released_judge},
        )
    finally:
        session.close()


@router.get("", response_model=List[WorkerStatusResponse])
async def list_workers(
    _: str = Depends(verify_api_key),
):
    """List all registered workers."""
    session = get_session()
    try:
        repo = CaseRepository(session)
        workers = repo.get_all_workers()
        return [
            WorkerStatusResponse(
                id=w.id,
                vps_id=w.vps_id,
                ip_address=w.ip_address,
                provider=w.provider,
                status=w.status,
                current_judge=w.current_judge,
                proxy_port=w.proxy_port,
                last_heartbeat=w.last_heartbeat,
                registered_at=w.registered_at,
                blocked_at=w.blocked_at,
                total_cases_scraped=w.total_cases_scraped or 0,
                total_judges_completed=w.total_judges_completed or 0,
            )
            for w in workers
        ]
    finally:
        session.close()
