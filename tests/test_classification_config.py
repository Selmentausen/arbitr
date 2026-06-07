"""Tests for classification config loading."""

from pathlib import Path

from src.config.classification import ClassificationConfig


def test_classification_config_loads():
    config = ClassificationConfig()
    assert config.prompt_version == "2.0"
    assert "construction" in config.category_ids
    assert "intellectual_property" in config.category_ids
    assert "other" in config.category_ids


def test_classification_config_ollama_settings():
    config = ClassificationConfig()
    assert config.get("ollama.model") == "qwen2.5:14b"
    assert config.get("ollama.fast_model") == "qwen2.5:7b"
