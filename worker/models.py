"""
Internal data models for the worker.
No external dependencies except Pydantic.
"""
from typing import Optional
from pydantic import BaseModel


class ScrapeResult(BaseModel):
    """
    Result of scraping a single judge.

    Cases are submitted incrementally via on_batch_ready callback during
    scraping, so this model only tracks totals and status — not the
    cases themselves.
    """

    judge_name: str
    total_cases_found: int = 0
    cases_after_filter: int = 0
    site_total: int = 0  # Total cases reported by the site for this judge
    is_blocked: bool = False
    block_reason: str = ""
    error: Optional[str] = None
    success: bool = False
