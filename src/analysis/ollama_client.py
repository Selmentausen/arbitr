"""HTTP client for Ollama chat API."""

import json
from typing import Optional

import httpx

from src.utils.logger import get_logger

logger = get_logger(__name__)


class OllamaError(Exception):
    """Raised when Ollama request fails."""


class OllamaClient:
    """Thin wrapper around Ollama /api/chat."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:14b",
        temperature: float = 0.1,
        timeout_seconds: int = 180,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds

    def chat_json(self, system: str, user: str) -> dict:
        """Send chat request and parse JSON response body."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature},
        }
        url = f"{self.base_url}/api/chat"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as e:
            raise OllamaError(
                f"Cannot connect to Ollama at {self.base_url}. Is 'ollama serve' running?"
            ) from e
        except httpx.HTTPStatusError as e:
            raise OllamaError(f"Ollama HTTP error: {e.response.status_code}") from e
        except httpx.TimeoutException as e:
            raise OllamaError(f"Ollama request timed out after {self.timeout_seconds}s") from e

        content = (data.get("message") or {}).get("content", "")
        if not content:
            raise OllamaError("Empty response from Ollama")

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON from Ollama: %s", content[:500])
            raise OllamaError(f"Invalid JSON from Ollama: {e}") from e

    def ping(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            with httpx.Client(timeout=5) as client:
                r = client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except httpx.HTTPError:
            return False


def create_ollama_client(config, use_fast: bool = False) -> OllamaClient:
    """Build client from ClassificationConfig."""
    ollama_cfg = config.get("ollama", {}) or {}
    model = ollama_cfg.get("fast_model" if use_fast else "model", "qwen2.5:14b")
    return OllamaClient(
        base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
        model=model,
        temperature=float(ollama_cfg.get("temperature", 0.1)),
        timeout_seconds=int(ollama_cfg.get("timeout_seconds", 180)),
    )
