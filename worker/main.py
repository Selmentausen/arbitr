"""
Arbitr Worker — standalone scraper for the VPS fleet.

Entry point:  python -m worker.main

What it does:
  1. Reads config from environment variables (see worker.config)
  2. Starts a local SOCKS proxy (microsocks) bound to a specific IP
  3. Registers with the FastAPI orchestrator
  4. Runs a heartbeat loop in the background
  5. Claims judges, scrapes them, sends results to the orchestrator
  6. Detects blocks, reports them, and waits for the orchestrator to command IP rotation

No database access. No cloud credentials. No local state.
"""
import asyncio
import signal
import sys
from typing import Any, Dict, List, Optional

from worker.config import WorkerConfig
from worker.client import OrchestratorClient
from worker.proxy import ProxyManager
from worker.scraper import WorkerScraper

from src.config.manager import ConfigManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Graceful shutdown flag
_shutdown = False
_stop_event = asyncio.Event()


def _handle_signal(sig, frame):
    global _shutdown
    logger.info("Received signal %s, initiating graceful shutdown...", sig)
    _shutdown = True
    _stop_event.set()


async def _heartbeat_loop(
    client: OrchestratorClient,
    config: WorkerConfig,
    proxy: Optional[ProxyManager],
):
    """Send periodic heartbeats and let the orchestrator embed commands."""
    while not _shutdown:
        try:
            status = {
                "proxy_running": proxy.is_running() if proxy else None,
            }
            await client.heartbeat(status=status)
        except Exception as e:
            logger.warning("Heartbeat failed: %s", e)
        await asyncio.sleep(config.heartbeat_interval)


async def _execute_command(
    command: dict,
    client: OrchestratorClient,
    proxy: Optional[ProxyManager],
) -> bool:
    """
    Execute a command received from the orchestrator.
    Returns True if successfully executed, False otherwise.
    """
    cmd_type = command.get("type")
    logger.info("Executing command: %s", cmd_type)
    success = True

    if cmd_type == "rotate_ip":
        new_ip = command.get("new_ip")
        proxy_port = command.get(
            "proxy_port", proxy.port if proxy else None
        )

        logger.info(
            "IP rotation: %s -> %s",
            proxy.bind_ip if proxy else "unknown",
            new_ip,
        )

        if proxy and new_ip:
            success = False
            # Retrying proxy restart as binding new IP might take time on VPS OS network interface
            max_attempts = 12
            for attempt in range(max_attempts):
                try:
                    proxy.restart(new_bind_ip=new_ip)
                    # Re-register with the new IP
                    await client.register(
                        ip_address=new_ip,
                        proxy_port=proxy_port,
                    )
                    logger.info("IP rotation complete. New IP: %s", new_ip)
                    success = True
                    break
                except Exception as e:
                    logger.warning(
                        "IP rotation attempt %d/%d failed: %s. Retrying in 10s...",
                        attempt + 1,
                        max_attempts,
                        e,
                    )
                    await asyncio.sleep(10)

            if not success:
                logger.error("IP rotation failed permanently after %d attempts", max_attempts)
                await client.report_blocked(
                    f"Rotation failed permanently after {max_attempts} attempts"
                )
        else:
            logger.error(
                "Cannot rotate: proxy=%s, new_ip=%s", proxy, new_ip
            )
            success = False

    elif cmd_type == "shutdown":
        logger.info("Orchestrator requested shutdown")
        global _shutdown
        _shutdown = True

    elif cmd_type == "pause":
        logger.info("Orchestrator requested pause — will enter idle loop")
        # Actual idling is handled by the main scrape loop

    elif cmd_type == "resume":
        logger.info("Orchestrator requested resume — exiting idle loop")
        # Actual resume is handled by the main scrape loop

    else:
        logger.warning("Unknown command type: %s", cmd_type)
        success = False

    client.clear_command()
    return success


async def _wait_for_rotation(
    client: OrchestratorClient,
    proxy: Optional[ProxyManager],
) -> None:
    """
    Enter a blocked-wait state.
    Poll heartbeat commands until the orchestrator sends a rotate_ip command.
    """
    logger.info(
        "Worker blocked. Waiting for IP rotation command from orchestrator..."
    )
    while not _shutdown:
        await asyncio.sleep(5)

        command = client.get_pending_command()
        if not command:
            continue

        cmd_type = command.get("type")
        success = await _execute_command(command, client, proxy)

        if cmd_type == "rotate_ip":
            if success:
                logger.info("IP rotated successfully. Resuming work.")
                break
            else:
                logger.warning(
                    "IP rotation failed. Staying in block-wait state for new command or retry."
                )


async def run_worker():
    """Main worker event loop."""
    config = WorkerConfig.from_env()

    logger.info("=" * 60)
    logger.info("Arbitr Worker starting")
    logger.info("  Worker ID:        %s", config.worker_id)
    logger.info("  VPS ID:           %s", config.vps_id)
    logger.info("  Orchestrator:     %s", config.orchestrator_url)
    logger.info(
        "  Proxy:            %s:%s",
        config.proxy_bind_ip,
        config.proxy_port,
    )
    logger.info("=" * 60)

    # Initialize components
    client = OrchestratorClient(
        base_url=config.orchestrator_url,
        api_key=config.api_key,
        worker_id=config.worker_id,
        vps_id=config.vps_id,
        max_retries=config.max_retries,
        base_delay=config.retry_base_delay,
    )

    proxy: Optional[ProxyManager] = None
    if config.proxy_port and config.proxy_bind_ip:
        proxy = ProxyManager(
            bind_ip=config.proxy_bind_ip,
            port=config.proxy_port,
            net_interface=config.net_interface,
        )
        try:
            proxy.start()
            logger.info(
                "Proxy started on %s:%d",
                config.proxy_bind_ip,
                config.proxy_port,
            )
        except Exception as e:
            logger.error("Failed to start proxy: %s", e)
            return

    # Load scraper config
    scraper_config = ConfigManager(config.config_path)
    scraper = WorkerScraper(scraper_config)

    current_job: Optional[dict] = None

    try:
        # 1. Register with orchestrator
        logger.info("Registering with orchestrator...")
        reg = await client.register(
            ip_address=config.proxy_bind_ip or "127.0.0.1",
            proxy_port=config.proxy_port,
        )
        logger.info("Registration successful: %s", reg)

        # 2. Start heartbeat loop
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(client, config, proxy)
        )

        # 3. Main scrape loop
        while not _shutdown:
            # Check for pending commands first
            command = client.get_pending_command()
            if command:
                cmd_type = command.get("type")
                success = await _execute_command(command, client, proxy)
                if _shutdown:
                    break
                if cmd_type == "rotate_ip" and not success:
                    await _wait_for_rotation(client, proxy)
                    continue
                if cmd_type == "pause":
                    # Enter idle loop until resume
                    logger.info(
                        "⏸ Scraping paused by orchestrator. Idling..."
                    )
                    while not _shutdown:
                        await asyncio.sleep(config.heartbeat_interval)
                        resume_cmd = client.get_pending_command()
                        if resume_cmd:
                            resume_type = resume_cmd.get("type")
                            await _execute_command(
                                resume_cmd, client, proxy
                            )
                            if _shutdown:
                                break
                            if resume_type == "resume":
                                logger.info(
                                    "▶ Scraping resumed by orchestrator."
                                )
                                break
                            # If it's another pause, stay in the loop
                    continue

            # Claim next job
            logger.info("Polling for next job...")
            job = await client.claim_job()

            if job is None:
                logger.info(
                    "No jobs available, sleeping %ds...", config.poll_interval
                )
                await asyncio.sleep(config.poll_interval)
                continue

            judge_name = job["judge_name"]
            current_job = job
            logger.info("Claimed judge: %s", judge_name)

            # Incremental submission callback — called after each batch
            async def _submit_batch(
                jname: str,
                batch_dicts: List[Dict],
                total_so_far: int,
            ) -> None:
                logger.info(
                    "Submitting batch of %d cases for %s "
                    "(total so far: %d)",
                    len(batch_dicts),
                    jname,
                    total_so_far,
                )
                try:
                    await client.submit_cases(
                        judge_name=jname,
                        cases=batch_dicts,
                        documents=[],
                    )
                    await client.update_progress(
                        jname, cases_collected=total_so_far
                    )
                except Exception as e:
                    logger.error(
                        "Batch submission failed: %s", e
                    )

            # Scrape with resume info + incremental submission
            result = await scraper.scrape_judge(
                judge_name=judge_name,
                proxy_port=config.proxy_port,
                proxy_bind_ip=config.proxy_bind_ip,
                stop_event=_stop_event,
                prev_collected=job.get("cases_collected", 0),
                prev_total=job.get("total_count_at_start", 0),
                max_cases=job.get("max_cases", 0),
                on_batch_ready=_submit_batch,
            )

            # Handle block
            if result.is_blocked:
                logger.warning(
                    "BLOCKED while scraping judge %s: %s",
                    judge_name,
                    result.block_reason,
                )
                # Cases already submitted incrementally — just report block
                try:
                    await client.report_blocked(result.block_reason)
                except Exception as e:
                    logger.error("Failed to report block: %s", e)

                try:
                    await client.release_job(
                        judge_name,
                        f"Blocked: {result.block_reason}",
                    )
                except Exception as e:
                    logger.error("Failed to release job: %s", e)

                current_job = None
                # Wait for orchestrator to command IP rotation
                await _wait_for_rotation(client, proxy)
                continue

            # Handle scrape error
            if not result.success and result.error:
                logger.error(
                    "Scrape failed for judge %s: %s",
                    judge_name,
                    result.error,
                )
                try:
                    await client.fail_job(judge_name, result.error)
                except Exception as e:
                    logger.error("Failed to report job failure: %s", e)
                current_job = None
                await asyncio.sleep(10)
                continue

            # All batches already submitted — just mark complete
            try:
                await client.complete_job(
                    judge_name, result.cases_after_filter
                )
                logger.info(
                    "Judge %s completed: %d cases submitted",
                    judge_name,
                    result.cases_after_filter,
                )
                current_job = None

            except Exception as e:
                logger.error(
                    "Failed to mark job complete for %s: %s",
                    judge_name,
                    e,
                )
                try:
                    await client.fail_job(
                        judge_name, f"Complete call failed: {e}"
                    )
                except Exception:
                    pass
                current_job = None

        # 4. Graceful shutdown
        logger.info("Shutting down gracefully...")

        # Release current job if we have one
        if current_job:
            try:
                await client.release_job(
                    current_job["judge_name"], "Worker shutdown"
                )
            except Exception as e:
                logger.warning("Failed to release job on shutdown: %s", e)

        # Cancel heartbeat
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    except Exception as e:
        logger.error("Worker fatal error: %s", e, exc_info=True)

    finally:
        if proxy:
            proxy.stop()
        await client.close()
        logger.info("Worker %s shut down", config.worker_id)


def main():
    """Entry point."""
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
