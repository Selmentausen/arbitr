"""
Manage a local SOCKS proxy (microsocks) bound to a specific outgoing IP.

One ProxyManager = one microsocks process = one outgoing IP.
On a multi-IP VPS, run one worker per ProxyManager instance.
"""
import subprocess
import time
import signal
import os
from typing import Optional


class ProxyManager:
    """
    Starts, stops, and rebinds a microsocks instance.

    Usage:
        proxy = ProxyManager(bind_ip="1.2.3.4", port=10001)
        proxy.start()
        ...
        proxy.restart(new_bind_ip="5.6.7.8")
        ...
        proxy.stop()
    """

    def __init__(self, bind_ip: str, port: int):
        self.bind_ip = bind_ip
        self.port = port
        self._process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        """Start microsocks bound to the given IP and port."""
        if self._process is not None and self._process.poll() is None:
            return  # already running

        cmd = [
            "microsocks",
            "-i", "127.0.0.1",      # listen only on localhost
            "-p", str(self.port),   # port
            "-b", self.bind_ip,     # bind outgoing to this IP
        ]

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        time.sleep(0.5)  # let it bind

        if self._process.poll() is not None:
            raise RuntimeError(
                f"microsocks failed to start on {self.bind_ip}:{self.port} "
                f"(exit code {self._process.poll()})"
            )

    def stop(self) -> None:
        """Stop the microsocks process."""
        if self._process is None:
            return

        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1)
        except Exception:
            pass
        finally:
            self._process = None

    def restart(self, new_bind_ip: Optional[str] = None) -> None:
        """Restart with optionally a new outgoing IP."""
        self.stop()
        if new_bind_ip:
            self.bind_ip = new_bind_ip
        self.start()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def __repr__(self) -> str:
        return f"ProxyManager(bind_ip={self.bind_ip}, port={self.port})"
