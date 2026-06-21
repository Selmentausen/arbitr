"""
Orchestrator configuration.

Uses environment variables for all secrets and connection strings.
"""

import os
from dataclasses import dataclass, field


@dataclass
class OrchestratorConfig:
    """Central orchestrator configuration."""

    # Database (via PgBouncer)
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL",
            "sqlite:///data/arbitr.db",
        )
    )

    # Redis
    redis_url: str = field(
        default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379")
    )

    # MinIO / S3 (legacy MinIO settings for backwards compat)
    minio_endpoint: str = field(
        default_factory=lambda: os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    )
    minio_access_key: str = field(
        default_factory=lambda: os.environ.get("MINIO_ACCESS_KEY", "arbitr-admin")
    )
    minio_secret_key: str = field(
        default_factory=lambda: os.environ.get("MINIO_SECRET_KEY", "")
    )
    minio_bucket: str = field(
        default_factory=lambda: os.environ.get("MINIO_BUCKET", "arbitr-pdfs")
    )
    minio_secure: bool = field(
        default_factory=lambda: os.environ.get("MINIO_SECURE", "false").lower() == "true"
    )

    # S3 (primary — overrides MinIO if set)
    s3_endpoint: str = field(
        default_factory=lambda: os.environ.get("S3_ENDPOINT", "")
    )
    s3_access_key: str = field(
        default_factory=lambda: os.environ.get("S3_ACCESS_KEY", "")
    )
    s3_secret_key: str = field(
        default_factory=lambda: os.environ.get("S3_SECRET_KEY", "")
    )
    s3_bucket: str = field(
        default_factory=lambda: os.environ.get("S3_BUCKET", "arbitr-pdfs")
    )
    s3_region: str = field(
        default_factory=lambda: os.environ.get("S3_REGION", "ru-1")
    )
    s3_secure: bool = field(
        default_factory=lambda: os.environ.get("S3_SECURE", "true").lower() == "true"
    )

    # API authentication
    api_key: str = field(
        default_factory=lambda: os.environ.get("API_KEY", "dev-key-change-me")
    )

    # Timeweb Cloud API
    timeweb_api_token: str = field(
        default_factory=lambda: os.environ.get("TIMEWEB_API_TOKEN", "")
    )

    # Rotation rate limiting
    rotation_max_per_hour: int = field(
        default_factory=lambda: int(os.environ.get("ROTATION_MAX_PER_HOUR", "3"))
    )
    rotation_max_per_day: int = field(
        default_factory=lambda: int(os.environ.get("ROTATION_MAX_PER_DAY", "10"))
    )
    rotation_cooldown_minutes: int = field(
        default_factory=lambda: int(os.environ.get("ROTATION_COOLDOWN_MINUTES", "15"))
    )
    rotation_command_expiry_minutes: int = field(
        default_factory=lambda: int(os.environ.get("ROTATION_COMMAND_EXPIRY_MINUTES", "30"))
    )

    # Worker monitoring
    heartbeat_timeout_minutes: int = 10  # Reclaim judge after this
    worker_heartbeat_timeout_minutes: int = 5  # Mark worker stale after this
    monitor_interval_seconds: int = 60  # Background check interval

    # Server
    host: str = field(
        default_factory=lambda: os.environ.get("HOST", "0.0.0.0")
    )
    port: int = field(
        default_factory=lambda: int(os.environ.get("PORT", "8000"))
    )


def get_config() -> OrchestratorConfig:
    """Get orchestrator configuration from environment."""
    return OrchestratorConfig()
