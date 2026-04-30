# src/core/logging.py
"""
Central logging configuration for production-grade logging.
Logs to both console and file with structured format.
"""
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


def setup_logging(
    log_dir: str = "logs",
    log_file: str = "app.log",
    level: int = logging.INFO,
    format_string: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
) -> logging.Logger:
    """
    Set up central logging with console and file handlers.
    
    Args:
        log_dir: Directory to store log files
        log_file: Name of the log file
        level: Logging level (default: INFO)
        format_string: Log message format
    
    Returns:
        Configured root logger
    """
    # Create logs directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    full_log_file = log_path / log_file
    
    # Create formatters
    detailed_formatter = logging.Formatter(format_string)
    console_formatter = logging.Formatter(format_string)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler (INFO level)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation (DEBUG level for full logs)
    file_handler = RotatingFileHandler(
        full_log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    root_logger.addHandler(file_handler)
    
    # Suppress noisy third-party loggers
    _suppress_noisy_loggers()
    
    return root_logger


def _suppress_noisy_loggers():
    """Suppress logs from third-party libraries that generate noise."""
    suppress_list = [
        "WDM",
        "wdm",
        "urllib3",
        "selenium",
        "selenium.webdriver",
        "influxdb_client",
        "asyncio",
    ]
    
    for logger_name in suppress_list:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a module-level logger.
    
    Args:
        name: Module name (typically __name__)
    
    Returns:
        Logger instance for the module
    """
    return logging.getLogger(name)


# Standard log message templates
class LogTemplates:
    """Standardized log message templates for consistent logging."""
    
    @staticmethod
    def start(mode: str, date: str) -> str:
        return f"START | mode={mode} date={date}"
    
    @staticmethod
    def download(file: str) -> str:
        return f"DOWNLOAD | file={file}"
    
    @staticmethod
    def process(rows: int) -> str:
        return f"PROCESS | rows={rows}"
    
    @staticmethod
    def db_inserted(count: int) -> str:
        return f"DB | inserted={count}"
    
    @staticmethod
    def success(duration: float) -> str:
        return f"SUCCESS | duration={duration:.2f}s"
    
    @staticmethod
    def failed(error: str) -> str:
        return f"FAILED | error={error}"
    
    @staticmethod
    def skipped(reason: str) -> str:
        return f"SKIPPED | reason={reason}"
    
    @staticmethod
    def info(message: str) -> str:
        return message