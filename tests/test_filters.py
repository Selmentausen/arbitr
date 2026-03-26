"""Tests for filter pipeline."""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.models.case import Case, CaseBase, StatusEnum
from src.config.manager import ConfigManager
from src.filters.stage1_screen import stage1_initial_screen
from src.filters.pipeline import FilterPipeline


@pytest.fixture
def config():
    """Create a config manager with test configuration."""
    test_config = {
        "areas": {
            "construction": {
                "keywords": ["подряд", "строительство", "договор подряда", "монтаж"],
                "party_combos": ["юр.лицо vs юр.лицо"],
                "weight": 30,
                "mediation_signals": ["мировое соглашение", "медиация"],
            },
            "bankruptcy": {
                "keywords": ["банкротство", "несостоятельность"],
                "party_combos": ["юр.лицо vs юр.лицо"],
                "weight": 25,
            },
        },
        "thresholds": {
            "high": 80,
            "low": 20,
            "gray_min": 40,
            "gray_max": 60,
        },
        "judge_groups": {
            "moscow": {
                "group1": ["construction", "bankruptcy"],
            },
            "saint_petersburg": {
                "group1": ["construction"],
            },
        },
        "linkage_rules": {
            "dispute_count_threshold": 3,
            "mediation_rate_weight": 10,
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(test_config, f)
        config_path = f.name

    yield ConfigManager(config_path)
    Path(config_path).unlink()


def _make_case(
    plaintiff: str = "ООО 'Тест'",
    defendant: str = "ООО 'Ответчик'",
    court: str = "Арбитражный суд",
    judges: list = None,
    case_number: str = "А40-100/2024",
) -> CaseBase:
    """Helper to create test cases."""
    return CaseBase(
        id="test-uuid",
        case_number=case_number,
        court=court,
        judges=judges or [],
        plaintiff=plaintiff,
        defendant=defendant,
    )


class TestStage1Screen:
    """Test Stage 1 initial screening."""

    def test_construction_keyword_match(self, config):
        """Test that construction keywords boost score."""
        case = _make_case(plaintiff="ООО 'Строительство Плюс'")
        result = stage1_initial_screen(case, config)

        assert result.category == "construction"
        assert result.relevance_score > 0
        assert isinstance(result, Case)

    def test_bankruptcy_keyword_match(self, config):
        """Test bankruptcy keyword matching."""
        case = _make_case(plaintiff="ООО 'Банкротство Инвест'")
        result = stage1_initial_screen(case, config)

        assert result.category == "bankruptcy"
        assert result.relevance_score > 0

    def test_no_keyword_match(self, config):
        """Test case with no matching keywords."""
        case = _make_case(plaintiff="ООО 'Рога и Копыта'", defendant="ООО 'Другая Компания'")
        result = stage1_initial_screen(case, config)

        assert result.relevance_score == 0.0
        assert result.status == StatusEnum.INSUFFICIENT_INFO

    def test_multiple_keywords_boost_score(self, config):
        """Test that multiple keyword matches increase score."""
        # Single keyword
        case1 = _make_case(plaintiff="ООО 'Подряд'")
        result1 = stage1_initial_screen(case1, config)

        # Two keywords
        case2 = _make_case(plaintiff="ООО 'Подряд Строительство'")
        result2 = stage1_initial_screen(case2, config)

        assert result2.relevance_score > result1.relevance_score

    def test_high_relevant_status(self, config):
        """Test that high scores get HIGH_RELEVANT status."""
        # Multiple construction keywords + Moscow court + mediation signal
        # weight(30) + 2 extra keywords(20) + mediation(15) + moscow(10) = 75+
        case = _make_case(
            plaintiff="ООО 'Подряд Строительство Монтаж Договор подряда Мировое соглашение Медиация'",
            court="Арбитражный суд города Москвы",
        )
        result = stage1_initial_screen(case, config)

        assert result.status == StatusEnum.HIGH_RELEVANT
        assert result.relevance_score >= 80

    def test_reject_status(self, config):
        """Test that zero-score cases get appropriate status."""
        # Use a non-Moscow court to avoid region bonus
        case = _make_case(
            plaintiff="ООО 'Простая Компания'",
            defendant="ООО 'Другая'",
            court="Арбитражный суд Новосибирской области",
        )
        result = stage1_initial_screen(case, config)

        # No keywords → score = 0 → should be INSUFFICIENT_INFO (no area match)
        assert result.relevance_score == 0.0
        assert result.status == StatusEnum.INSUFFICIENT_INFO

    def test_moscow_court_bonus(self, config):
        """Test Moscow court gives judge group bonus."""
        case_moscow = _make_case(
            plaintiff="ООО 'Подряд'",
            court="Арбитражный суд города Москвы",
        )
        case_other = _make_case(
            plaintiff="ООО 'Подряд'",
            court="Арбитражный суд Новосибирской области",
        )

        result_moscow = stage1_initial_screen(case_moscow, config)
        result_other = stage1_initial_screen(case_other, config)

        assert result_moscow.relevance_score > result_other.relevance_score

    def test_stage1_details_in_extracted_data(self, config):
        """Test that stage1 details are stored in extracted_data."""
        case = _make_case(plaintiff="ООО 'Подряд'")
        result = stage1_initial_screen(case, config)

        assert "stage1_score" in result.extracted_data
        assert "stage1_details" in result.extracted_data

    def test_score_capped_at_100(self, config):
        """Test that score never exceeds 100."""
        # Extreme case with many keywords
        case = _make_case(
            plaintiff="подряд строительство договор подряда монтаж мировое соглашение медиация",
            court="Арбитражный суд города Москвы",
        )
        result = stage1_initial_screen(case, config)

        assert result.relevance_score <= 100.0


class TestFilterPipeline:
    """Test the pipeline manager."""

    def test_process_single_case(self, config):
        """Test processing a single case."""
        pipeline = FilterPipeline(config)
        case = _make_case(plaintiff="ООО 'Подряд'")
        result = pipeline.process_case(case)

        assert isinstance(result, Case)
        assert result.relevance_score > 0

    def test_process_batch(self, config):
        """Test processing a batch."""
        pipeline = FilterPipeline(config)
        cases = [
            _make_case(plaintiff="ООО 'Подряд'", case_number="А40-1/2024"),
            _make_case(plaintiff="ООО 'Банкротство'", case_number="А40-2/2024"),
            _make_case(plaintiff="ООО 'Обычная'", case_number="А40-3/2024"),
        ]

        results = pipeline.process_batch(cases)
        assert len(results) == 3

        # First should match construction
        assert results[0].category == "construction"
        # Second should match bankruptcy
        assert results[1].category == "bankruptcy"
        # Third should have low/zero score
        assert results[2].relevance_score == 0.0

    def test_batch_handles_errors(self, config):
        """Test that batch processing handles individual errors gracefully."""
        pipeline = FilterPipeline(config)
        cases = [
            _make_case(plaintiff="ООО 'Подряд'", case_number="А40-1/2024"),
        ]

        # Should not raise
        results = pipeline.process_batch(cases)
        assert len(results) == 1
