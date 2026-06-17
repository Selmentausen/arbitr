"""
High-level case collection orchestrator.
Manages pagination, progress tracking, and data persistence.
"""

import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from src.scraper.api_client import KadApiClient
from src.scraper.parser import parse_case_list
from src.models.case import CaseBase
from src.utils.logger import get_logger


logger = get_logger(__name__)


class CaseCollector:
    """Orchestrates case collection with pagination and progress tracking."""
    
    def __init__(self, api_client: KadApiClient):
        """
        Initialize collector.
        
        Args:
            api_client: Initialized KadApiClient instance
        """
        self.api_client = api_client
    
    def collect_cases(
        self,
        judge_name: str,
        max_cases: int = 100,
        page_size: int = 25,
        save_raw: bool = True
    ) -> List[CaseBase]:
        """
        Collect cases for a given judge with pagination.
        
        Args:
            judge_name: Judge name (e.g., "Титова Е. В.")
            max_cases: Maximum number of cases to collect
            page_size: Number of cases per page
            save_raw: Whether to save raw HTML responses
            
        Returns:
            List of CaseBase objects
        """
        logger.info(f"Starting case collection: judge={judge_name}, max_cases={max_cases}")
        
        # Step 1: Get judge ID
        judge_id = self.api_client.get_judge_id(judge_name)
        if not judge_id:
            logger.error(f"Could not find judge ID for '{judge_name}'")
            return []
        
        # Step 2: Collect cases with pagination
        all_cases = []
        page = 1
        total_count = None
        
        while len(all_cases) < max_cases:
            logger.info(f"Fetching page {page} (collected {len(all_cases)}/{max_cases} cases so far)")
            
            try:
                # Fetch page
                html_response = self.api_client.search_cases(
                    judge_id=judge_id,
                    page=page,
                    count=page_size
                )
                
                # Save raw HTML if requested
                if save_raw:
                    self._save_raw_response(judge_id, page, html_response)
                
                # Parse cases
                cases, pagination = parse_case_list(html_response)
                
                if not cases:
                    logger.warning(f"No cases found on page {page}, stopping collection")
                    break
                
                # Update total count from first page
                if total_count is None:
                    total_count = pagination.get("total_count", 0)
                    logger.info(f"Total cases available: {total_count}")
                
                # Add cases (up to max_cases limit)
                remaining = max_cases - len(all_cases)
                all_cases.extend(cases[:remaining])
                
                logger.info(f"Collected {len(cases)} cases from page {page} ({len(all_cases)}/{max_cases} total)")
                
                # Check if we've reached the end
                if len(cases) < page_size or len(all_cases) >= max_cases:
                    break
                
                page += 1
                
            except Exception as e:
                logger.error(f"Error fetching page {page}: {e}")
                break
        
        logger.info(f"Collection complete: {len(all_cases)} cases collected")
        return all_cases
    
    def save_to_json(self, cases: List[CaseBase], filepath: str):
        """
        Save cases to JSON file.
        
        Args:
            cases: List of CaseBase objects
            filepath: Output file path
        """
        output_path = Path(filepath)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert to JSON-serializable format
        cases_data = [case.model_dump(mode='json') for case in cases]
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(cases_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved {len(cases)} cases to {output_path}")
    
    def _save_raw_response(self, judge_id: str, page: int, html_content: str):
        """
        Save raw HTML response for debugging.
        
        Args:
            judge_id: Judge UUID
            page: Page number
            html_content: Raw HTML content
        """
        raw_dir = Path("data/raw")
        raw_dir.mkdir(parents=True, exist_ok=True)
        
        # Use short judge ID for filename
        judge_id_short = judge_id.split('-')[0]
        filename = f"judge_{judge_id_short}_page_{page}.html"
        filepath = raw_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.debug(f"Saved raw HTML to {filepath}")


def collect_and_save(
    judge_name: str,
    max_cases: int = 100,
    output_file: str = "data/cases.json",
    config_path: Optional[str] = None
) -> int:
    """
    Convenience function to collect cases and save to file.
    
    Args:
        judge_name: Judge name
        max_cases: Maximum number of cases to collect
        output_file: Output JSON file path
        config_path: Path to config file (optional)
        
    Returns:
        Number of cases collected
    """
    from src.config.manager import load_config
    
    # Load configuration
    config = load_config(config_path)
    
    # Create API client and collector
    with KadApiClient(config) as client:
        collector = CaseCollector(client)
        
        # Collect cases
        cases = collector.collect_cases(judge_name, max_cases=max_cases)
        
        # Save to file
        if cases:
            collector.save_to_json(cases, output_file)
        
        return len(cases)
