"""
HTTP client for the orchestrator API.

Workers use this for ALL communication with the central server.
Features:
  - Exponential backoff retries for transient failures
  - Batch idempotency (batch_id UUID for deduplication)
  - Heartbeat command piggyback (orchestrator sends commands in heartbeat response)
  - Direct S3 upload via presigned URLs (orchestrator never sees PDF bytes)

"""
import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx


class OrchestratorClient:
    """
    Async HTTP client for the orchestrator API.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        worker_id: str,
        vps_id: str,
        max_retries: int = 3,
        base_delay: float = 1.0,
        s3_timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.worker_id = worker_id
        self.vps_id = vps_id
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.s3_timeout = s3_timeout

        self._client: Optional[httpx.AsyncClient] = None
        self._pending_command: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(max_connections=10),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _retry_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Execute an HTTP request with exponential backoff retries."""
        client = await self._get_client()

        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.request(method, url, **kwargs)
                # Retry on 5xx or 429 (rate limit)
                if resp.status_code >= 500 or resp.status_code == 429:
                    if attempt < self.max_retries:
                        delay = self.base_delay * (2 ** attempt)
                        await asyncio.sleep(delay)
                        continue
                resp.raise_for_status()
                return resp
            except (httpx.HTTPError, httpx.NetworkError) as e:
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                else:
                    raise

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    async def register(
        self, ip_address: str, proxy_port: Optional[int]
    ) -> Dict[str, Any]:
        """Register this worker with the orchestrator."""
        resp = await self._retry_request(
            "POST",
            "/api/workers/register",
            json={
                "worker_id": self.worker_id,
                "ip_address": ip_address,
                "vps_id": self.vps_id,
                "provider": "timeweb",
                "proxy_port": proxy_port,
            },
        )
        return resp.json()

    async def heartbeat(
        self, status: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Send heartbeat and check for pending commands from the orchestrator.

        The orchestrator can embed commands in the heartbeat response, e.g.:
            {"command": {"type": "rotate_ip", "new_ip": "1.2.3.4", ...}}

        Returns the command dict if present, else None.
        """
        payload = {
            "worker_id": self.worker_id,
            "timestamp": datetime.utcnow().isoformat(),
            **(status or {}),
        }

        resp = await self._retry_request(
            "POST",
            f"/api/workers/{self.worker_id}/heartbeat",
            json=payload,
        )

        data = resp.json()
        self._pending_command = data.get("command")
        return self._pending_command

    def get_pending_command(self) -> Optional[Dict[str, Any]]:
        """Get the last command received from the orchestrator."""
        return self._pending_command

    def clear_command(self) -> None:
        """Clear the pending command after executing it."""
        self._pending_command = None

    async def report_blocked(
        self, reason: str, blocked_url: Optional[str] = None
    ) -> None:
        """Report that this worker's IP has been blocked."""
        await self._retry_request(
            "POST",
            f"/api/workers/{self.worker_id}/blocked",
            json={"reason": reason, "blocked_url": blocked_url},
        )

    # ------------------------------------------------------------------
    # Job claiming
    # ------------------------------------------------------------------

    async def claim_job(self) -> Optional[Dict[str, Any]]:
        """Claim the next available judge job."""
        resp = await self._retry_request(
            "GET",
            f"/api/jobs/next?worker_id={self.worker_id}",
        )
        if resp.status_code == 204:
            return None
        return resp.json()

    async def update_progress(self, judge_name: str, **kwargs) -> None:
        """Update scraping progress for a judge."""
        await self._retry_request(
            "POST",
            f"/api/jobs/{judge_name}/progress",
            json=kwargs,
        )

    async def complete_job(self, judge_name: str, cases_collected: int) -> None:
        """Mark a judge job as completed."""
        await self._retry_request(
            "POST",
            f"/api/jobs/{judge_name}/complete",
            json={"cases_collected": cases_collected, "status": "completed"},
        )

    async def fail_job(self, judge_name: str, error_message: str) -> None:
        """Mark a judge job as failed."""
        await self._retry_request(
            "POST",
            f"/api/jobs/{judge_name}/failed",
            json={"error_message": error_message},
        )

    async def release_job(self, judge_name: str, reason: str) -> None:
        """
        Explicitly release a claimed job before shutting down or on block.
        """
        try:
            await self._retry_request(
                "POST",
                f"/api/jobs/{judge_name}/release",
                json={"reason": reason},
            )
        except httpx.HTTPStatusError:
            # Fallback if release endpoint doesn't exist yet
            await self.fail_job(judge_name, f"Released: {reason}")

    # ------------------------------------------------------------------
    # Case submission (metadata only — orchestrator returns presigned URLs)
    # ------------------------------------------------------------------

    async def submit_cases(
        self,
        judge_name: str,
        cases: List[Dict[str, Any]],
        documents: List[Dict[str, Any]],
        batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit case metadata and extracted text.

        Returns a dict that may contain:
            {
                "saved": 50,
                "upload_urls": {
                    "case-id-123": {
                        "doc-id-456": "https://s3...?signature=...",
                        ...
                    },
                    ...
                }
            }
        """
        batch_id = batch_id or str(uuid.uuid4())

        resp = await self._retry_request(
            "POST",
            "/api/cases/batch",
            json={
                "worker_id": self.worker_id,
                "vps_id": self.vps_id,
                "judge_name": judge_name,
                "batch_id": batch_id,
                "cases": cases,
                "documents": documents,
            },
        )
        return resp.json()

