"""Unified structured logging for daemon — JSON to stderr, request_id injection."""

from __future__ import annotations

import logging
import sys

DAEMON_LOGGER = "bible_cc"


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure the bible-cc root logger with structured JSON output to stderr."""
    logger = logging.getLogger(DAEMON_LOGGER)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JSONFormatter())
        logger.addHandler(handler)

    return logger


class _JSONFormatter(logging.Formatter):
    """Minimal JSON-line formatter."""

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
