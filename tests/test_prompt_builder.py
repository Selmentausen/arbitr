"""Tests for prompt assembly."""

from src.analysis.prompt_builder import build_prompt
from src.config.classification import ClassificationConfig


def test_prompt_includes_categories_and_disambiguation():
    config = ClassificationConfig()
    prompt = build_prompt("Тестовое досье дела", config)

    assert "construction" in prompt.system
    assert "intellectual_property" in prompt.system
    assert "интеллектуальная" in prompt.system.lower()
    assert "однокоренных слов" in prompt.system
    assert "Пример 1" in prompt.system
    assert "Тестовое досье дела" in prompt.user
