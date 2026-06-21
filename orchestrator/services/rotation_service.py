"""
Rotation service — manages IP rotation decisions, rate limits, and worker commands.

The orchestrator uses this to decide WHEN to rotate a worker's IP and HOW
to communicate that command to the worker (via heartbeat piggyback).

Key responsibilities:
  1. Rate limiting: enforce max rotations per VPS per hour/day
  2. Decision engine: decide if a blocked worker should be rotated
  3. Command queue: store pending commands for workers to receive via heartbeat
  4. Execution: call Timeweb API to actually rotate the IP
  5. Cleanup: remove stale commands, track rotation history

No external dependencies except the TimewebClient and S3Client.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from orchestrator.services.timeweb_client import TimewebClient
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class WorkerCommand:
    """A command queued for a worker to execute."""
    type: str  # "rotate_ip", "shutdown", "update_config"
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None


@dataclass
class RotationRecord:
    """History of a rotation event for rate limiting."""
    worker_id: str
    vps_id: str
    old_ip: str
    new_ip: str
    rotated_at: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""


class RotationService:
    """
    Manages IP rotation for the worker fleet.

    Configuration (via env or passed at init):
        ROTATION_MAX_PER_VPS_PER_HOUR  — default 3
        ROTATION_MAX_PER_VPS_PER_DAY   — default 10
        ROTATION_COOLDOWN_MINUTES      — default 15
        COMMAND_EXPIRY_MINUTES         — default 30
    """

    def __init__(
        self,
        timeweb_client: Optional[TimewebClient] = None,
        max_per_hour: int = 3,
        max_per_day: int = 10,
        cooldown_minutes: int = 15,
        command_expiry_minutes: int = 30,
    ):
        self.timeweb = timeweb_client or TimewebClient()
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self.cooldown_minutes = cooldown_minutes
        self.command_expiry_minutes = command_expiry_minutes

        # In-memory command queue: {worker_id: WorkerCommand}
        self._commands: Dict[str, WorkerCommand] = {}
        # Rotation history: {worker_id: [RotationRecord, ...]}
        self._history: Dict[str, List[RotationRecord]] = {}

        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Command queue (heartbeat piggyback)
    # ------------------------------------------------------------------

    def queue_command(self, worker_id: str, command_type: str, **kwargs) -> None:
        """
        Queue a command for a worker to receive on its next heartbeat.
        """
        expires = datetime.utcnow() + timedelta(
            minutes=self.command_expiry_minutes
        )
        self._commands[worker_id] = WorkerCommand(
            type=command_type,
            payload=kwargs,
            expires_at=expires,
        )
        logger.info(
            "Queued command %s for worker %s (expires: %s)",
            command_type, worker_id, expires,
        )

    def get_pending_command(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the next pending command for a worker (if any and not expired).
        """
        cmd = self._commands.get(worker_id)
        if cmd is None:
            return None

        if cmd.expires_at and datetime.utcnow() > cmd.expires_at:
            # Expired — remove it
            del self._commands[worker_id]
            return None

        return {"type": cmd.type, **cmd.payload}

    def clear_command(self, worker_id: str) -> None:
        """Remove a command after the worker has acknowledged it."""
        self._commands.pop(worker_id, None)

    def _cleanup_expired_commands(self) -> None:
        """Remove expired commands."""
        now = datetime.utcnow()
        expired = [
            wid for wid, cmd in self._commands.items()
            if cmd.expires_at and now > cmd.expires_at
        ]
        for wid in expired:
            del self._commands[wid]

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _get_rotation_history(self, vps_id: str) -> List[RotationRecord]:
        """Get rotation history for a VPS (across all workers on that VPS)."""
        return [
            rec for recs in self._history.values()
            for rec in recs if rec.vps_id == vps_id
        ]

    def _check_rate_limit(self, vps_id: str) -> tuple[bool, str]:
        """
        Check if rotation is allowed for this VPS.

        Returns (allowed, reason).
        """
        history = self._get_rotation_history(vps_id)
        now = datetime.utcnow()

        # Hourly limit
        hour_ago = now - timedelta(hours=1)
        rotations_this_hour = sum(
            1 for r in history if r.rotated_at > hour_ago
        )
        if rotations_this_hour >= self.max_per_hour:
            return False, (
                f"Hourly rotation limit reached ({rotations_this_hour}/"
                f"{self.max_per_hour}) for VPS {vps_id}"
            )

        # Daily limit
        day_ago = now - timedelta(days=1)
        rotations_today = sum(
            1 for r in history if r.rotated_at > day_ago
        )
        if rotations_today >= self.max_per_day:
            return False, (
                f"Daily rotation limit reached ({rotations_today}/"
                f"{self.max_per_day}) for VPS {vps_id}"
            )

        # Cooldown: last rotation on this VPS must be > cooldown_minutes ago
        if history:
            last_rotation = max(r.rotated_at for r in history)
            minutes_since = (now - last_rotation).total_seconds() / 60
            if minutes_since < self.cooldown_minutes:
                return False, (
                    f"Rotation cooldown active: {minutes_since:.1f}min < "
                    f"{self.cooldown_minutes}min for VPS {vps_id}"
                )

        return True, ""

    # ------------------------------------------------------------------
    # Decision engine
    # ------------------------------------------------------------------

    def should_rotate(self, worker_id: str, vps_id: str) -> tuple[bool, str]:
        """
        Decide whether to rotate this worker's IP.

        Returns (should_rotate, reason).
        """
        allowed, reason = self._check_rate_limit(vps_id)
        if not allowed:
            return False, reason

        return True, "Rotation allowed"

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def rotate_worker_ip(
        self,
        worker_id: str,
        vps_id: str,
        server_id: str,
        old_ip: str,
        proxy_port: int,
        reason: str = "",
    ) -> Optional[str]:
        """
        Execute IP rotation for a worker.

        Steps:
          1. Check rate limits
          2. Call Timeweb API to release old IP and assign new one
          3. Queue a "rotate_ip" command for the worker
          4. Record rotation history

        Returns the new IP, or None if rotation failed.
        """
        allowed, limit_reason = self._check_rate_limit(vps_id)
        if not allowed:
            logger.warning(
                "Rotation denied for worker %s: %s", worker_id, limit_reason
            )
            return None

        if not self.timeweb.is_configured():
            logger.error(
                "Timeweb API not configured. Cannot rotate IP for worker %s.",
                worker_id,
            )
            return None

        try:
            new_ip = await self.timeweb.rotate_ip(server_id, old_ip)
            if new_ip is None:
                logger.error(
                    "Timeweb rotation failed for server %s", server_id
                )
                return None

            # Queue command for the worker
            self.queue_command(
                worker_id=worker_id,
                command_type="rotate_ip",
                new_ip=new_ip,
                proxy_port=proxy_port,
                reason=reason,
            )

            # Record history
            record = RotationRecord(
                worker_id=worker_id,
                vps_id=vps_id,
                old_ip=old_ip,
                new_ip=new_ip,
                reason=reason,
            )
            self._history.setdefault(worker_id, []).append(record)

            logger.info(
                "Rotation queued for worker %s: %s -> %s",
                worker_id, old_ip, new_ip,
            )
            return new_ip

        except Exception as e:
            logger.error(
                "Rotation error for worker %s: %s", worker_id, e, exc_info=True
            )
            return None

    # ------------------------------------------------------------------
    # Background cleanup
    # ------------------------------------------------------------------

    async def run_cleanup(self) -> None:
        """Periodic cleanup of expired commands and old history."""
        self._cleanup_expired_commands()

        # Trim history older than 7 days to prevent memory growth
        cutoff = datetime.utcnow() - timedelta(days=7)
        for worker_id, records in list(self._history.items()):
            self._history[worker_id] = [
                r for r in records if r.rotated_at > cutoff
            ]
            if not self._history[worker_id]:
                del self._history[worker_id]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get rotation statistics for the dashboard."""
        total_rotations = sum(len(r) for r in self._history.values())
        pending_commands = len(self._commands)

        return {
            "total_rotations": total_rotations,
            "pending_commands": pending_commands,
            "max_per_hour": self.max_per_hour,
            "max_per_day": self.max_per_day,
            "cooldown_minutes": self.cooldown_minutes,
        }
