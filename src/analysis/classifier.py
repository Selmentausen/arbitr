"""LLM-based legal area classifier."""

from pathlib import Path
from typing import Optional

from src.analysis.context_builder import build_case_dossier
from src.analysis.models import BuiltPrompt, ClassificationResult, LLMClassificationResponse
from src.analysis.ollama_client import OllamaClient, OllamaError, create_ollama_client
from src.analysis.pdf_extractor import enrich_case_with_pdf_text
from src.analysis.prompt_builder import build_prompt
from src.config.classification import ClassificationConfig
from src.models.case import Case
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _normalize_probabilities(
    raw: dict[str, float], category_ids: list[str]
) -> dict[str, float]:
    """Ensure all categories present and sum to 1.0."""
    probs = {cid: float(max(0.0, raw.get(cid, 0.0))) for cid in category_ids}
    total = sum(probs.values())
    if total <= 0:
        n = len(category_ids)
        return {cid: 1.0 / n for cid in category_ids}
    return {cid: probs[cid] / total for cid in category_ids}


def _response_to_result(
    response: LLMClassificationResponse,
    category_ids: list[str],
    prompt_version: str,
    model: str,
) -> ClassificationResult:
    probs = _normalize_probabilities(response.probabilities, category_ids)
    primary = max(probs, key=probs.get)
    return ClassificationResult(
        probabilities=probs,
        primary_category=primary,
        inferred_other_category=response.inferred_other_category,
        confidence=probs[primary],
        reasoning=response.reasoning,
        key_signals=response.key_signals,
        uncertainty=response.uncertainty,
        prompt_version=prompt_version,
        model=model,
    )


def prepare_case_for_classification(
    case: Case,
    config: ClassificationConfig,
    pdf_dir: Path,
    skip_pdf: bool = False,
) -> tuple[Case, BuiltPrompt]:
    """Extract PDF text if needed and build the prompt."""
    case = enrich_case_with_pdf_text(case, pdf_dir, config, skip_pdf=skip_pdf)
    dossier = build_case_dossier(case, config)
    prompt = build_prompt(dossier, config)
    return case, prompt


def classify_case(
    case: Case,
    config: ClassificationConfig,
    pdf_dir: Path,
    *,
    skip_pdf: bool = False,
    use_fast: bool = False,
    client: Optional[OllamaClient] = None,
    dry_run: bool = False,
) -> tuple[Case, Optional[ClassificationResult], BuiltPrompt]:
    """
    Classify a case by legal area.

    Returns (updated_case, result_or_none_if_dry_run, built_prompt).
    """
    case, prompt = prepare_case_for_classification(case, config, pdf_dir, skip_pdf=skip_pdf)

    if dry_run:
        return case, None, prompt

    ollama = client or create_ollama_client(config, use_fast=use_fast)
    category_ids = config.category_ids

    raw = ollama.chat_json(prompt.system, prompt.user)
    parsed = LLMClassificationResponse.model_validate(raw)
    result = _response_to_result(
        parsed,
        category_ids,
        config.prompt_version,
        ollama.model,
    )
    logger.info(
        "Classified %s → %s (%.0f%%) [%s]",
        case.case_number,
        result.primary_category,
        result.confidence * 100,
        result.uncertainty,
    )
    return case, result, prompt


def build_prompt_audit(case: Case, prompt: BuiltPrompt) -> dict:
    """Metadata and full prompt text saved with each classification."""
    dossier = prompt.dossier
    pdf_texts = case.pdf_texts or []
    return {
        "dossier_chars": len(dossier),
        "pdf_text_blocks": len(pdf_texts),
        "pdf_text_chars": sum(len(t) for t in pdf_texts),
        "includes_pdf_section": "Фрагменты PDF:" in dossier,
        "system_prompt_chars": len(prompt.system),
        "user_prompt_chars": len(prompt.user),
        "system_prompt": prompt.system,
        "user_prompt": prompt.user,
    }


def apply_classification_to_case(
    case: Case,
    result: ClassificationResult,
    config: ClassificationConfig,
    prompt: Optional[BuiltPrompt] = None,
) -> Case:
    """Merge ML result into case and optionally update category/score."""
    storage = result.to_storage_dict()
    if prompt is not None:
        storage["prompt_audit"] = build_prompt_audit(case, prompt)
    case.extracted_data["ml_classification"] = storage

    thresholds = config.get("thresholds", {}) or {}
    auto_threshold = float(thresholds.get("auto_assign_category", 0.70))

    if result.confidence >= auto_threshold:
        if result.primary_category == "other" and result.inferred_other_category:
            case.category = result.inferred_other_category
            case.relevance_score = min(100.0, result.confidence * 100.0)
        elif result.primary_category != "other":
            case.category = result.primary_category
            relevant_prob = result.probabilities.get(result.primary_category, result.confidence)
            case.relevance_score = min(100.0, relevant_prob * 100.0)

    return case
