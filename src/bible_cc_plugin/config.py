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
    inject_fallback: str = "skip"

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
    commit_threshold_turns: int = 8
    commit_threshold_chars: int = 16000
    mid_session_detection: bool = True
    mid_session_upload: bool = False
    hint_format: str = "quote_with_command"
    tool_result_max_chars: int = 250

    @field_validator("hint_format")
    @classmethod
    def _validate_hint(cls, v: str) -> str:
        if v not in HINT_FORMATS:
            return "command_only"
        return v


class DetectionConfig(BaseModel):
    model: str = "deepseek-v4-flash"
    max_tokens: int = 512
    temperature: float = 0.0


class BypassConfig(BaseModel):
    session_patterns: list[str] = []


class AppConfig(BaseModel):
    bible: BibleConfig = Field(default_factory=BibleConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    injection: InjectionConfig = Field(default_factory=InjectionConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    bypass: BypassConfig = Field(default_factory=BypassConfig)


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
        config_path = Path.home() / ".bible-cc" / "config.json"
    if config_path.exists():
        file_data = json.loads(config_path.read_text())
        config = AppConfig(**file_data)

    # Tier 3: env var override
    if v := os.getenv("BIBLE_ATLAS_BASE_URL"):
        config.bible.base_url = v
    if v := os.getenv("BIBLE_ATLAS_TOKEN"):
        config.bible.token = v
    if v := os.getenv("BIBLE_CC_DAEMON_PORT"):
        config.daemon.port = int(v)
    if v := os.getenv("BIBLE_CC_DB_PATH"):
        config.daemon.db_path = v
    if v := os.getenv("BIBLE_CC_CAPTURE_ENABLED"):
        config.capture.enabled = v.lower() in ("1", "true", "yes")
    if v := os.getenv("BIBLE_CC_DETECTION_MODEL"):
        config.detection.model = v

    if debug:
        _debug_trace(config, config_path, file=sys.stderr)

    return config


def _debug_trace(config: AppConfig, config_path: Path, *, file) -> None:
    """Print config source trace for debugging."""
    print(f"[config] loading from: {config_path}", file=file)
    print(f"[config] bible.base_url = {config.bible.base_url!r}", file=file)
    print(f"[config] bible.token = {'<set>' if config.bible.token else '<none>'}", file=file)
    print(f"[config] daemon.port = {config.daemon.port}", file=file)
    print(f"[config] daemon.db_path = {config.daemon.db_path}", file=file)
    print(f"[config] daemon.port_auto_fallback = {config.daemon.port_auto_fallback}", file=file)
    print(f"[config] capture.enabled = {config.capture.enabled}", file=file)
    print(f"[config] capture.hint_format = {config.capture.hint_format}", file=file)
