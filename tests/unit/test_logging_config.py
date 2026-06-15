"""Unit tests for logging_config.py."""

import logging
import sys

import pytest

from bible_cc_plugin.logging_config import (
    DAEMON_LOGGER,
    configure_logging,
    get_logger,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _cleanup_logger():
    """Clean up handlers between tests to prevent leakage."""
    logger = logging.getLogger(DAEMON_LOGGER)
    logger.handlers.clear()
    yield
    logger.handlers.clear()


class TestSetupLogging:
    """Verify setup_logging() behaviour."""

    def test_returns_logger(self):
        logger = setup_logging(level="INFO")
        assert logger.name == DAEMON_LOGGER

    def test_idempotent(self):
        first = setup_logging(level="INFO")
        second = setup_logging(level="DEBUG")
        assert first is second

        handler_count = len(first.handlers)
        setup_logging(level="INFO")
        setup_logging(level="DEBUG")
        assert len(first.handlers) == handler_count

    def test_propagate_false(self):
        logger = setup_logging(level="INFO")
        assert logger.propagate is False

    def test_level_set(self):
        logger = setup_logging(level="WARNING")
        assert logger.level == logging.WARNING


class TestConfigureLogging:
    """Verify configure_logging() behaviour."""

    def test_idempotent_file_handler(self, tmp_path):
        log_file = tmp_path / "test.log"
        configure_logging(level="INFO", file=str(log_file))
        configure_logging(level="INFO", file=str(log_file))

        logger = logging.getLogger(DAEMON_LOGGER)
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1

    def test_creates_parent_directory(self, tmp_path):
        log_file = tmp_path / "sub" / "dir" / "test.log"
        configure_logging(level="INFO", file=str(log_file))
        assert log_file.parent.exists()

    def test_none_log_file_skips(self):
        logger = logging.getLogger(DAEMON_LOGGER)
        for h in list(logger.handlers):
            if isinstance(h, logging.FileHandler):
                logger.removeHandler(h)

        configure_logging(level="INFO", file=None)
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 0

    def test_writes_json_lines(self, tmp_path):
        log_file = tmp_path / "test.log"
        configure_logging(level="DEBUG", file=str(log_file))

        child = logging.getLogger(f"{DAEMON_LOGGER}.test")
        child.debug("hello world")

        for h in logging.getLogger(DAEMON_LOGGER).handlers:
            if isinstance(h, logging.FileHandler):
                h.flush()

        content = log_file.read_text()
        assert "hello world" in content
        assert '"level"' in content
        assert '"DEBUG"' in content


class TestGetLogger:
    """Verify get_logger() behaviour."""

    def test_returns_child_logger(self):
        logger = get_logger("foo.bar")
        assert logger.name == f"{DAEMON_LOGGER}.foo.bar"

    def test_consistent_identity(self):
        a = get_logger("test")
        b = get_logger("test")
        assert a is b


class TestStderrHandler:
    """Verify stderr handler behaviour."""

    def test_added_when_stderr_is_tty(self, monkeypatch):
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

        logger = logging.getLogger(DAEMON_LOGGER)
        logger.handlers.clear()

        setup_logging(level="INFO")
        stderr_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and getattr(h, "stream", None) is sys.stderr
        ]
        assert len(stderr_handlers) >= 1

    def test_skipped_when_stderr_not_tty(self, monkeypatch):
        monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

        logger = logging.getLogger(DAEMON_LOGGER)
        logger.handlers.clear()

        setup_logging(level="INFO")
        assert len(logger.handlers) == 0
