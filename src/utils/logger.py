"""Logging configuration for Arbitr system."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10485760,  # 10MB
    backup_count: int = 5,
    format_string: Optional[str] = None,
) -> None:
    """
    Setup logging configuration with file and console handlers.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file. If None, uses logs/arbitr.log
        max_bytes: Maximum log file size before rotation
        backup_count: Number of backup log files to keep
        format_string: Custom log format string
    """
    # Create logs directory if it doesn't exist
    project_root = Path(__file__).parent.parent.parent
    logs_dir = project_root / "logs"
    logs_dir.mkdir(exist_ok=True)
    
    # Default log file
    if log_file is None:
        log_file = str(logs_dir / "arbitr.log")
    
    # Default format
    if format_string is None:
        format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Create formatters
    formatter = logging.Formatter(format_string)
    
    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # Log initial message
    root_logger.info(f"Logging initialized - Level: {level}, File: {log_file}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module.
    
    Args:
        name: Logger name (typically __name__ of the module)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)
