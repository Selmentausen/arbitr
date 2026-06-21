"""Scraper module for kad.arbitr.ru API interaction."""

from .parser import parse_case_list, parse_judge_suggest

__all__ = [
    "parse_case_list",
    "parse_judge_suggest",
]
