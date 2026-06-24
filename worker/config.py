"""
Worker configuration — all values come from environment variables.
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkerConfig:
    """
    Stateless worker configuration.

    Environment variables:
        WORKER_ID           — unique worker ID (e.g., vps-tw-01-w1)
        VPS_ID              — logical VPS group (e.g., vps-tw-01)
        ORCHESTRATOR_URL    — central server URL (e.g., https://orchestrator:8000)
        API_KEY             — Bearer token for orchestrator auth
        PROXY_PORT          — local microsocks port (e.g., 10001)
        PROXY_BIND_IP       — public IP to bind microsocks to (outgoing IP)
        HEARTBEAT_INTERVAL  — seconds between heartbeats (default: 30)
        POLL_INTERVAL       — seconds between job polls when idle (default: 60)
        SCRAPE_TIMEOUT      — max seconds per judge scrape (default: 300)
        BATCH_SIZE          — cases per submission batch (default: 50)
        MAX_RETRIES         — HTTP retries per request (default: 3)
        RETRY_BASE_DELAY    — initial retry backoff in seconds (default: 1.0)
        S3_UPLOAD_TIMEOUT   — seconds for S3 PUT (default: 60)
        CONFIG_PATH         — path to configs/main.yaml (default: configs/main.yaml)
        NO_STEALTH          — disable playwright-stealth (default: false)
    """

    worker_id: str = field(
        default_factory=lambda: os.environ.get("WORKER_ID", f"worker-{os.getpid()}")
    )
    vps_id: str = field(
        default_factory=lambda: os.environ.get("VPS_ID", "local")
    )
    orchestrator_url: str = field(
        default_factory=lambda: os.environ.get(
            "ORCHESTRATOR_URL", "http://localhost:8000"
        )
    )
    api_key: str = field(
        default_factory=lambda: os.environ.get("API_KEY", "CHANGE_ME_TO_A_LONG_RANDOM_STRING_32_CHARS_MIN")
    )

    # Proxy / IP binding
    proxy_port: Optional[int] = field(
        default_factory=lambda: (
            int(p) if (p := os.environ.get("PROXY_PORT")) else None
        )
    )
    proxy_bind_ip: Optional[str] = field(
        default_factory=lambda: os.environ.get("PROXY_BIND_IP")
    )

    # Timing
    heartbeat_interval: int = field(
        default_factory=lambda: int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
    )
    poll_interval: int = field(
        default_factory=lambda: int(os.environ.get("POLL_INTERVAL", "60"))
    )
    scrape_timeout: float = field(
        default_factory=lambda: float(os.environ.get("SCRAPE_TIMEOUT", "300"))
    )

    # Submission
    batch_size: int = field(
        default_factory=lambda: int(os.environ.get("BATCH_SIZE", "50"))
    )
    max_retries: int = field(
        default_factory=lambda: int(os.environ.get("MAX_RETRIES", "3"))
    )
    retry_base_delay: float = field(
        default_factory=lambda: float(os.environ.get("RETRY_BASE_DELAY", "1.0"))
    )
    s3_upload_timeout: float = field(
        default_factory=lambda: float(os.environ.get("S3_UPLOAD_TIMEOUT", "60"))
    )

    # Scraper config
    config_path: str = field(
        default_factory=lambda: os.environ.get("CONFIG_PATH", "configs/main.yaml")
    )
    no_stealth: bool = field(
        default_factory=lambda: os.environ.get("NO_STEALTH", "false").lower() == "true"
    )

    def __repr__(self) -> str:
        return (
            f"WorkerConfig(worker_id={self.worker_id}, "
            f"vps_id={self.vps_id}, "
            f"orchestrator_url={self.orchestrator_url}, "
            f"proxy={self.proxy_bind_ip}:{self.proxy_port})"
        )

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        """Create configuration from environment variables."""
        return cls()
