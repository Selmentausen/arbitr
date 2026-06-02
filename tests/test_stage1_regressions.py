"""Regression tests for stage1_screen bugs."""

from pathlib import Path

import pytest

from src.config.manager import ConfigManager
from src.filters.stage1_screen import stage1_initial_screen
from src.models.case import CaseBase


@pytest.fixture
def main_config():
    root = Path(__file__).parent.parent
    return ConfigManager(str(root / "configs" / "main.yaml"))


def _case(plaintiff: str) -> CaseBase:
    return CaseBase(
        id="test-uuid",
        case_number="А40-100/2024",
        court="Арбитражный суд города Москвы",
        judges=[],
        plaintiff=plaintiff,
        defendant="ООО 'Ответчик'",
    )


def test_stage1_does_not_raise_on_debug_log(main_config):
    """matched_area typo caused NameError on every processed case."""
    result = stage1_initial_screen(_case("ООО 'Подряд'"), main_config)
    assert "stage1_score" in result.extracted_data


def test_reject_disabled_assigns_uncertain_not_reject(main_config):
    """With filtering.reject_enabled=false, low scores must not become reject."""
    assert main_config.get("filtering.reject_enabled") is False
    result = stage1_initial_screen(
        _case("ООО 'Рога и Копыта'"),
        main_config,
    )
    assert result.status.value != "reject"


def test_cases_for_enrichment_includes_all_with_url(main_config):
    from src.filters.pipeline import FilterPipeline
    from src.models.case import Case, StatusEnum

    pipeline = FilterPipeline(main_config)
    cases = [
        Case(
            id="1",
            case_number="A40-1/2024",
            court="АС",
            judges=[],
            plaintiff="x",
            defendant="y",
            case_url="https://kad.arbitr.ru/Card/1",
            status=StatusEnum.REJECT,
        ),
        Case(
            id="2",
            case_number="A40-2/2024",
            court="АС",
            judges=[],
            plaintiff="x",
            defendant="y",
            status=StatusEnum.HIGH_RELEVANT,
        ),
    ]
    enrich = pipeline.cases_for_enrichment(cases)
    assert len(enrich) == 1
    assert enrich[0].case_number == "A40-1/2024"


def test_multiple_keywords_add_extra_score(main_config):
    """len(matched_keywords) - 1 was parsed as list - int."""
    single = stage1_initial_screen(_case("ООО 'Подряд'"), main_config)
    multi = stage1_initial_screen(
        _case("ООО 'Подряд Строительство Монтаж'"), main_config
    )
    assert multi.relevance_score > single.relevance_score
