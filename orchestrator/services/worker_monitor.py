"""
Background worker monitor.

Runs periodically to:
1. Detect stale worker heartbeats and mark workers offline
2. Reclaim judges from stale/crashed workers
3. Trigger IP rotation for blocked workers via the rotation service
"""

import asyncio
from datetime import datetime

from orchestrator.config import OrchestratorConfig
from orchestrator.services.rotation_service import RotationService
from src.storage.database import get_session
from src.storage.repository import CaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def monitor_loop(config: OrchestratorConfig, rotation_service: RotationService):
    """
    Background monitoring loop.

    Runs every config.monitor_interval_seconds and:
    1. Reclaims judges from workers with stale heartbeats
    2. Marks workers with stale heartbeats as offline
    3. Triggers rotation for blocked workers (via rotation_service)
    4. Runs periodic cleanup on the rotation service
    """
    logger.info(
        "Worker monitor started (interval: %ds, heartbeat timeout: %dm, worker timeout: %dm)",
        config.monitor_interval_seconds,
        config.heartbeat_timeout_minutes,
        config.worker_heartbeat_timeout_minutes,
    )

    while True:
        try:
            await asyncio.sleep(config.monitor_interval_seconds)
            await _run_monitor_cycle(config, rotation_service)
        except asyncio.CancelledError:
            logger.info("Worker monitor stopped")
            break
        except Exception as e:
            logger.error("Monitor cycle error: %s", e, exc_info=True)


async def _run_monitor_cycle(config: OrchestratorConfig, rotation_service: RotationService):
    """Execute one monitoring cycle."""
    session = get_session()
    try:
        repo = CaseRepository(session)

        # 1. Reclaim judges with stale heartbeats
        reclaimed = repo.reclaim_stale_judges(config.heartbeat_timeout_minutes)
        if reclaimed > 0:
            logger.warning("Reclaimed %d stale judges", reclaimed)

        # 2. Mark stale workers as offline
        stale_workers = repo.get_stale_workers(config.worker_heartbeat_timeout_minutes)
        for w in stale_workers:
            logger.warning(
                "Worker %s is stale (last heartbeat: %s), marking offline",
                w.id, w.last_heartbeat
            )
            repo.update_worker_status(w.id, status="offline")

        # 3. Handle blocked workers — trigger rotation via rotation service
        blocked_workers = repo.get_blocked_workers()
        for w in blocked_workers:
            if w.blocked_at:
                minutes_blocked = (datetime.utcnow() - w.blocked_at).total_seconds() / 60
                if minutes_blocked > 5:
                    logger.info(
                        "Worker %s has been blocked for %.0f minutes. "
                        "Attempting automatic rotation...",
                        w.id, minutes_blocked,
                    )
                    # Trigger rotation asynchronously
                    await rotation_service.rotate_worker_ip(
                        worker_id=w.id,
                        vps_id=w.vps_id or "",
                        server_id=w.vm_id or w.vps_id or "",
                        old_ip=w.ip_address or "",
                        proxy_port=w.proxy_port or 0,
                        reason=f"Auto-rotation after {minutes_blocked:.0f}min blocked",
                    )

        # 4. Run rotation service cleanup (expired commands, old history)
        await rotation_service.run_cleanup()

    finally:
        session.close()
