"""Tests for classifier probability normalization and Ollama mocking."""

from unittest.mock import MagicMock, patch

import pytest

from src.analysis.classifier import _normalize_probabilities, _response_to_result
from src.analysis.models import LLMClassificationResponse
from src.config.classification import ClassificationConfig


def test_normalize_probabilities_renormalizes():
    raw = {"construction": 0.8, "bankruptcy": 0.1, "other": 0.05}
    result = _normalize_probabilities(raw, ["construction", "bankruptcy", "other"])
    assert abs(sum(result.values()) - 1.0) < 0.001
    assert result["construction"] > result["bankruptcy"]


def test_normalize_probabilities_uniform_if_zero():
    result = _normalize_probabilities({}, ["construction", "bankruptcy", "other"])
    assert abs(result["construction"] - 1 / 3) < 0.01


def test_response_to_result_picks_primary():
    response = LLMClassificationResponse(
        probabilities={"construction": 0.85, "bankruptcy": 0.10, "other": 0.05},
        reasoning="Тест",
        key_signals=["подряд"],
        uncertainty="low",
    )
    result = _response_to_result(response, ["construction", "bankruptcy", "other"], "1.0", "test-model")
    assert result.primary_category == "construction"
    assert result.confidence == pytest.approx(0.85, abs=0.01)
    assert result.model == "test-model"
