"""Configuration manager for loading and accessing YAML configs."""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Manager for loading and accessing configuration from YAML files.
    
    Supports hot-reload for testing and provides convenient access
    to rules for specific legal areas.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize ConfigManager.
        
        Args:
            config_path: Path to main YAML config file. If None, uses default.
        """
        if config_path is None:
            # Default to configs/main.yaml relative to project root
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "configs" / "main.yaml"
        
        self.config_path = Path(config_path)
        self._config: dict[str, Any] = {}
        self.load()

    def load(self) -> dict[str, Any]:
        """
        Load configuration from YAML file.
        
        Returns:
            Loaded configuration dictionary
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config file is invalid YAML
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f)
            areas = self._config.get("areas", None)
            areas = areas if areas else None
            self._load_areas(areas=areas)
            logger.info(f"Loaded configuration from {self.config_path}")
            return self._config
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML config: {e}")
            raise

    def reload(self) -> dict[str, Any]:
        """
        Reload configuration from file (for hot-reload during testing).
        
        Returns:
            Reloaded configuration dictionary
        """
        logger.info("Reloading configuration...")
        return self.load()

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key.
        
        Args:
            key: Configuration key (supports dot notation, e.g., 'thresholds.high')
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split(".")
        value = self._config
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        
        return value

    def get_rules(self, area: str) -> dict[str, Any]:
        """
        Get filtering rules for a specific legal area.
        
        Args:
            area: Legal area name (e.g., 'construction', 'bankruptcy')
            
        Returns:
            Rules dictionary for the area
            
        Raises:
            KeyError: If area not found in configuration
        """
        areas = self._config.get("areas", {})
        if area not in areas:
            available = list(areas.keys())
            raise KeyError(
                f"Area '{area}' not found in configuration. Available areas: {available}"
            )
        
        return areas[area]

    def get_thresholds(self) -> dict[str, float]:
        """
        Get score thresholds for filtering.
        
        Returns:
            Dictionary with threshold values (high, low, gray_min, gray_max)
        """
        return self._config.get("thresholds", {})

    def get_judge_groups(self, region: Optional[str] = None) -> dict[str, list[str]]:
        """
        Get judge group mappings.
        
        Args:
            region: Optional region filter (e.g., 'moscow')
            
        Returns:
            Judge groups dictionary
        """
        judge_groups = self._config.get("judge_groups", {})
        if region:
            return judge_groups.get(region, {})
        return judge_groups

    def get_linkage_rules(self) -> dict[str, Any]:
        """
        Get linkage analysis rules.
        
        Returns:
            Linkage rules dictionary
        """
        return self._config.get("linkage_rules", {})
    
    def _load_areas(self, areas: list[str] = None) -> None:
        """Dynamically load area configurations and textual dictionaries."""
        areas_dir = self.config_path.parent / "areas"
        self._config["areas"] = {}

        if not areas_dir.exists():
            return

        # Loop through every yaml file in the areas folder
        # if areas is passed as an argument, only load the files that are in the areas list
        for area_file in areas_dir.glob("*.yaml"):
            if areas and area_file.stem not in areas:
                continue
            area_name = area_file.stem
            try:
                with open(area_file, "r", encoding="utf-8") as f:
                    area_config = yaml.safe_load(f) or {}
                if "keywords_file" in area_config:
                    dict_path = self.config_path.parent / area_config["keywords_file"]
                    area_config["keywords"] = self._read_dictionary_file(dict_path)
                if "stage2_keywords_file" in area_config:
                    dict_path = self.config_path.parent / area_config["stage2_keywords_file"]
                    area_config["stage2_keywords"] = self._read_dictionary_file(dict_path)
                self._config["areas"][area_name] = area_config
            except Exception as e:
                logger.error(f"Error loading area config {area_file}: {e}")
                    
    def _read_dictionary_file(self, file_path: Path) -> list[str]:
        """Read a dictionary text file into a list of strings."""
        if not file_path.exists():
            logger.warning(f"Dictionary file not found: {file_path}")
            return []
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        
        if "," in content:
            return [word.strip() for word in content.split(",") if word.strip()]
        return [line.strip() for line in content.splitlines() if line.strip()]
        

    @property
    def config(self) -> dict[str, Any]:
        """Get full configuration dictionary."""
        return self._config


# Convenience functions for direct use
_global_config: Optional[ConfigManager] = None


def load_config(file: str) -> dict[str, Any]:
    """
    Load configuration from YAML file (convenience function).
    
    Args:
        file: Path to YAML configuration file
        
    Returns:
        Configuration dictionary
    """
    global _global_config
    _global_config = ConfigManager(file)
    return _global_config.config


def get_rules(area: str) -> dict[str, Any]:
    """
    Get rules for specific legal area (convenience function).
    
    Args:
        area: Legal area name
        
    Returns:
        Rules dictionary for the area
        
    Raises:
        RuntimeError: If config not loaded yet
    """
    if _global_config is None:
        raise RuntimeError("Configuration not loaded. Call load_config() first.")
    return _global_config.get_rules(area)