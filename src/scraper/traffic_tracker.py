"""Track HTTP bytes through a Playwright BrowserContext (proxy billing estimates)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Set

from playwright.async_api import BrowserContext, Response


@dataclass
class TrafficStats:
    request_bytes: int = 0
    response_bytes: int = 0
    request_count: int = 0
    response_count: int = 0

    @property
    def total_bytes(self) -> int:
        return self.request_bytes + self.response_bytes

    def total_mb(self) -> float:
        return self.total_bytes / 1024 / 1024

    def download_mb(self) -> float:
        return self.response_bytes / 1024 / 1024

    def upload_mb(self) -> float:
        return self.request_bytes / 1024 / 1024


class NetworkTrafficTracker:
    """Attach to a browser context; counts request/response payload sizes."""

    def __init__(self) -> None:
        self.stats = TrafficStats()
        self._pending: Set[asyncio.Task] = set()

    def attach(self, context: BrowserContext) -> None:
        context.on("request", self._on_request)
        context.on("response", self._on_response)

    def _on_request(self, request) -> None:
        self.stats.request_count += 1
        try:
            body = request.post_data_buffer
            if body:
                self.stats.request_bytes += len(body)
            elif request.post_data:
                self.stats.request_bytes += len(request.post_data.encode("utf-8"))
        except Exception:
            pass

    def _on_response(self, response: Response) -> None:
        self.stats.response_count += 1
        task = asyncio.create_task(self._count_response(response))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _count_response(self, response: Response) -> None:
        try:
            headers = response.headers
            cl = headers.get("content-length") or headers.get("Content-Length")
            if cl and str(cl).isdigit():
                self.stats.response_bytes += int(cl)
                return
            body = await response.body()
            self.stats.response_bytes += len(body)
        except Exception:
            pass

    async def drain(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
