"""Unified logging for bible-cc-plugin.

Two handlers, both attached to the root "bible_cc" logger:
- stderr: human-readable text, added only when stderr is a TTY (interactive terminal).
  In daemon subprocesses stderr is redirected to the log file, so the stderr
  handler is skipped to avoid duplicate lines.
- file: TimedRotatingFileHandler with configurable format (JSON or text).
  Fault-tolerant — falls back to stderr if the file cannot be opened.

Child loggers (bible_cc.hook, bible_cc.daemon_launcher, etc.) are created via
get_logger(name) and propagate up to "bible_cc" which owns all handlers.

Design borrows from BiBLE-Atlas (bible/common/logger.py):
- propagate=False on the root "bible_cc" logger prevents bubbling to Python root.
- configure_logging() is fault-tolerant (try/except with stderr fallback).
- get_logger(__name__) convention for automatic scoping.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

DAEMON_LOGGER = "bible_cc"

# ---------------------------------------------------------------------------
# Format strings
# ---------------------------------------------------------------------------

_TEXT_FMT = (
    "[%(asctime)s] [%(levelname)s] %(name)s "
    "%(filename)s:%(lineno)d: %(message)s"
)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """JSON-line format for log files — one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        return json.dumps(
            {
                "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Idempotent: configure the bible_cc root logger with a human-readable stderr handler.

    The stderr handler is only added when stderr is a TTY. In daemon subprocesses
    stderr is redirected to the log file, so this avoids writing duplicate
    human-readable lines alongside the output from the file handler.

    Returns the root "bible_cc" logger (same object every call).
    """
    logger = logging.getLogger(DAEMON_LOGGER)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False  # never bubble to Python root logger

    if not logger.handlers and sys.stderr.isatty():
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_TEXT_FMT))
        logger.addHandler(handler)

    return logger


def configure_logging(
    level: str = "INFO",
    file: str | None = None,
    *,
    format: str = "json",
    rotation_when: str = "midnight",
    rotation_backup_count: int = 7,
) -> None:
    """Post-config: add a TimedRotatingFileHandler to the bible_cc root logger.

    Idempotent — the file handler is only added once. If *file* is None
    or empty, no file handler is added (stderr-only mode).

    Creates parent directories automatically. Fault-tolerant: if the file
    handler setup fails, the error is printed to stderr and execution continues.
    """
    logger = logging.getLogger(DAEMON_LOGGER)

    # Ensure base stderr setup (no-op if already done)
    setup_logging(level)

    # Update level in case it changed
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Already have a file handler?
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            return

    if not file:
        return

    # Choose formatter
    if format == "text":
        formatter = logging.Formatter(_TEXT_FMT)
    else:
        formatter = _JsonFormatter()

    try:
        log_path = Path(file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            str(log_path),
            when=rotation_when,
            backupCount=rotation_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except Exception:
        # Fault-tolerant: warn to stderr if available, otherwise swallow
        for h in logger.handlers:
            if isinstance(h, logging.StreamHandler):
                h.stream.write(
                    f"[WARNING] Failed to create log file: {file}\n"
                )
                break


def get_logger(name: str) -> logging.Logger:
    """Return a child logger ``bible_cc.<name>``.

    Child loggers propagate to the root "bible_cc" logger which owns all
    handlers — no need to attach handlers here.
    """
    return logging.getLogger(f"{DAEMON_LOGGER}.{name}")
