"""
Stage 2 Filter: Analyzes full case card data.

Checks newly extracted participants (third parties/others), instances,
and case result texts against Stage 2 keywords.
"""

import re
from typing import Dict, Any

from src.models.case import Case, StatusEnum
from src.config.manager import ConfigManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

def stage2_html_analyze(case: Case, config: ConfigManager) -> Case:
    """
    Apply stage 2 keyword screening.
    
    Args:
        case: Full Case object enriched with HTML participant/instance data
        config: Configuration manager
        
    Returns:
        Updated Case object
    """
    if case.status not in (StatusEnum.UNCERTAIN, StatusEnum.INSUFFICIENT_INFO):
        return case

    # Make sure we have the area config
    area = case.category
    areas = config.get("areas", {})
    
    # If no category, try finding one or just fallback to construction by default
    if not area:
        area = "construction"
        case.category = area
        
    area_rules = areas.get(area, {})
    
    stage2_keywords = area_rules.get("stage2_keywords", [])
    if not stage2_keywords:
        logger.warning(f"No stage 2 keywords defined for area: {area}")
        return case

    # Build searchable text from Stage 2 data
    text_parts = []
    
    # Third parties and Others
    for role in ["third_party", "other_party"]:
        for p in case.participants.get(role, []):
            text_parts.append(p.name)
            if p.address:
                text_parts.append(p.address)
            
    # Instances text (e.g. results of hearings)
    for inst in case.instances:
        text_parts.append(inst.court_name)
    
    for court, meta in case.extracted_data.items():
        if isinstance(meta, dict) and "result" in meta:
            text_parts.append(meta["result"])
            
    searchable_text = " ".join(text_parts).lower()
    
    # Keyword matching
    matches = 0
    match_details = []
    weight = area_rules.get("stage2_weight", 40)
    
    for kw in stage2_keywords:
        keyword = kw.lower().strip()
        if not keyword:
            continue
            
        if len(keyword) <= 3:
            pattern = rf'(?:^|[\s"\'«»()\-,.]){re.escape(keyword)}(?:[\s"\'«»()\-,.]|$)'
            if re.search(pattern, searchable_text):
                matches += 1
                match_details.append(keyword)
        else:
            if keyword in searchable_text:
                matches += 1
                match_details.append(keyword)
                
    if matches > 0:
        case.relevance_score += weight
        case.relevance_score += min(matches - 1, 3) * 10
        case.relevance_score = min(case.relevance_score, 100.0)
        
        # Re-evaluate status
        thresholds = config.get_thresholds()
        if case.relevance_score >= thresholds.get("high", 80):
            case.status = StatusEnum.HIGH_RELEVANT
        else:
            case.status = StatusEnum.UNCERTAIN
            
        case.extracted_data["stage2_matches"] = match_details
        
    logger.debug(f"Stage 2: case={case.case_number}, matches={matches}, new_score={case.relevance_score:.1f}, status={case.status.value}")
    
    return case