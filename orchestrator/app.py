"""
FastAPI application factory for the orchestrator.

This is the main entry point for the central server.
Run with: uvicorn orchestrator.app:app --host 0.0.0.0 --port 8000
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from orchestrator.config import get_config
from orchestrator.routers import workers, jobs, cases, dashboard_api, fleet
from orchestrator.services.s3_client import S3Client
from orchestrator.services.rotation_service import RotationService
from orchestrator.services.timeweb_client import TimewebClient
from orchestrator.services.worker_monitor import monitor_loop
from src.storage.database import init_db
from src.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    On startup:
      1. Initialize database connection
      2. Initialize S3 client (if configured)
      3. Initialize rotation service (Timeweb client + rate limiting)
      4. Start background monitor task

    On shutdown:
      1. Cancel background tasks
      2. Close Timeweb client connections
    """
    config = get_config()

    # 1. Initialize database connection
    logger.info("Initializing database...")
    init_db()
    logger.info(
        "Database initialized (dialect: %s)",
        "postgresql" if config.database_url.startswith("postgresql") else "sqlite",
    )

    # 2. Initialize S3 client
    s3_client = S3Client(
        endpoint=config.s3_endpoint,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
        bucket=config.s3_bucket,
        region=config.s3_region,
        secure=config.s3_secure,
    )
    if s3_client.is_configured():
        logger.info("S3 client configured: %s/%s", s3_client.endpoint, s3_client.bucket)
    else:
        logger.warning(
            "S3 not configured. Set S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY. "
            "PDF uploads will not be available."
        )
    app.state.s3_client = s3_client

    # 3. Initialize rotation service
    timeweb_client = TimewebClient(api_token=config.timeweb_api_token)
    rotation_service = RotationService(
        timeweb_client=timeweb_client,
        max_per_hour=config.rotation_max_per_hour,
        max_per_day=config.rotation_max_per_day,
        cooldown_minutes=config.rotation_cooldown_minutes,
        command_expiry_minutes=config.rotation_command_expiry_minutes,
    )
    if timeweb_client.is_configured():
        logger.info("Timeweb API configured for IP rotation")
    else:
        logger.warning(
            "Timeweb API not configured. IP rotation will be disabled."
        )
    app.state.rotation_service = rotation_service

    # 4. Start background monitor task
    monitor_task = asyncio.create_task(
        monitor_loop(config, rotation_service)
    )
    logger.info("Background worker monitor started")

    # 5. Initialize fleet control state
    app.state.scraping_paused = True
    logger.info("Fleet scraping state: INACTIVE")

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down orchestrator...")
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass

    await timeweb_client.close()
    logger.info("Orchestrator shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Arbitr Orchestrator",
        description="Central control server for the distributed VPS scraping fleet",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Register routers
    app.include_router(workers.router)
    app.include_router(jobs.router)
    app.include_router(cases.router)
    app.include_router(dashboard_api.router)
    app.include_router(fleet.router)

    @app.get("/health")
    async def health_check():
        """Health check endpoint (no auth required)."""
        return {"status": "ok", "service": "arbitr-orchestrator"}

    return app


# Module-level app instance for uvicorn
app = create_app()
