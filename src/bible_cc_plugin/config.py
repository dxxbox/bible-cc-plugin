"""Config system — three-tier loading: defaults → ~/.bible-cc/config.json → env vars.

Design: 04-config/schema.md (L3). Every field, default, range, and env override
is specified there. This module implements the spec.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

HINT_FORMATS = {"quote_with_command", "quote_only", "command_only", "narrative"}


class BibleConfig(BaseModel):
    base_url: str = "http://localhost:5555"
    token: str | None = None
    kb_index: str = "bible-cc"

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")) or v.endswith("/"):
            return "http://localhost:5555"
        return v

    @field_validator("token")
    @classmethod
    def _empty_token_is_none(cls, v: str | None) -> str | None:
        if v == "":
            return None
        return v


class DaemonConfig(BaseModel):
    port: int = 9777
    port_auto_fallback: bool = False
    db_path: str = "~/.bible-cc/daemon.db"

    @field_validator("port")
    @classmethod
    def _validate_port(cls, v: int) -> int:
        if not (1024 <= v <= 65535):
            return 9777
        return v


class InjectionConfig(BaseModel):
    enabled: bool = True
    token_budget: int = 1200
    include_turns_summary: bool = True
    include_moments: bool = True
    crash_recovery_moments: bool = True
    inject_fallback: str = "empty"

    @field_validator("inject_fallback")
    @classmethod
    def _validate_fallback(cls, v: str) -> str:
        if v not in ("skip", "empty"):
            return "skip"
        return v


class SearchConfig(BaseModel):
    default_top_k: int = 8
    default_min_score: float = 0.35
    default_knowledge_tag: str = "design"


class CaptureConfig(BaseModel):
    enabled: bool = True
    mode: Literal["key_moments", "all"] = "key_moments"
    commit_threshold_turns: int = 4
    commit_threshold_chars: int = 2000
    mid_session_detection: bool = True
    mid_session_upload: bool = False
    hint_format: str = "quote_with_command"
    stop_hint_wait_seconds: float = 3.5  # Stop hook poll window when detection queued
    tool_result_max_chars: int = 250

    @field_validator("hint_format")
    @classmethod
    def _validate_hint(cls, v: str) -> str:
        if v not in HINT_FORMATS:
            return "command_only"
        return v


class DetectionConfig(BaseModel):
    model: str = "deepseek-v4-flash"
    max_tokens: int = 1024  # detection output budget (thinking disabled, JSON only)
    temperature: float = 0.0
    retrospective_max_input_chars: int = 32000  # Phase 2 prompt truncation budget (~8K tokens)


class BypassConfig(BaseModel):
    session_patterns: list[str] = []


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "~/.bible-cc/daemon.log"
    format: str = "text"  # "json" | "text"
    rotation_when: str = "midnight"  # "midnight" | "H" | "D" | ...
    rotation_backup_count: int = 7


class AppConfig(BaseModel):
    bible: BibleConfig = Field(default_factory=BibleConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    injection: InjectionConfig = Field(default_factory=InjectionConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    bypass: BypassConfig = Field(default_factory=BypassConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_config(config_path: Path | None = None, *, debug: bool = False) -> AppConfig:
    """Three-tier config loading (04-config/schema.md §4).

    1. Built-in defaults (via Pydantic default_factory)
    2. ~/.bible-cc/config.json overlay
    3. Environment variable overlay (highest priority)
    """
    # Tier 1: defaults
    config = AppConfig()

    # Tier 2: config file
    if config_path is None:
        config_path = Path(
            os.getenv(
                "BIBLE_CC_CONFIG_PATH",
                Path.home() / ".bible-cc" / "config.json",
            )
        )
    file_data: dict = {}
    if config_path.exists():
        file_data = json.loads(config_path.read_text())

    # Tier 3: env var override — merge into file_data so Pydantic validators run
    _apply_env_overrides(file_data)

    # Build with Pydantic validation (covers both file + env values)
    config = AppConfig(**file_data) if file_data else AppConfig()

    if debug:
        _debug_trace(config, config_path)

    return config


def _apply_env_overrides(data: dict) -> None:
    """Merge env var overrides into *data* dict (mutates in place).

    All values are set as raw strings/ints — Pydantic validation fires when
    AppConfig(**data) is constructed, so invalid env values fall back to
    the field defaults instead of crashing.
    """
    if v := os.getenv("BIBLE_ATLAS_BASE_URL"):
        data.setdefault("bible", {})["base_url"] = v
    if v := os.getenv("BIBLE_ATLAS_TOKEN"):
        data.setdefault("bible", {})["token"] = v
    if v := os.getenv("BIBLE_CC_DAEMON_PORT"):
        try:
            data.setdefault("daemon", {})["port"] = int(v)
        except ValueError:
            pass  # invalid → fall back to default via Pydantic
    if v := os.getenv("BIBLE_CC_DB_PATH"):
        data.setdefault("daemon", {})["db_path"] = v
    if v := os.getenv("BIBLE_CC_CAPTURE_ENABLED"):
        data.setdefault("capture", {})["enabled"] = v.lower() in ("1", "true", "yes")
    if v := os.getenv("BIBLE_CC_DETECTION_MODEL"):
        data.setdefault("detection", {})["model"] = v
    if v := os.getenv("ANTHROPIC_SMALL_FAST_MODEL"):
        data.setdefault("detection", {})["model"] = v
    if v := os.getenv("ANTHROPIC_MODEL"):
        if not os.getenv("ANTHROPIC_SMALL_FAST_MODEL"):
            data.setdefault("detection", {})["model"] = v
    if v := os.getenv("BIBLE_CC_LOG_LEVEL"):
        data.setdefault("logging", {})["level"] = v
    if v := os.getenv("BIBLE_CC_LOG_FILE"):
        data.setdefault("logging", {})["file"] = v
    if v := os.getenv("BIBLE_CC_LOG_FORMAT"):
        data.setdefault("logging", {})["format"] = v


def _debug_trace(config: AppConfig, config_path: Path) -> None:
    """Log config source trace for debugging (logger + print fallback)."""
    from bible_cc_plugin.logging_config import get_logger

    log = get_logger("config")
    lines = [
        f"loading from: {config_path}",
        f"bible.base_url = {config.bible.base_url!r}",
        f"bible.token = {'<set>' if config.bible.token else '<none>'}",
        f"daemon.port = {config.daemon.port}",
        f"daemon.db_path = {config.daemon.db_path}",
        f"daemon.port_auto_fallback = {config.daemon.port_auto_fallback}",
        f"capture.enabled = {config.capture.enabled}",
        f"capture.hint_format = {config.capture.hint_format}",
        f"logging.level = {config.logging.level}",
        f"logging.file = {config.logging.file}",
    ]
    for line in lines:
        log.debug("[config] %s", line)
        # Fallback: if no handlers configured yet, also print to stderr
        import logging as _logging

        if not log.handlers and not _logging.getLogger("bible_cc").handlers:
            print(f"[config] {line}", file=sys.stderr)
