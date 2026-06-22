"""Tests for configuration manager."""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.config.manager import ConfigManager


@pytest.fixture
def sample_config():
    """Sample configuration for testing."""
    return {
        "areas": {
            "construction": {
                "keywords": ["подряд", "строительство"],
                "party_combos": ["юр.лицо vs юр.лицо"],
                "weight": 30,
            },
            "bankruptcy": {
                "keywords": ["банкротство"],
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
            }
        },
        "linkage_rules": {
            "dispute_count_threshold": 3,
            "mediation_rate_weight": 10,
        },
    }


@pytest.fixture
def config_file(sample_config):
    """Create temporary config file and mock areas for testing."""
    import shutil
    temp_dir = Path(tempfile.mkdtemp())
    config_path = temp_dir / "config.yaml"
    
    # Extract areas to write them to the mock areas directory
    areas_data = sample_config.pop("areas", {})
    
    # Write main config
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(sample_config, f)
        
    # Write area configs
    areas_dir = temp_dir / "areas"
    areas_dir.mkdir(exist_ok=True)
    for area_name, area_rules in areas_data.items():
        area_path = areas_dir / f"{area_name}.yaml"
        with open(area_path, "w", encoding="utf-8") as f_area:
            yaml.dump(area_rules, f_area)
            
    yield str(config_path)
    
    # Cleanup recursively
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestConfigManager:
    """Test cases for ConfigManager."""

    def test_load_config(self, config_file):
        """Test loading configuration from file."""
        manager = ConfigManager(config_file)
        assert manager.config is not None
        assert "areas" in manager.config
        assert "thresholds" in manager.config

    def test_load_nonexistent_file(self):
        """Test loading non-existent config file."""
        with pytest.raises(FileNotFoundError):
            ConfigManager("/nonexistent/config.yaml")

    def test_get_value(self, config_file):
        """Test getting configuration values."""
        manager = ConfigManager(config_file)
        
        # Test direct key
        assert manager.get("areas") is not None
        
        # Test dot notation
        assert manager.get("thresholds.high") == 80
        assert manager.get("thresholds.low") == 20
        
        # Test default value
        assert manager.get("nonexistent.key", "default") == "default"

    def test_get_rules(self, config_file):
        """Test getting rules for specific area."""
        manager = ConfigManager(config_file)
        
        construction_rules = manager.get_rules("construction")
        assert construction_rules is not None
        assert "keywords" in construction_rules
        assert "подряд" in construction_rules["keywords"]
        assert construction_rules["weight"] == 30

    def test_get_rules_invalid_area(self, config_file):
        """Test getting rules for non-existent area."""
        manager = ConfigManager(config_file)
        
        with pytest.raises(KeyError) as exc_info:
            manager.get_rules("nonexistent_area")
        
        assert "nonexistent_area" in str(exc_info.value)

    def test_get_thresholds(self, config_file):
        """Test getting score thresholds."""
        manager = ConfigManager(config_file)
        
        thresholds = manager.get_thresholds()
        assert thresholds["high"] == 80
        assert thresholds["low"] == 20
        assert thresholds["gray_min"] == 40
        assert thresholds["gray_max"] == 60

    def test_get_judge_groups(self, config_file):
        """Test getting judge groups."""
        manager = ConfigManager(config_file)
        
        # All groups
        all_groups = manager.get_judge_groups()
        assert "moscow" in all_groups
        
        # Specific region
        moscow_groups = manager.get_judge_groups("moscow")
        assert "group1" in moscow_groups
        assert "construction" in moscow_groups["group1"]

    def test_get_linkage_rules(self, config_file):
        """Test getting linkage rules."""
        manager = ConfigManager(config_file)
        
        linkage_rules = manager.get_linkage_rules()
        assert linkage_rules["dispute_count_threshold"] == 3
        assert linkage_rules["mediation_rate_weight"] == 10

    def test_reload(self, config_file, sample_config):
        """Test configuration hot-reload."""
        manager = ConfigManager(config_file)
        
        # Modify config file
        modified_config = sample_config.copy()
        modified_config["thresholds"]["high"] = 90
        
        with open(config_file, "w") as f:
            yaml.dump(modified_config, f)
        
        # Reload
        manager.reload()
        
        assert manager.get("thresholds.high") == 90
