import asyncio
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Set, List, Dict, Optional
from playwright.async_api import BrowserContext, Response, Request


def _fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


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
    """Attach to a browser context; counts request/response payload sizes and records details."""

    def __init__(self) -> None:
        self.stats = TrafficStats()
        self._pending: Set[asyncio.Task] = set()
        self.records: List[Dict] = []
        self._request_to_record: Dict[Request, Dict] = {}

    def attach(self, context: BrowserContext) -> None:
        context.on("request", self._on_request)
        context.on("response", self._on_response)

    def _on_request(self, request: Request) -> None:
        if not (request.url.startswith("http://") or request.url.startswith("https://")):
            return
            
        self.stats.request_count += 1
        record = {
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "status": None,
            "content_type": None,
            "size_bytes": 0,
        }
        self.records.append(record)
        self._request_to_record[request] = record

        try:
            body = request.post_data_buffer
            if body:
                record["size_bytes"] += len(body)
                self.stats.request_bytes += len(body)
            elif request.post_data:
                ul_len = len(request.post_data.encode("utf-8"))
                record["size_bytes"] += ul_len
                self.stats.request_bytes += ul_len
        except Exception:
            pass

    def _on_response(self, response: Response) -> None:
        if not (response.url.startswith("http://") or response.url.startswith("https://")):
            return
            
        self.stats.response_count += 1
        req = response.request
        record = self._request_to_record.get(req)
        
        task = asyncio.create_task(self._count_response(response, record))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _count_response(self, response: Response, record: Optional[Dict]) -> None:
        try:
            status = response.status
            ct = response.headers.get("content-type") or response.headers.get("Content-Type") or ""
            if record:
                record["status"] = status
                record["content_type"] = ct

            cl = response.headers.get("content-length") or response.headers.get("Content-Length")
            resp_bytes = 0
            if cl and str(cl).isdigit():
                resp_bytes = int(cl)
            else:
                body = await response.body()
                resp_bytes = len(body)
            
            self.stats.response_bytes += resp_bytes
            if record:
                record["size_bytes"] += resp_bytes
        except Exception:
            pass

    async def drain(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    def save_csv_report(self, filepath: str | Path) -> None:
        """Save a detailed CSV report of all tracked network requests."""
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["URL", "Method", "Resource Type", "Status", "Content-Type", "Size (Bytes)"])
            for r in self.records:
                writer.writerow([
                    r["url"],
                    r["method"],
                    r["resource_type"],
                    r.get("status") or "",
                    r.get("content_type") or "",
                    r["size_bytes"]
                ])

    def get_summary_tables(self) -> str:
        """Generate a text-based summary of resource types and top requests."""
        type_stats = {}
        for r in self.records:
            rtype = r["resource_type"]
            size = r["size_bytes"]
            if rtype not in type_stats:
                type_stats[rtype] = {"count": 0, "size": 0}
            type_stats[rtype]["count"] += 1
            type_stats[rtype]["size"] += size

        lines = []
        lines.append("=" * 80)
        lines.append("DETAILED NETWORK TRAFFIC REPORT")
        lines.append("=" * 80)
        lines.append(f"{'RESOURCE TYPE':<20} | {'COUNT':<6} | {'TOTAL SIZE':<12}")
        lines.append("-" * 80)
        for rtype, stats in sorted(type_stats.items(), key=lambda x: x[1]["size"], reverse=True):
            lines.append(f"{rtype:<20} | {stats['count']:<6} | {_fmt_size(stats['size']):<12}")
        lines.append("-" * 80)
        lines.append(f"{'Total':<20} | {len(self.records):<6} | {_fmt_size(self.stats.total_bytes):<12}")
        lines.append("\n")

        lines.append("TOP 15 LARGEST REQUESTS:")
        lines.append(f"{'SIZE':<10} | {'METHOD':<6} | {'TYPE':<10} | {'URL'}")
        lines.append("-" * 80)
        sorted_records = sorted(self.records, key=lambda x: x["size_bytes"], reverse=True)
        for r in sorted_records[:15]:
            url_short = r["url"] if len(r["url"]) <= 80 else r["url"][:77] + "..."
            lines.append(f"{_fmt_size(r['size_bytes']):<10} | {r['method']:<6} | {r['resource_type']:<10} | {url_short}")
        lines.append("=" * 80)
        return "\n".join(lines)
