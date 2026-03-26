"""
Stage 1 Filter: Initial keyword-based screening.

Analyzes basic case data (plaintiff, defendant, court, judges) against
configured area keywords and judge group mappings to assign:
- Category (construction, bankruptcy, etc.)
- Initial relevance score (0-100)
- Status (high_relevant, reject, insufficient_info, uncertain)
"""

import re
from typing import Dict, List, Optional, Any

from src.models.case import Case, CaseBase, StatusEnum
from src.config.manager import ConfigManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


def stage1_initial_screen(case: CaseBase, config: ConfigManager) -> Case:
    """
    Apply initial keyword-based screening to a case.

    Checks plaintiff/defendant names and other basic data against
    configured area keywords. Assigns category, preliminary score, and status.

    Args:
        case: Basic case data from search results
        config: Configuration manager

    Returns:
        Full Case object with scoring/category applied
    """
    # Convert CaseBase to Case if needed
    if isinstance(case, Case):
        full_case = case
    else:
        full_case = Case(**case.model_dump())

    score = 0.0
    matched_area = None
    match_details = {}

    # Get all configured areas
    areas = config.get("areas", {})
    thresholds = config.get_thresholds()

    best_area_score = 0.0
    best_area_name = None

    for area_name, area_rules in areas.items():
        area_score = _score_case_for_area(full_case, area_rules)
        if area_score > best_area_score:
            best_area_score = area_score
            best_area_name = area_name

    score = best_area_score
    matched_area = best_area_name

    # Apply judge group bonus
    judge_bonus = _check_judge_groups(full_case, config)
    if judge_bonus > 0:
        score += judge_bonus
        match_details["judge_group_bonus"] = judge_bonus

    # Cap score at 100
    score = min(score, 100.0)

    # Assign results
    full_case.relevance_score = score
    full_case.category = matched_area

    # Determine status based on thresholds
    high_threshold = thresholds.get("high", 80)
    low_threshold = thresholds.get("low", 20)

    if matched_area is None and score == 0.0:
        # No keywords matched any area — insufficient data to judge
        full_case.status = StatusEnum.INSUFFICIENT_INFO
    elif score >= high_threshold:
        full_case.status = StatusEnum.HIGH_RELEVANT
    elif score <= low_threshold:
        full_case.status = StatusEnum.REJECT
    else:
        full_case.status = StatusEnum.UNCERTAIN

    # Store match details in extracted_data
    full_case.extracted_data["stage1_details"] = match_details
    full_case.extracted_data["stage1_score"] = score

    logger.debug(
        f"Stage 1: case={full_case.case_number}, "
        f"area={matched_area}, score={score:.1f}, status={full_case.status.value}"
    )

    return full_case


def _score_case_for_area(case: Case, area_rules: Dict[str, Any]) -> float:
    """
    Score a case against a specific area's rules.

    Checks keywords in plaintiff/defendant names, and mediation signals.
    """
    score = 0.0
    keywords = area_rules.get("keywords", [])
    weight = area_rules.get("weight", 30)
    mediation_signals = area_rules.get("mediation_signals", [])

    # Combine searchable text
    searchable_text = " ".join([
        case.plaintiff.lower(),
        case.defendant.lower(),
        case.court.lower(),
    ]).lower()

    # Keyword matching
    keyword_matches = 0
    for keyword in keywords:
        if keyword.lower() in searchable_text:
            keyword_matches += 1

    if keyword_matches > 0:
        # Base score from keyword weight
        score += weight
        # Bonus for multiple keyword matches
        score += min(keyword_matches - 1, 3) * 10

    # Mediation signal bonus (if available in extracted data)
    # These will be more useful in later stages when we have case page HTML
    for signal in mediation_signals:
        if signal.lower() in searchable_text:
            score += 15

    return score


def _check_judge_groups(case: Case, config: ConfigManager) -> float:
    """
    Check if the case court belongs to a region with specialized judge groups.
    Returns bonus score based on court region match.
    """
    judge_groups = config.get_judge_groups()
    bonus = 0.0

    # Check if the court name matches a known region
    court_lower = case.court.lower()

    for region, groups in judge_groups.items():
        region_match = False
        if region == "moscow" and "москв" in court_lower:
            region_match = True
        elif region == "saint_petersburg" and ("петербург" in court_lower or "спб" in court_lower):
            region_match = True

        if region_match:
            # Court is in a region with specialized judge groups → bonus
            bonus += 10.0
            break

    return bonus
