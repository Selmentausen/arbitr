"""Base repository with shared helpers and session management."""
import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.storage.database import get_session, is_postgres
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _serialize_json(data: Any) -> Any:
    """Serialize data for JSON column storage (native dict on PG, string on SQLite)."""
    if is_postgres():
        return data
    return json.dumps(data, ensure_ascii=False)


def _deserialize_json(raw: Any, default: Any = None) -> Any:
    """Deserialize data from a JSON column (native dict on PG, string on SQLite)."""
    if raw is None:
        return default if default is not None else {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


class BaseRepository:
    """Shared session management for all domain repositories."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session
        self._owns_session = session is None

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = get_session()
        return self._session

    def close(self) -> None:
        if self._owns_session and self._session is not None:
            self._session.close()
