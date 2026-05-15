from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4


LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(filename)s:%(lineno)d | "
    "run_id=%(run_id)s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_DIR = "output/logs"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10


class RunContextFilter(logging.Filter):
    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "run_id"):
            record.run_id = self.run_id
        return True


def _coerce_level(level: int | str) -> int:
    if isinstance(level, int):
        return level

    normalized = str(level).strip().upper()
    resolved = logging.getLevelName(normalized)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _default_logger_name() -> str:
    stem = Path(sys.argv[0]).stem
    if not stem or stem.startswith("-"):
        return "application"
    return stem


def configure_logging(
    *,
    logger_name: str | None = None,
    level: int | str = logging.INFO,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    resolved_level = _coerce_level(level)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    logger_name = logger_name or _default_logger_name()

    logs_path = Path(log_dir).expanduser()
    logs_path.mkdir(parents=True, exist_ok=True)
    log_file = logs_path / f"{logger_name}_{run_id}.log"

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    context_filter = RunContextFilter(run_id)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(resolved_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(resolved_level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(resolved_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logger = logging.getLogger(logger_name)
    logger.setLevel(resolved_level)
    logger.propagate = True
    logger.info("Logging initialized | log_file=%s", _safe_resolve(log_file), stacklevel=2)
    return logger


def log_file_read(
    logger: logging.Logger,
    file_path: str | Path,
    *,
    domain: str,
    sheet: str | None = None,
) -> None:
    path = Path(file_path).expanduser()
    resolved = _safe_resolve(path)
    device_file_name = path.name or resolved.name or str(file_path)

    parts = [
        f"domain={domain}",
        f"device_file_name={device_file_name!r}",
        f"device_path={str(resolved)!r}",
    ]
    if sheet:
        parts.append(f"sheet={sheet!r}")

    logger.info("Reading source file | %s", " | ".join(parts), stacklevel=2)
