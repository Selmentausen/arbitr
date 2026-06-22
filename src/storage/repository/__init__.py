"""Repository package — backward-compatible monolithic import available."""

from src.storage.repository.base import BaseRepository
from src.storage.repository.cases import CaseRepository as _CaseCore
from src.storage.repository.ml import MLRepository
from src.storage.repository.stats import StatsRepository
from src.storage.repository.judge_progress import JudgeProgressRepository
from src.storage.repository.distributed import DistributedRepository


class CaseRepository(_CaseCore, MLRepository, StatsRepository, JudgeProgressRepository, DistributedRepository):
    """Backward-compatible repository with all methods from the original monolithic class."""
    pass


__all__ = [
    "BaseRepository",
    "CaseRepository",
    "MLRepository",
    "StatsRepository",
    "JudgeProgressRepository",
    "DistributedRepository",
]
