"""Scraper module for kad.arbitr.ru API interaction."""

from .api_client import KadApiClient
from .collector import CaseCollector, collect_and_save
from .parser import parse_case_list, parse_judge_suggest

__all__ = [
    "KadApiClient",
    "CaseCollector",
    "collect_and_save",
    "parse_case_list",
    "parse_judge_suggest",
]

