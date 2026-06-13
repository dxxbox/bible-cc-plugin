"""Phase 0 minimal types — config models only. Extended in later phases."""

from enum import Enum


class MomentType(str, Enum):
    SESSION_START = "session_start"
    DECISION = "decision"
    ACCOMPLISHMENT = "accomplishment"
