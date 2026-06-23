"""Context injection — three-scenario branching + XML building.

Phase 1c: local SQLite only.  No BiBLE calls, no LLM calls.
Design: 06-recall/local-injection.md, 03-daemon/http-api.md §5.1.
"""

from __future__ import annotations

import sqlite3

from bible_cc_plugin.logging_config import setup_logging

_logger = setup_logging(level="INFO")

_TOKEN_CHARS_ESTIMATE = 3  # ~3 chars per token (rough heuristic)


# ── scenario determination ───────────────────────────────────────────────


def determine_injection_scenario(
    conn: sqlite3.Connection,
    session_id: str,
    recovery_data: dict | None,
) -> str:
    """Return ``'empty'``, ``'crash_recovery'``, or ``'clear_or_compact'``.

    Priority: crash_recovery > clear_or_compact > empty.
    """
    if recovery_data is not None and recovery_data.get("unclosed_sessions_found", 0) > 0:
        return "crash_recovery"

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM turns WHERE session_id=?", (session_id,)
    ).fetchone()
    if row and row["n"] > 0:
        return "clear_or_compact"

    return "empty"


# ── builders ─────────────────────────────────────────────────────────────


def build_empty_context(fallback_mode: str) -> str:
    """``"skip"`` → empty string.  ``"empty"`` → empty XML block."""
    if fallback_mode == "empty":
        return "<relevant-memories></relevant-memories>"
    return ""


def build_turns_summary(conn: sqlite3.Connection, session_id: str, max_turns: int = 20) -> str:
    """Plain-text summary of the most recent *max_turns* turns."""
    rows = conn.execute(
        "SELECT role, content, tool_name FROM turns WHERE session_id=? ORDER BY seq DESC LIMIT ?",
        (session_id, max_turns),
    ).fetchall()

    if not rows:
        return ""

    lines = []
    for r in reversed(rows):
        if r["role"] == "user":
            text = (r["content"] or "")[:120]
            lines.append(f"[user] {text}")
        else:
            tool = r["tool_name"] or "tool"
            lines.append(f"[assistant] used {tool}")
    return "\n".join(lines)


def build_moments_context(conn: sqlite3.Connection, session_id: str) -> str:
    """XML for unflushed moments in *session_id*."""
    from bible_cc_plugin.daemon.buffer import get_unflushed_moments

    moments = get_unflushed_moments(conn, session_id)
    if not moments:
        return ""

    parts = []
    for m in moments:
        parts.append(
            f'<moment type="{m["moment_type"]}" title="{m["title"]}">{m["narrative"]}</moment>'
        )
    return "\n".join(parts)


def build_crash_recovery_context(moments: list[dict], turns: list[dict]) -> str:
    """Marked with ``[Recovered from prior session]``."""
    lines = ["[Recovered from prior session]"]

    if turns:
        lines.append(f"Previous session had {len(turns)} turns.")
        for t in turns[-5:]:
            if t.get("role") == "user" and t.get("content"):
                lines.append(f"[user] {(t['content'] or '')[:100]}")
            elif t.get("tool_name"):
                lines.append(f"[assistant] used {t['tool_name']}")

    if moments:
        lines.append(f"Recovered {len(moments)} pending moments:")
        for m in moments:
            lines.append(
                f'<moment type="{m["moment_type"]}" title="{m["title"]}">{m["narrative"]}</moment>'
            )

    return "\n".join(lines)


# ── token budget ─────────────────────────────────────────────────────────


def apply_token_budget(context: str, budget: int) -> str:
    """Truncate *context* to *budget* tokens, preserving XML wrapper."""
    if not context:
        return context

    if not context.startswith("<relevant-memories>"):
        context = f"<relevant-memories>\n{context}\n</relevant-memories>"

    max_chars = budget * _TOKEN_CHARS_ESTIMATE
    if len(context) <= max_chars:
        return context

    cut = context.rfind("\n", 0, max_chars)
    if cut < len("<relevant-memories>"):
        cut = max(0, max_chars - 200)

    truncated = context[:cut] + "\n[truncated]\n</relevant-memories>"
    _logger.debug("token budget truncation: %d→%d chars", len(context), len(truncated))
    return truncated


# ── orchestration ────────────────────────────────────────────────────────


def build_context(
    conn: sqlite3.Connection,
    session_id: str,
    recovery_data: dict | None,
    fallback_mode: str,
    token_budget: int,
    include_turns_summary: bool,
    include_moments: bool,
    include_crash_recovery_moments: bool = True,
) -> tuple[str, dict]:
    """Return ``(context, sources)`` for a context injection request."""
    scenario = determine_injection_scenario(conn, session_id, recovery_data)
    _logger.info(
        "context/inject session=%s scenario=%s recovery=%s",
        session_id,
        scenario,
        recovery_data is not None,
    )

    parts: list[str] = []
    sources: dict = {"turns": 0, "moments": 0, "crash_recovery": 0}

    if scenario == "empty":
        return build_empty_context(fallback_mode), sources

    if scenario == "crash_recovery":
        if recovery_data is None:
            _logger.warning("crash_recovery scenario with no recovery_data — falling back to empty")
            return build_empty_context(fallback_mode), sources
        moments = recovery_data.get("_moments", []) if include_crash_recovery_moments else []
        turns = recovery_data.get("_turns", []) if include_turns_summary else []
        ctx = build_crash_recovery_context(moments, turns)
        sources["crash_recovery"] = recovery_data.get("unclosed_sessions_found", 0)
        return apply_token_budget(ctx, token_budget), sources

    # clear_or_compact — current session data
    if include_turns_summary:
        ts = build_turns_summary(conn, session_id)
        if ts:
            parts.append(ts)
            sources["turns"] = ts.count("\n") + 1  # DRIFT #2: actual turn count

    if include_moments:
        mc = build_moments_context(conn, session_id)
        if mc:
            parts.append(mc)
            sources["moments"] = 1

    ctx = "\n\n".join(parts)
    return apply_token_budget(ctx, token_budget), sources
