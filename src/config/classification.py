"""Loader for ML classification configuration."""

from pathlib import Path
from typing import Any, Optional

import yaml

from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CLASSIFICATION_PATH = (
    Path(__file__).parent.parent.parent / "configs" / "classification.yaml"
)


class ClassificationConfig:
    """Configuration for LLM-based case classification."""

    def __init__(self, config_path: Optional[str | Path] = None):
        path = Path(config_path) if config_path else DEFAULT_CLASSIFICATION_PATH
        if not path.exists():
            raise FileNotFoundError(f"Classification config not found: {path}")
        with open(path, encoding="utf-8") as f:
            self._config: dict[str, Any] = yaml.safe_load(f) or {}
        self.config_path = path
        logger.debug("Loaded classification config from %s", path)

    @property
    def prompt_version(self) -> str:
        return str(self._config.get("prompt_version", "1.0"))

    @property
    def categories(self) -> list[dict[str, str]]:
        return self._config.get("categories", [])

    @property
    def category_ids(self) -> list[str]:
        return [c["id"] for c in self.categories]

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value: Any = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value

    @property
    def config(self) -> dict[str, Any]:
        return self._config
