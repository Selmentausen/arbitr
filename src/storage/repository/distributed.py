"""Distributed worker fleet repository — worker registration, status, heartbeats."""
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import or_

from src.storage.database import WorkerStatusRecord
from src.storage.repository.base import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DistributedRepository(BaseRepository):
    """Worker fleet management for the distributed architecture."""

    def register_worker(
        self,
        worker_id: str,
        ip_address: Optional[str] = None,
        vps_id: Optional[str] = None,
        provider: str = "timeweb",
        vm_id: Optional[str] = None,
        proxy_port: Optional[int] = None,
    ) -> WorkerStatusRecord:
        """Register a worker or update its registration."""
        rec = self.session.get(WorkerStatusRecord, worker_id)
        if rec is None:
            rec = WorkerStatusRecord(
                id=worker_id,
                vps_id=vps_id,
                ip_address=ip_address,
                provider=provider,
                vm_id=vm_id,
                proxy_port=proxy_port,
                status="active",
                last_heartbeat=datetime.utcnow(),
                registered_at=datetime.utcnow(),
            )
            self.session.add(rec)
        else:
            rec.ip_address = ip_address or rec.ip_address
            rec.vps_id = vps_id or rec.vps_id
            rec.vm_id = vm_id or rec.vm_id
            rec.proxy_port = proxy_port or rec.proxy_port
            rec.status = "active"
            rec.last_heartbeat = datetime.utcnow()
            rec.blocked_at = None
        self.session.commit()
        return rec

    def update_worker_heartbeat(self, worker_id: str) -> bool:
        """Update worker heartbeat timestamp."""
        rec = self.session.get(WorkerStatusRecord, worker_id)
        if rec is None:
            return False
        rec.last_heartbeat = datetime.utcnow()
        self.session.commit()
        return True

    def mark_worker_blocked(
        self,
        worker_id: str,
        release_judge: bool = True,
    ) -> Optional[str]:
        """Mark a worker as blocked. Optionally release its current judge."""
        rec = self.session.get(WorkerStatusRecord, worker_id)
        if rec is None:
            return None

        rec.status = "blocked"
        rec.blocked_at = datetime.utcnow()
        released_judge = rec.current_judge

        if release_judge and released_judge:
            from src.storage.repository.judge_progress import JudgeProgressRepository

            judge_repo = JudgeProgressRepository(self.session)
            judge_rec = judge_repo.get_judge_progress(released_judge)
            if judge_rec and judge_rec.claimed_by == worker_id:
                judge_rec.status = "pending"
                judge_rec.claimed_by = None
                judge_rec.heartbeat = None
                judge_rec.retry_count = (judge_rec.retry_count or 0) + 1
                judge_rec.error_message = f"Released: worker {worker_id} reported blocked"
            rec.current_judge = None
            self.session.commit()

        return released_judge

    def update_worker_status(
        self,
        worker_id: str,
        status: str,
        current_judge: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> bool:
        """Update worker status and optionally current judge/IP."""
        rec = self.session.get(WorkerStatusRecord, worker_id)
        if rec is None:
            return False
        rec.status = status
        if current_judge is not None:
            rec.current_judge = current_judge
        if ip_address is not None:
            rec.ip_address = ip_address
        rec.last_heartbeat = datetime.utcnow()
        self.session.commit()
        return True

    def get_all_workers(self) -> List[WorkerStatusRecord]:
        """Get all registered workers."""
        return (
            self.session.query(WorkerStatusRecord)
            .order_by(WorkerStatusRecord.id)
            .all()
        )

    def get_blocked_workers(self) -> List[WorkerStatusRecord]:
        """Get all workers currently marked as blocked."""
        return (
            self.session.query(WorkerStatusRecord)
            .filter(WorkerStatusRecord.status == "blocked")
            .all()
        )

    def get_stale_workers(self, timeout_minutes: int = 5) -> List[WorkerStatusRecord]:
        """Get active workers that haven't sent a heartbeat recently."""
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        return (
            self.session.query(WorkerStatusRecord)
            .filter(
                WorkerStatusRecord.status == "active",
                or_(
                    WorkerStatusRecord.last_heartbeat.is_(None),
                    WorkerStatusRecord.last_heartbeat < cutoff,
                ),
            )
            .all()
        )
