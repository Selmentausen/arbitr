"""
Manage a local SOCKS proxy (microsocks) bound to a specific outgoing IP.

One ProxyManager = one microsocks process = one outgoing IP.
On a multi-IP VPS, run one worker per ProxyManager instance.
"""
import sys
import os
import subprocess
import time
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _get_default_interface() -> str:
    """Detect the default network interface on Linux."""
    if sys.platform != "linux":
        return "eth0"
    try:
        res = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout:
            parts = res.stdout.split()
            if "dev" in parts:
                idx = parts.index("dev")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
    except Exception as e:
        logger.warning("Failed to auto-detect default interface: %s", e)
    return "eth0"


class ProxyManager:
    """
    Starts, stops, and rebinds a microsocks instance, and manages OS-level
    IP address assignment on the network interface.

    Usage:
        proxy = ProxyManager(bind_ip="1.2.3.4", port=10001)
        proxy.start()
        ...
        proxy.restart(new_bind_ip="5.6.7.8")
        ...
        proxy.stop()
    """

    def __init__(self, bind_ip: str, port: int, net_interface: Optional[str] = None):
        self.bind_ip = bind_ip
        self.port = port
        self.net_interface = net_interface
        self._process: Optional[subprocess.Popen] = None

    def _run_ip_cmd(self, cmd_args: list) -> bool:
        """Run an ip route/addr command, prefixing with sudo if not root."""
        is_root = os.getuid() == 0 if hasattr(os, "getuid") else False
        cmd = cmd_args if is_root else ["sudo"] + cmd_args
        try:
            logger.info("Running IP command: %s", " ".join(cmd))
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                return True
            
            # If the IP is already added or not found during deletion, that's fine
            stderr_lower = res.stderr.lower()
            if "exists" in stderr_lower or "not found" in stderr_lower or "cannot assign" in stderr_lower:
                logger.info("IP command returned expected warning/exist code: %s", res.stderr.strip())
                return True
                
            logger.error("IP command failed: %s. Stderr: %s", " ".join(cmd), res.stderr.strip())
        except Exception as e:
            logger.error("Failed to execute IP command %s: %s", " ".join(cmd), e)
        return False

    def start(self) -> None:
        """Start microsocks bound to the given IP and port, first binding IP to OS interface."""
        if self._process is not None and self._process.poll() is None:
            return  # already running

        # Bind IP to the network interface if on Linux
        if sys.platform == "linux" and self.bind_ip and self.bind_ip != "127.0.0.1":
            interface = self.net_interface or _get_default_interface()
            logger.info("Binding secondary IP %s/24 to interface %s...", self.bind_ip, interface)
            self._run_ip_cmd(["ip", "addr", "add", f"{self.bind_ip}/24", "dev", interface])

        cmd = [
            "microsocks",
            "-i", "127.0.0.1",      # listen only on localhost
            "-p", str(self.port),   # port
            "-b", self.bind_ip,     # bind outgoing to this IP
        ]

        logger.info("Starting microsocks on port %d bound to outgoing IP %s...", self.port, self.bind_ip)
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        time.sleep(0.5)  # let it bind

        if self._process.poll() is not None:
            # Try to clean up address if microsocks failed to start
            if sys.platform == "linux" and self.bind_ip and self.bind_ip != "127.0.0.1":
                interface = self.net_interface or _get_default_interface()
                self._run_ip_cmd(["ip", "addr", "del", f"{self.bind_ip}/24", "dev", interface])
            raise RuntimeError(
                f"microsocks failed to start on {self.bind_ip}:{self.port} "
                f"(exit code {self._process.poll()})"
            )

    def stop(self) -> None:
        """Stop the microsocks process and unbind the IP address from OS interface."""
        if self._process is not None:
            logger.info("Stopping microsocks on port %d...", self.port)
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=1)
            except Exception as e:
                logger.error("Error stopping microsocks: %s", e)
            finally:
                self._process = None

        # Unbind IP from network interface if on Linux
        if sys.platform == "linux" and self.bind_ip and self.bind_ip != "127.0.0.1":
            interface = self.net_interface or _get_default_interface()
            logger.info("Unbinding secondary IP %s/24 from interface %s...", self.bind_ip, interface)
            self._run_ip_cmd(["ip", "addr", "del", f"{self.bind_ip}/24", "dev", interface])

    def restart(self, new_bind_ip: Optional[str] = None) -> None:
        """Restart with optionally a new outgoing IP."""
        # Stop will clean up and unbind the current ip address
        self.stop()
        if new_bind_ip:
            self.bind_ip = new_bind_ip
        # Start will bind the new ip address and start microsocks
        self.start()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def __repr__(self) -> str:
        return f"ProxyManager(bind_ip={self.bind_ip}, port={self.port}, interface={self.net_interface})"
