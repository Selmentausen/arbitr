"""
Fleet management API routes.

Seed judge queue, manually trigger VM rotation, and manage the fleet.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from orchestrator.middleware.auth import verify_api_key
from orchestrator.models.api_schemas import RotateRequest, StatusResponse, RotationStatus
from orchestrator.services.rotation_service import RotationService
from src.scraper.judge_loader import load_judges_from_file
from src.storage.database import JudgeProgressRecord, get_session
from src.storage.repository import CaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.post("/seed-judges", response_model=StatusResponse)
async def seed_judges(
    judges_file: Optional[str] = None,
    _: str = Depends(verify_api_key),
):
    """
    Seed the judge_progress queue from a judges.txt file.

    Each line = one judge full name (e.g. "Титова Екатерина Викторовна").
    Skips duplicates.
    """
    path = Path(judges_file or "configs/dictionaries/judges.txt")
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Judges file not found: {path}"
        )

    session = get_session()
    try:
        repo = CaseRepository(session)
        judges = load_judges_from_file(path)
        added = 0
        skipped = 0

        for entry in judges:
            judge_name = entry.search_name

            existing = repo.get_judge_progress(judge_name)
            if existing:
                skipped += 1
                continue

            session.add(JudgeProgressRecord(
                judge_name=judge_name,
                status="pending",
            ))
            added += 1

        session.commit()
        logger.info(
            "Seeded judges: %d added, %d skipped (already exist)",
            added, skipped,
        )
        return StatusResponse(
            ok=True,
            message=f"Seeded {added} judges ({skipped} already existed)",
            data={"added": added, "skipped": skipped},
        )
    finally:
        session.close()


@router.post("/rotate/{worker_id}", response_model=RotationStatus)
async def rotate_worker(
    worker_id: str,
    request: Request,
    body: RotateRequest = RotateRequest(),
    _: str = Depends(verify_api_key),
):
    """
    Manually trigger IP rotation for a worker.

    The orchestrator checks rate limits, calls the Timeweb API,
    and queues a rotate_ip command for the worker.
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
        if worker is None:
            raise HTTPException(
                status_code=404, detail=f"Worker {worker_id} not found"
            )

        rotation_service: RotationService = request.app.state.rotation_service

        logger.info(
            "Manual rotation requested for worker %s (strategy: %s)",
            worker_id, body.strategy,
        )

        if body.strategy == "cooldown":
            # Just mark as rotating, don't assign new IP yet
            repo.update_worker_status(worker_id, status="rotating")
            return RotationStatus(
                worker_id=worker_id,
                old_ip=worker.ip_address,
                status="cooldown",
                reason="Manual cooldown requested",
            )

        # Trigger actual rotation
        new_ip = await rotation_service.rotate_worker_ip(
            worker_id=worker_id,
            vps_id=worker.vps_id or "",
            server_id=worker.vm_id or worker.vps_id or "",
            old_ip=worker.ip_address or "",
            proxy_port=worker.proxy_port or 0,
            reason="Manual rotation via API",
        )

        if new_ip is None:
            return RotationStatus(
                worker_id=worker_id,
                old_ip=worker.ip_address,
                status="failed",
                reason="Rotation failed (check rate limits or Timeweb API)",
            )

        return RotationStatus(
            worker_id=worker_id,
            old_ip=worker.ip_address,
            new_ip=new_ip,
            status="in_progress",
            reason="Rotation command queued for worker",
        )
    finally:
        session.close()


@router.get("/rotation-stats", response_model=StatusResponse)
async def rotation_stats(
    request: Request,
    _: str = Depends(verify_api_key),
):
    """Get rotation statistics (rate limits, pending commands, history)."""
    rotation_service: RotationService = request.app.state.rotation_service
    stats = rotation_service.get_stats()
    return StatusResponse(
        ok=True,
        message="Rotation statistics",
        data=stats,
    )
