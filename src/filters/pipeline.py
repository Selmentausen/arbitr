"""
Filter Pipeline Manager.

Orchestrates multi-stage filtering of court cases.
Currently implements Stage 1 (keyword screening).
Future stages: HTML analysis, PDF analysis, LLM analysis.
"""

from typing import List, Optional

from src.models.case import Case, CaseBase, StatusEnum
from src.config.manager import ConfigManager
from src.filters.stage1_screen import stage1_initial_screen
from src.utils.logger import get_logger

logger = get_logger(__name__)


class FilterPipeline:
    """
    Multi-stage filter pipeline for court cases.

    Runs cases through configured filter stages, skipping further
    analysis when score is definitively high or low.
    """

    def __init__(self, config: ConfigManager):
        """
        Initialize pipeline.

        Args:
            config: Configuration manager with thresholds and rules
        """
        self.config = config
        self.thresholds = config.get_thresholds()

    def process_case(self, case: CaseBase) -> Case:
        """
        Run a single case through the filter pipeline.

        Args:
            case: Basic case data

        Returns:
            Fully processed Case with scoring and categorization
        """
        # Stage 1: Initial keyword screening
        result = stage1_initial_screen(case, self.config)

        # Check if we can skip further stages
        high_threshold = self.thresholds.get("high", 80)
        low_threshold = self.thresholds.get("low", 20)

        if result.relevance_score >= high_threshold:
            logger.info(
                f"Case {result.case_number}: score={result.relevance_score:.1f} → HIGH_RELEVANT (skipping further stages)"
            )
            return result

        if result.relevance_score <= low_threshold:
            logger.info(
                f"Case {result.case_number}: score={result.relevance_score:.1f} → REJECT (skipping further stages)"
            )
            return result

        # Future: Stage 2 (HTML analysis), Stage 3 (PDF), Stage 4 (LLM)
        # These will be added as the system matures:
        #
        # if result.status in (StatusEnum.UNCERTAIN, StatusEnum.INSUFFICIENT_INFO):
        #     result = stage2_html_analyze(result, self.config)
        #
        # if result.status == StatusEnum.UNCERTAIN:
        #     result = stage3_pdf_analyze(result, self.config)
        #
        # gray_min = self.thresholds.get("gray_min", 40)
        # gray_max = self.thresholds.get("gray_max", 60)
        # if gray_min <= result.relevance_score <= gray_max:
        #     result = stage4_llm_analyze(result, self.config)

        logger.info(
            f"Case {result.case_number}: score={result.relevance_score:.1f} → {result.status.value}"
        )
        return result

    def process_batch(self, cases: List[CaseBase]) -> List[Case]:
        """
        Process a batch of cases through the pipeline.

        Args:
            cases: List of basic case data

        Returns:
            List of processed Cases with scoring
        """
        logger.info(f"Processing batch of {len(cases)} cases...")
        results = []

        for case in cases:
            try:
                result = self.process_case(case)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to process case {case.case_number}: {e}")
                # Create a case with error status
                error_case = Case(**case.model_dump())
                error_case.status = StatusEnum.INSUFFICIENT_INFO
                error_case.extracted_data["error"] = str(e)
                results.append(error_case)

        # Log summary
        status_counts = {}
        for r in results:
            status = r.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

        logger.info(f"Batch complete: {len(results)} cases processed")
        for status, count in status_counts.items():
            logger.info(f"  {status}: {count}")

        return results
