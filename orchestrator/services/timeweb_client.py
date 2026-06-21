"""
Timeweb Cloud API client for IP rotation.

Interface for managing VPS servers, floating IPs, and snapshots.
The actual Timeweb API calls are stubbed here — fill in the real
endpoints once you have API documentation.

Environment:
    TIMEWEB_API_TOKEN  — API authentication token
    TIMEWEB_PROJECT_ID — Project ID (optional)
"""
import os
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import httpx

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FloatingIpInfo:
    """A floating IP assigned to a VPS."""
    ip: str
    id: Optional[str] = None
    status: str = "active"  # active, pending, releasing


@dataclass
class ServerInfo:
    """A Timeweb VPS server."""
    id: str
    name: str
    status: str
    ips: List[FloatingIpInfo]
    region: Optional[str] = None


class TimewebClient:
    """
    Client for the Timeweb Cloud API.
    """

    BASE_URL = "https://api.timeweb.cloud/api/v1"

    def __init__(self, api_token: Optional[str] = None):
        self.api_token = api_token or os.environ.get("TIMEWEB_API_TOKEN", "")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def is_configured(self) -> bool:
        return bool(self.api_token)

    # ------------------------------------------------------------------
    # Server management
    # ------------------------------------------------------------------

    async def list_servers(self) -> List[ServerInfo]:
        """List all VPS servers in the account."""
        client = await self._get_client()
        resp = await client.get(f"{self.BASE_URL}/servers")
        resp.raise_for_status()
        data = resp.json()
        # TODO: Parse actual Timeweb response format
        return []

    async def get_server(self, server_id: str) -> Optional[ServerInfo]:
        """Get details of a specific VPS."""
        client = await self._get_client()
        resp = await client.get(f"{self.BASE_URL}/servers/{server_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        # TODO: Parse actual Timeweb response format
        return None

    # ------------------------------------------------------------------
    # Floating IP management
    # ------------------------------------------------------------------

    async def list_floating_ips(self, server_id: str) -> List[FloatingIpInfo]:
        """List all floating IPs assigned to a server."""
        client = await self._get_client()
        resp = await client.get(
            f"{self.BASE_URL}/servers/{server_id}/ips"
        )
        resp.raise_for_status()
        # TODO: Parse actual Timeweb response format
        return []

    async def assign_floating_ip(self, server_id: str) -> Optional[FloatingIpInfo]:
        """
        Order and assign a new floating IP to a server.

        Returns the new IP info, or None if the quota is exceeded.
        """
        client = await self._get_client()
        resp = await client.post(
            f"{self.BASE_URL}/servers/{server_id}/ips",
            json={"type": "ipv4"},
        )
        if resp.status_code == 429:
            logger.warning("Floating IP quota exceeded for server %s", server_id)
            return None
        resp.raise_for_status()
        # TODO: Parse actual Timeweb response format
        return None

    async def release_floating_ip(
        self, server_id: str, ip_id: str
    ) -> bool:
        """Release a floating IP from a server."""
        client = await self._get_client()
        resp = await client.delete(
            f"{self.BASE_URL}/servers/{server_id}/ips/{ip_id}"
        )
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    # ------------------------------------------------------------------
    # Rotation (high-level)
    # ------------------------------------------------------------------

    async def rotate_ip(
        self, server_id: str, old_ip: str
    ) -> Optional[str]:
        """
        Rotate a server's IP: release old, assign new.

        Returns the new IP address, or None if rotation failed.
        """
        logger.info(
            "Rotating IP for server %s: releasing %s", server_id, old_ip
        )

        # Step 1: Find and release the old IP
        ips = await self.list_floating_ips(server_id)
        ip_to_release = None
        for ip_info in ips:
            if ip_info.ip == old_ip:
                ip_to_release = ip_info.id
                break

        if ip_to_release:
            ok = await self.release_floating_ip(server_id, ip_to_release)
            if not ok:
                logger.error(
                    "Failed to release IP %s from server %s",
                    old_ip, server_id,
                )
                return None
            logger.info("Released IP %s", old_ip)
        else:
            logger.warning(
                "IP %s not found on server %s, may already be released",
                old_ip, server_id,
            )

        # Step 2: Assign a new IP
        new_ip_info = await self.assign_floating_ip(server_id)
        if new_ip_info is None:
            logger.error(
                "Failed to assign new IP to server %s", server_id
            )
            return None

        logger.info(
            "Assigned new IP %s to server %s",
            new_ip_info.ip, server_id,
        )
        return new_ip_info.ip
