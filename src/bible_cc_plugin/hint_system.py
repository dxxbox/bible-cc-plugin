"""Hint formatting — moment hints + error hints.

Phase 2d.2 + 2d.3 — pure functions, importable by both daemon and hook.
Design: 08-operability/hint-system.md, CLAUDE.md §Hint Notification.
"""

from __future__ import annotations

from bible_cc_plugin.daemon.detector import MomentCandidate

_LABELS = {
    "decision": "Decision",
    "accomplishment": "Accomplishment",
    "session_start": "Session Start",
}


def format_hint(moment: MomentCandidate, format_mode: str) -> str:
    """Format a moment as a user-visible hint string.

    Args:
        moment: Detected MomentCandidate.
        format_mode: One of "quote_with_command", "quote_only",
            "command_only", "narrative".

    Returns:
        A hint string starting with "⎿ ⏳".
    """
    prefix = "⎿ ⏳"
    label = _LABELS.get(moment.type, moment.type)
    title = moment.title.replace('"', "'")

    if format_mode == "quote_with_command":
        return f'{prefix} Captured: "{title}" — {label}. /bible-cc:review'
    elif format_mode == "quote_only":
        return f'{prefix} Captured: "{title}" — {label}.'
    elif format_mode == "command_only":
        return f"{prefix} Key moment captured ({label}). /bible-cc:review"
    elif format_mode == "narrative":
        summary = moment.narrative[:200]
        return f"{prefix} Captured {moment.type}: {title}. {summary}"
    else:
        return f'{prefix} Moment captured: "{title}"'


def format_error_hint(error_type: str, detail: str) -> str:
    """Format an error as a user-visible hint."""
    if error_type == "port_conflict":
        return f"❌ bible-cc daemon: port conflict — {detail}"
    return f"❌ bible-cc daemon: {error_type} — {detail}"
