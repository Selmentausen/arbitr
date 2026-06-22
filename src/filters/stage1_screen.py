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

from src.models.case import Case, CaseParticipant, StatusEnum
from src.config.manager import ConfigManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


def stage1_initial_screen(case: Case, config: ConfigManager) -> Case:
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
    # Seed the participants dictionary from plaintiff/defendant strings
    full_case = case
    if full_case.plaintiff and full_case.plaintiff != "Unknown":
        full_case.participants["plaintiff"] = [CaseParticipant(name=full_case.plaintiff)]
    if full_case.defendant and full_case.defendant != "Unknown":
        for def_name in full_case.defendant.split(", "):
            if def_name.strip():
                full_case.participants.setdefault("defendant", []).append(
                    CaseParticipant(name=def_name.strip())
                )

    # Get all configured areas
    areas = config.get("areas", {})
    thresholds = config.get_thresholds()
    reject_enabled = config.get("filtering.reject_enabled", True)
    global_reject_keywords = config.get("global_reject_keywords", [])

    if reject_enabled:
        # Pre filter: Case Type
        case_type_result = _pre_filter_case_type(full_case, areas)
        if case_type_result == "reject":
            full_case.status = StatusEnum.REJECT
            full_case.extracted_data["reject_reason"] = (
                f"case_type '{full_case.case_type}' not in allowed types"
            )
            return full_case
        if case_type_result == "unknown":
            full_case.status = StatusEnum.INSUFFICIENT_INFO
            full_case.extracted_data["reject_reason"] = "case_type unknown from search results"

        reject_match = _pre_filter_reject_keywords(
            full_case, global_reject_keywords, areas
        )
        if reject_match:
            full_case.status = StatusEnum.REJECT
            full_case.relevance_score = 0.0
            full_case.extracted_data["reject_reason"] = (
                f"reject keyword matched: '{reject_match}'"
            )
            return full_case

    score = 0.0
    best_area_name = None
    best_area_score = 0.0

    for area_name, area_rules in areas.items():
        area_score = _score_company_name_match(full_case, area_rules)
        if area_score > best_area_score:
            best_area_score = area_score
            best_area_name = area_name

    score = best_area_score

    # Apply judge group bonus
    judge_bonus = _check_judge_groups(full_case, config)
    if judge_bonus > 0:
        score += judge_bonus
        full_case.extracted_data["judge_group_bonus"] = judge_bonus

    # Cap score at 100
    score = min(score, 100.0)

    # Assign results
    full_case.relevance_score = score
    full_case.category = best_area_name

    # Determine status based on thresholds
    high_threshold = thresholds.get("high", 80)
    low_threshold = thresholds.get("low", 20)

    if best_area_name is None and score == 0.0:
        # No keywords matched any area — insufficient data to judge
        full_case.status = StatusEnum.INSUFFICIENT_INFO
    elif score >= high_threshold:
        full_case.status = StatusEnum.HIGH_RELEVANT
    elif score <= low_threshold:
        full_case.status = (
            StatusEnum.REJECT if reject_enabled else StatusEnum.UNCERTAIN
        )
    else:
        full_case.status = StatusEnum.UNCERTAIN

    # Store match details in extracted_data
    full_case.extracted_data["stage1_score"] = score

    logger.debug(
        f"Stage 1: case={full_case.case_number}, "
        f"area={best_area_name}, "
        f"score={score:.1f}, status={full_case.status.value}"
    )

    return full_case


def _pre_filter_case_type(case: Case, areas: Dict[str, Any]) -> str:
    """
    Check if the case type is allowed by any area config.

    Returns:
        "pass" - case type is allowed
        "reject" - case type is explicitly not allowed
        "unknown" - case type couldn't be determined
    """
    if case.case_type is None:
        return "unknown"
    
    for area_name, area_rules in areas.items():
        allowed_types = area_rules.get("allowed_case_types", [])
        if not allowed_types:
            return "pass"
        if case.case_type in allowed_types:
            return "pass"
    return "reject"

def _pre_filter_reject_keywords(case: Case, global_reject_keywords: List[str], areas: Dict[str, any]) -> Optional[str]:
    """
    Check plaintiff/defendant names against reject keywrod lists.

    Returns:
        The matched keyword string if rejected, None if passed.
    """
    searchable_text = f"{case.plaintiff} {case.defendant}".lower()
    for kw in global_reject_keywords:
        if kw.lower() in searchable_text:
            return kw
    
    for area_name, area_rules in areas.items():
        for kw in area_rules.get("reject_keywords", []):
            if kw.lower() in searchable_text:
                return kw
    
    return None


def _score_company_name_match(case: Case, area_rules: Dict[str, Any]) -> float:
    """
    Score a case based on keyword matches in plaintiff/defendant names.
    
    This answers: "Is a party likely related to this area (e.g. construction)?"
    It does NOT answer: "Is this case about a construction dispute?"
    """
    score = 0.0
    keywords = area_rules.get("keywords", [])
    weight = area_rules.get("company_match_weight", area_rules.get("weight", 20))

    searchable_text = f"{case.plaintiff} {case.defendant}".lower()
    
    matched_keywords = []
    for kw in keywords:
        kw = kw.lower()
        if len(kw) <= 3:
            pattern = rf'(?:^|[\s"\'«»()\-,.]){re.escape(kw)}(?:[\s"\'«»()\-,.]|$)'
            if re.search(pattern, searchable_text):
                matched_keywords.append(kw)
        else:
            if kw in searchable_text:
                matched_keywords.append(kw)
    
    if matched_keywords:
        score += weight
        score += min(len(matched_keywords) - 1, 3) * (weight * 0.3)
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
