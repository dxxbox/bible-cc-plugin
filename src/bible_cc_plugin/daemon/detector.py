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


@dataclass
class _ParseResult:
    candidates: list[MomentCandidate]
    invalid_json: bool = False


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
{
  "result": "moment" | "none",
  "moments": [{"type": "...", "title": "...", "narrative": "..."}],
  "assessment": "overall summary"
}

Keep output compact:
- At most 3 moments.
- title <= 12 words.
- narrative: one sentence, <= 160 characters.
- assessment <= 200 characters.

If no new key moment occurred, output: {"result": "none"}
Do NOT include markdown fences or extra text. Output ONLY the JSON object."""


def build_phase2_prompt(
    all_turns: list[dict],
    known_moments: list[MomentCandidate],
    max_input_chars: int = 32000,
) -> str:
    """Build the Phase 2 retrospective prompt (detection.md §3.2).

    Includes known moments with "Do NOT re-report" instruction and
    the full session transcript.  SESSION_START type excluded.

    If the prompt would exceed max_input_chars, truncates to head + tail
    with an omission note to keep the LLM call lightweight.
    """
    # ── Fixed preamble + footer (known moments + instructions) ──────────
    preamble_lines: list[str] = []
    if known_moments:
        preamble_lines.append(
            "The following key moments were ALREADY detected during the session."
        )
        preamble_lines.append(
            "Do NOT re-report them. Only report NEW moments not covered below:"
        )
        preamble_lines.append("")
        known_budget = min(4000, max(0, max_input_chars // 4))
        known_used = 0
        known_omitted = 0
        for index, m in enumerate(known_moments):
            line = f"- [{m.type}] {m.title}"
            if len(line) > 300:
                line = line[:297].rstrip() + "..."

            if known_used + len(line) > known_budget:
                known_omitted = len(known_moments) - index
                break

            preamble_lines.append(line)
            known_used += len(line)

        if known_omitted:
            preamble_lines.append(
                f"... [{known_omitted} already-detected moments omitted to fit budget]"
            )
        preamble_lines.append("")

    footer_lines = [
        "",
        "Now identify:",
        "1. Any ADDITIONAL key moments missed by mid-session detection",
        "2. A compact assessment of what was accomplished",
        "",
        "Output constraints:",
        "- Return at most 3 moments.",
        "- Use one short sentence per narrative (<=160 chars).",
        "- Keep assessment <=200 chars.",
        "- Output only valid compact JSON.",
    ]
    fixed_overhead = len("\n".join(preamble_lines)) + len("\n".join(footer_lines)) + 2

    def _compose_prompt(body: str) -> str:
        return "\n".join(preamble_lines + [body] + footer_lines)

    def _fit_body_to_budget(body: str) -> str:
        """Trim transcript body so the composed prompt respects max_input_chars."""
        if len(_compose_prompt(body)) <= max_input_chars:
            return body

        marker = "\n[...phase2 transcript truncated to fit budget...]"
        low = 0
        high = len(body)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = body[:mid].rstrip() + marker
            if len(_compose_prompt(candidate)) <= max_input_chars:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best or "Full session transcript:\n[...omitted to fit budget...]"

    # ── Build turn lines with budget awareness ──────────────────────────
    turn_lines: list[str] = ["Full session transcript:"]
    separator_margin = min(1000, max_input_chars // 10)
    budget = max_input_chars - fixed_overhead - len(turn_lines[0]) - separator_margin

    def _format_turn(i: int, t: dict, content_max: int | None = None) -> str:
        """Format a single turn.  If content_max is given, per-turn content is
        truncated to that limit with a marker."""
        role = t.get("role", "unknown")
        header = f"--- Turn {i + 1} ({role}) ---"
        if role == "user":
            body = t.get("content", "")
        elif t.get("tool_name"):
            header += f"\n[Used tool: {t['tool_name']}]"
            body = t.get("tool_output", "")
        else:
            body = t.get("content", "")
        if content_max is not None and len(body) > content_max:
            body = body[:content_max] + "\n[...truncated...]"
        return header + "\n" + body

    # Format all turns without truncation (for full-char counting)
    all_formatted = [_format_turn(i, t) for i, t in enumerate(all_turns)]
    total_turn_chars = sum(len(s) for s in all_formatted)

    if total_turn_chars <= budget:
        # No truncation needed — all turns fit
        prompt_body = "Full session transcript:\n" + "\n\n".join(all_formatted)
    else:
        # ── Budget-aware truncation ──────────────────────────────────────
        # Build head + tail greedily.  Each per-turn content is capped.
        # Guarantees: omitted >= 0, final prompt <= max_input_chars.

        per_turn_content_max = 4000  # ~1K tokens per turn upper bound

        head_parts: list[str] = []
        tail_parts: list[str] = []
        used = 0
        hi = 0
        ti = len(all_formatted) - 1

        while hi <= ti and used < budget:
            remaining = budget - used

            # ── Add head turn ──
            turn_str = all_formatted[hi]
            if len(turn_str) > remaining:
                content_max = min(
                    max(remaining - 100, 200), per_turn_content_max
                )
                turn_str = _format_turn(hi, all_turns[hi], content_max=content_max)
            head_parts.append(turn_str)
            used += len(turn_str)
            hi += 1

            if hi > ti or used >= budget:
                break

            # ── Add tail turn ──
            remaining = budget - used
            turn_str = all_formatted[ti]
            if len(turn_str) > remaining:
                content_max = min(
                    max(remaining - 100, 200), per_turn_content_max
                )
                turn_str = _format_turn(ti, all_turns[ti], content_max=content_max)
            tail_parts.append(turn_str)
            used += len(turn_str)
            ti -= 1

        omitted = max(0, ti - hi + 1)
        omission = (
            f"--- [{omitted} turns omitted — see transcript for details] ---"
        )
        # Reversed so tail turns appear in chronological order
        segments = ["Full session transcript:"] + head_parts
        if omitted > 0:
            segments.append(omission)
        segments.extend(reversed(tail_parts))
        prompt_body = "\n\n".join(segments)

        _logger.info(
            "phase2 prompt truncated: original_chars~%d pre_fit_chars=%d "
            "head=%d tail=%d omitted=%d",
            fixed_overhead + total_turn_chars,
            fixed_overhead + len(prompt_body),
            len(head_parts),
            len(tail_parts),
            omitted,
        )

    prompt_body = _fit_body_to_budget(prompt_body)
    prompt = _compose_prompt(prompt_body)
    if len(prompt) > max_input_chars:
        _logger.warning(
            "phase2 prompt fixed sections exceed max_input_chars: "
            "prompt_chars=%d max_input_chars=%d known_moments=%d",
            len(prompt),
            max_input_chars,
            len(known_moments),
        )
        prompt = prompt[:max_input_chars]
    return prompt


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

    try:
        client = _create_client()
        text = _call_detection_api(client, prompt, config, phase, max_tokens)
    except Exception as exc:
        _logger.warning(
            "detection LLM call failed: model=%s phase=%d error=%s",
            config.model,
            phase,
            exc,
        )
        return []

    # Parse structured JSON output (detection.md §4)
    parsed = _parse_detection_response_result(text)
    if phase == 2 and parsed.invalid_json:
        try:
            _logger.info("phase2 detection JSON invalid — retrying with compact prompt")
            text = _call_detection_api(
                client,
                _build_phase2_compact_retry_prompt(prompt, max_chars=len(prompt)),
                config,
                phase,
                max_tokens,
                retry=True,
            )
            parsed = _parse_detection_response_result(text)
        except Exception as exc:
            _logger.warning(
                "detection LLM retry failed: model=%s phase=%d error=%s",
                config.model,
                phase,
                exc,
            )
            return []

    if not parsed.candidates:
        _logger.debug("detection result: none (no key moments found)")
    else:
        _logger.info(
            "detection result: %d moment(s) — %s",
            len(parsed.candidates),
            ", ".join(c.title for c in parsed.candidates),
        )

    return parsed.candidates


def _call_detection_api(
    client,
    prompt: str,
    config: DetectionConfig,
    phase: int,
    max_tokens: int,
    retry: bool = False,
) -> str:
    """Call the LLM once and return extracted text from response blocks."""
    start = time.monotonic()
    response = client.messages.create(
        model=config.model,
        max_tokens=max_tokens,
        temperature=config.temperature,
        system=_PHASE1_SYSTEM_PROMPT if phase == 1 else _PHASE2_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Some models emit ThinkingBlock before TextBlock; collect all text blocks.
    text_parts: list[str] = []
    block_types: list[str] = []
    for block in response.content or []:
        block_types.append(type(block).__name__)
        block_text = getattr(block, "text", "")
        if block_text:
            text_parts.append(str(block_text).strip())
    text = "\n".join(part for part in text_parts if part)
    if not text:
        _logger.warning(
            "detection response text empty: stop_reason=%s content_count=%d block_types=%s",
            getattr(response, "stop_reason", "?"),
            len(response.content or []),
            block_types,
        )

    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    label = " retry" if retry else ""
    _logger.info(
        "detection LLM%s: model=%s phase=%d latency=%dms "
        "input_tokens=%d output_tokens=%d",
        label,
        config.model,
        phase,
        elapsed_ms,
        input_tokens,
        output_tokens,
    )
    return text


def _build_phase2_compact_retry_prompt(prompt: str, max_chars: int | None = None) -> str:
    """Prepend stricter instructions after Phase 2 returned invalid JSON."""
    prefix = "\n".join(
        [
            "RETRY: Your previous response was invalid or truncated JSON.",
            "Return ONLY one minified JSON object.",
            "Use this exact shape:",
            '{"result":"moment","moments":[{"type":"decision","title":"...",'
            '"narrative":"..."}],"assessment":"..."}',
            "Hard limits: max 2 moments, title <= 8 words, narrative <= 100 chars, "
            "assessment <= 120 chars.",
            "If uncertain, return {\"result\":\"none\"}.",
            "",
        ]
    )
    if max_chars is None or len(prefix) + len(prompt) <= max_chars:
        return prefix + prompt
    if max_chars < 1000:
        return prefix + prompt

    marker = "\n[...retry prompt truncated to fit budget...]\n"
    remaining = max_chars - len(prefix) - len(marker)
    return prefix + prompt[:remaining].rstrip() + marker


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
            prompt = build_phase2_prompt(
                turns, known_moments or [],
                max_input_chars=config.retrospective_max_input_chars,
            )
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
    return _parse_detection_response_result(text).candidates


def _parse_detection_response_result(text: str) -> _ParseResult:
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
        return _ParseResult([], invalid_json=True)

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
        return _ParseResult([], invalid_json=True)

    if not isinstance(data, dict):
        _logger.warning("detection response not a JSON object: %s", type(data).__name__)
        return _ParseResult([])

    result = data.get("result", "")
    if result == "none":
        return _ParseResult([])

    if result != "moment":
        _logger.warning(
            "detection response unexpected result=%r — expected 'moment' or 'none'",
            result,
        )
        return _ParseResult([])

    raw_moments = data.get("moments")
    if not isinstance(raw_moments, list):
        _logger.warning("detection response 'moments' is not a list")
        return _ParseResult([])

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

    return _ParseResult(candidates)
