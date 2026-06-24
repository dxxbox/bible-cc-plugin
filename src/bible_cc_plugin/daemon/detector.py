"""Anthropic client wrapper for moment detection (Phase 1 & Phase 2).

Phase 2b Feature 2b.1 — thin wrapper around the Anthropic SDK.
Design: 05-capture/detection.md §3-5 (prompt templates, structured output, LLM params).

Key design rules:
- API failures must NEVER crash the daemon — always return [].
- DETECTOR_TEST_MODE env var enables deterministic stub (zero-cost CI).
- Phase 2 max_tokens = detection.max_tokens × 2 (auto-derived, no separate config).
- Prompt uses UPPERCASE moment types; storage uses lowercase (detection.md §4 note).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Literal

from bible_cc_plugin.config import DetectionConfig
from bible_cc_plugin.logging_config import get_logger

_logger = get_logger("detector")

# ── Sentinel for "no Anthropic SDK installed" ──────────────────────────────
_anthropic_available = True
try:
    import anthropic
except ImportError:  # pragma: no cover — CI always has the dep
    _anthropic_available = False
    _logger.warning("anthropic SDK not installed — detection disabled")


# ══════════════════════════════════════════════════════════════════════════════
# Type
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class MomentCandidate:
    """A key moment detected by the LLM, before content-hash dedup and DB insert."""

    type: str  # "session_start" | "decision" | "accomplishment"
    title: str
    narrative: str
    tool_summary: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# Internal: Anthropic client
# ══════════════════════════════════════════════════════════════════════════════


def _create_client() -> anthropic.Anthropic:
    """Create an Anthropic client, resolving the API key from environment.

    Resolution order:
      1. ANTHROPIC_API_KEY
      2. ANTHROPIC_AUTH_TOKEN (Claude Code's own auth token)

    Raises:
        ValueError: if neither env var is set.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "") or os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN not set. "
            "Set one of the env vars or enable DETECTOR_TEST_MODE for CI."
        )
    return anthropic.Anthropic(api_key=api_key)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 Prompt Template (detection.md §3.1)
# ══════════════════════════════════════════════════════════════════════════════

_PHASE1_SYSTEM_PROMPT = """\
You are analyzing a conversation between a user and an AI agent.
Identify if any KEY MOMENTS occurred in these recent turns.

Key moment types:
- SESSION_START: the user defines the topic/scope of work
- DECISION: the user confirms a choice, approach, or design direction
- ACCOMPLISHMENT: something was completed, verified, and accepted

Do NOT flag:
- Intermediate bug fixes or error corrections
- Exploratory discoveries (unless user explicitly confirms importance)

Output a single JSON object with this exact structure:
{"result": "moment" | "none", "moments": [{"type": "...", "title": "...", "narrative": "..."}]}

If no key moment occurred, output: {"result": "none"}
Do NOT include markdown fences or extra text. Output ONLY the JSON object."""


def build_phase1_prompt(turns: list[dict]) -> str:
    """Build the Phase 1 detection prompt from recent turns (detection.md §3.1).

    Args:
        turns: List of turn dicts with keys: role, content, tool_name, tool_output.

    Returns:
        A complete prompt string suitable as the user message to the LLM.
        The system prompt is attached separately via the API.
    """
    lines = [
        "Key moment types:",
        "- SESSION_START: the user defines the topic/scope of work",
        "- DECISION: the user confirms a choice, approach, or design direction",
        "- ACCOMPLISHMENT: something was completed, verified, and accepted",
        "",
        "Do NOT flag intermediate bug fixes or error corrections.",
        "",
        "Recent conversation:",
    ]
    for i, t in enumerate(turns):
        role = t.get("role", "unknown")
        lines.append(f"\n--- Turn {i + 1} ({role}) ---")

        if role == "user":
            lines.append(t.get("content", ""))
        elif t.get("tool_name"):
            # Assistant tool call
            lines.append(f"[Used tool: {t['tool_name']}]")
            output = t.get("tool_output", "")
            lines.append(output)
        else:
            # Assistant text reply (future compatibility — detection.md §1.2.1)
            lines.append(t.get("content", ""))

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 Prompt Template (detection.md §3.2)
# ══════════════════════════════════════════════════════════════════════════════

_PHASE2_SYSTEM_PROMPT = """\
You are reviewing a COMPLETE conversation between a user and an AI agent.
The session has ended. Provide a synthesis.

Key moment types (SESSION_START excluded from Phase 2):
- DECISION: the user confirms a choice, approach, or design direction
- ACCOMPLISHMENT: something was completed, verified, and accepted

Do NOT flag intermediate bug fixes or error corrections.

Output a single JSON object:
{"result": "moment" | "none", "moments": [{"type": "...", "title": "...", "narrative": "..."}], "assessment": "overall summary"}

If no new key moment occurred, output: {"result": "none"}
Do NOT include markdown fences or extra text. Output ONLY the JSON object."""


def build_phase2_prompt(
    all_turns: list[dict],
    known_moments: list[MomentCandidate],
) -> str:
    """Build the Phase 2 retrospective prompt (detection.md §3.2).

    Includes known moments with "Do NOT re-report" instruction and
    the full session transcript.  SESSION_START type excluded.
    """
    lines = []

    if known_moments:
        lines.append(
            "The following key moments were ALREADY detected during the session."
        )
        lines.append(
            "Do NOT re-report them. Only report NEW moments not covered below:"
        )
        lines.append("")
        for m in known_moments:
            lines.append(f"- [{m.type}] {m.title}")
        lines.append("")

    lines.append("Full session transcript:")
    for i, t in enumerate(all_turns):
        role = t.get("role", "unknown")
        lines.append(f"\n--- Turn {i + 1} ({role}) ---")
        if role == "user":
            lines.append(t.get("content", ""))
        elif t.get("tool_name"):
            lines.append(f"[Used tool: {t['tool_name']}]")
            lines.append(t.get("tool_output", ""))
        else:
            lines.append(t.get("content", ""))

    lines.append("")
    lines.append("Now identify:")
    lines.append("1. Overall session assessment — what was accomplished?")
    lines.append("2. Any ADDITIONAL key moments missed by mid-session detection")
    lines.append("3. What should be remembered for future sessions?")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Stub detector (CI zero-cost)
# ══════════════════════════════════════════════════════════════════════════════


def _stub_detection(prompt: str) -> list[MomentCandidate]:
    """Deterministic stub for CI.  No real API call.

    - Prompt containing "NO_MOMENT" → [] (no moment).
    - Otherwise → one fixed DECISION candidate.
    """
    if "NO_MOMENT" in prompt:
        return []
    return [
        MomentCandidate(
            type="decision",
            title="Stub Decision",
            narrative="Stub narrative for CI testing.",
        )
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


def call_detection_llm(
    prompt: str,
    config: DetectionConfig,
    phase: int = 1,
) -> list[MomentCandidate]:
    """Call the Anthropic API for moment detection and parse structured output.

    Args:
        prompt: The user message (turns text + instructions).
        config: DetectionConfig with model, max_tokens, temperature.
        phase: 1 or 2. Phase 2 uses max_tokens × 2.

    Returns:
        List of MomentCandidate.  Empty list on failure or "none" result.
        This function NEVER raises — all exceptions are caught and logged.
    """
    # CI stub path
    if os.getenv("DETECTOR_TEST_MODE", "") in ("1", "true", "yes"):
        return _stub_detection(prompt)

    if not _anthropic_available:
        _logger.warning("detection skipped — anthropic SDK not installed")
        return []

    max_tokens = config.max_tokens if phase == 1 else config.max_tokens * 2
    start = time.monotonic()

    try:
        client = _create_client()
        response = client.messages.create(
            model=config.model,
            max_tokens=max_tokens,
            temperature=config.temperature,
            system=_PHASE1_SYSTEM_PROMPT if phase == 1 else _PHASE2_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        _logger.warning(
            "detection LLM call failed: model=%s phase=%d error=%s",
            config.model,
            phase,
            exc,
        )
        return []

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Extract text from response
    text = ""
    if response.content:
        block = response.content[0]
        text = getattr(block, "text", "")
        if not text:
            _logger.warning(
                "detection response text empty: stop_reason=%s content_count=%d "
                "block_type=%s block_keys=%s block_repr=%.500s",
                getattr(response, "stop_reason", "?"),
                len(response.content),
                type(block).__name__,
                [k for k in dir(block) if not k.startswith("_")],
                repr(block)[:500],
            )

    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

    _logger.info(
        "detection LLM: model=%s phase=%d latency=%dms "
        "input_tokens=%d output_tokens=%d",
        config.model,
        phase,
        elapsed_ms,
        input_tokens,
        output_tokens,
    )

    # Parse structured JSON output (detection.md §4)
    candidates = _parse_detection_response(text)
    if not candidates:
        _logger.debug("detection result: none (no key moments found)")
    else:
        _logger.info(
            "detection result: %d moment(s) — %s",
            len(candidates),
            ", ".join(c.title for c in candidates),
        )

    return candidates


def detect_moments(
    turns: list[dict],
    known_moments: list[MomentCandidate] | None,
    phase: Literal[1, 2],
    config: DetectionConfig,
) -> list[MomentCandidate]:
    """Unified entry point for Phase 1 and Phase 2 detection.

    Args:
        turns: Recent turns (Phase 1: 2-3 turns; Phase 2: all session turns).
        known_moments: Phase 1 already-detected moments (Phase 2 only; Phase 1: None).
        phase: 1 or 2.  Phase 2 uses a different prompt (not yet implemented).
        config: DetectionConfig.

    Returns:
        List of MomentCandidate (may be empty).  NEVER raises.
    """
    try:
        if phase == 1:
            prompt = build_phase1_prompt(turns)
            return call_detection_llm(prompt, config, phase=1)
        else:
            prompt = build_phase2_prompt(turns, known_moments or [])
            return call_detection_llm(prompt, config, phase=2)
    except Exception:
        _logger.error("detect_moments crashed", exc_info=True)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Internal: response parsing
# ══════════════════════════════════════════════════════════════════════════════


# Valid moment types for Phase 1 (uppercase in prompt, lowercase in storage).
_VALID_PHASE1_TYPES = {"session_start", "decision", "accomplishment"}


def _parse_detection_response(text: str) -> list[MomentCandidate]:
    """Parse the LLM's structured JSON response into MomentCandidate list.

    Handles:
    - Normal JSON: {"result": "moment", "moments": [...]}
    - "none" response: {"result": "none"}
    - Malformed JSON → log WARNING → return [].
    - Unknown moment types → filtered out.
    - UPPERCASE type normalization (DECISION → decision).
    """
    if not text:
        _logger.warning("detection response empty")
        return []

    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        _logger.warning(
            "detection response not valid JSON: %.200s",
            text.replace("\n", "\\n"),
        )
        return []

    if not isinstance(data, dict):
        _logger.warning("detection response not a JSON object: %s", type(data).__name__)
        return []

    result = data.get("result", "")
    if result == "none":
        return []

    if result != "moment":
        _logger.warning(
            "detection response unexpected result=%r — expected 'moment' or 'none'",
            result,
        )
        return []

    raw_moments = data.get("moments")
    if not isinstance(raw_moments, list):
        _logger.warning("detection response 'moments' is not a list")
        return []

    candidates: list[MomentCandidate] = []
    for item in raw_moments:
        if not isinstance(item, dict):
            continue
        mtype = str(item.get("type", "")).strip().lower()
        if mtype not in _VALID_PHASE1_TYPES:
            _logger.debug("detection: filtered non-key moment type=%r", mtype)
            continue

        candidates.append(
            MomentCandidate(
                type=mtype,
                title=str(item.get("title", "")).strip(),
                narrative=str(item.get("narrative", "")).strip(),
                tool_summary=str(item.get("tool_summary", "")).strip(),
            )
        )

    return candidates
