"""Unit tests for detector.py — Anthropic client wrapper for moment detection.

Phase 2b Feature 2b.1 — all tests [Unit] [Pre].
Uses stub mode (DETECTOR_TEST_MODE=true) and mock to avoid real API calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bible_cc_plugin.config import DetectionConfig

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_turn(
    role: str, content: str = "", tool_name: str = "", tool_output: str = ""
) -> dict:
    """Build a turn dict matching the SQLite turns row shape."""
    return {
        "role": role,
        "content": content,
        "tool_name": tool_name,
        "tool_output": tool_output,
    }


def _default_config() -> DetectionConfig:
    """Return a DetectionConfig with known defaults."""
    return DetectionConfig(model="deepseek-v4-flash", max_tokens=512, temperature=0.0)


# ══════════════════════════════════════════════════════════════════════════════
# Feature 2b.1: Anthropic Client Wrapper
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildPhase1Prompt:
    """build_phase1_prompt() — produce correct prompt from turns."""

    def test_contains_user_turn(self):
        """Prompt includes user message content."""
        from bible_cc_plugin.daemon.detector import build_phase1_prompt

        turns = [_make_turn("user", content="Let's use PostgreSQL for auth")]
        prompt = build_phase1_prompt(turns)
        assert "Let's use PostgreSQL for auth" in prompt

    def test_contains_tool_turn(self):
        """Prompt includes tool name and output for tool turns."""
        from bible_cc_plugin.daemon.detector import build_phase1_prompt

        turns = [
            _make_turn("assistant", tool_name="Bash", tool_output="All tests passed.")
        ]
        prompt = build_phase1_prompt(turns)
        assert "Bash" in prompt
        assert "All tests passed." in prompt

    def test_contains_assistant_text_turn(self):
        """Prompt includes assistant text reply content (not tool call).

        Intent: covers the three turn types — user, assistant-tool, assistant-text.
        Although Stop hook currently doesn't capture assistant text (detection.md
        §1.2.1), build_phase1_prompt must handle this role correctly for future
        compatibility.
        """
        from bible_cc_plugin.daemon.detector import build_phase1_prompt

        turns = [
            _make_turn("assistant", content="I recommend using PostgreSQL.")
        ]
        prompt = build_phase1_prompt(turns)
        assert "I recommend using PostgreSQL." in prompt

    def test_includes_moment_type_definitions(self):
        """Prompt contains SESSION_START, DECISION, ACCOMPLISHMENT type definitions
        and excludes intermediate bug fixes."""
        from bible_cc_plugin.daemon.detector import build_phase1_prompt

        turns = [_make_turn("user", content="hello")]
        prompt = build_phase1_prompt(turns)
        assert "SESSION_START" in prompt
        assert "DECISION" in prompt
        assert "ACCOMPLISHMENT" in prompt
        assert "intermediate" in prompt.lower() or "bug fix" in prompt.lower()


# ══════════════════════════════════════════════════════════════════════════════


class TestCallDetectionLLM:
    """call_detection_llm() — API call + structured output parsing."""

    def test_stub_returns_candidates(self, monkeypatch):
        """Stub mode returns a MomentCandidate list for normal prompt."""
        monkeypatch.setenv("DETECTOR_TEST_MODE", "1")

        from bible_cc_plugin.daemon.detector import call_detection_llm

        config = _default_config()
        result = call_detection_llm("test prompt", config, phase=1)
        assert isinstance(result, list)
        assert len(result) >= 1
        candidate = result[0]
        assert hasattr(candidate, "type")
        assert hasattr(candidate, "title")
        assert hasattr(candidate, "narrative")

    def test_stub_returns_empty_for_no_moment(self, monkeypatch):
        """Stub mode returns [] when prompt contains NO_MOMENT marker."""
        monkeypatch.setenv("DETECTOR_TEST_MODE", "1")

        from bible_cc_plugin.daemon.detector import call_detection_llm

        config = _default_config()
        result = call_detection_llm("NO_MOMENT: nothing happened", config, phase=1)
        assert result == []

    def test_api_failure_returns_empty(self, monkeypatch):
        """API error → log WARNING → return [], never crash."""
        monkeypatch.setenv("DETECTOR_TEST_MODE", "")  # force real path
        monkeypatch.delenv("DETECTOR_TEST_MODE", raising=False)

        # Mock the Anthropic client to raise
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = MagicMock(side_effect=RuntimeError("API down"))

        with patch(
            "bible_cc_plugin.daemon.detector._create_client", return_value=mock_client
        ):
            from bible_cc_plugin.daemon.detector import call_detection_llm

            config = _default_config()
            # Must not raise
            result = call_detection_llm("test", config, phase=1)
            assert result == []

    def test_extracts_text_after_thinking_block(self, monkeypatch):
        """ThinkingBlock before TextBlock → parse the later JSON text block."""
        monkeypatch.delenv("DETECTOR_TEST_MODE", raising=False)

        class ThinkingBlock:
            thinking = "internal reasoning"

        class TextBlock:
            text = (
                '{"result":"moment","moments":[{"type":"DECISION",'
                '"title":"Use V4 contract","narrative":"User confirmed V4 as source."}]}'
            )

        mock_response = SimpleNamespace(
            content=[ThinkingBlock(), TextBlock()],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch(
            "bible_cc_plugin.daemon.detector._create_client", return_value=mock_client
        ):
            from bible_cc_plugin.daemon.detector import call_detection_llm

            result = call_detection_llm("test", _default_config(), phase=1)

        assert len(result) == 1
        assert result[0].type == "decision"
        assert result[0].title == "Use V4 contract"


# ══════════════════════════════════════════════════════════════════════════════


class TestStubDetectorDeterministic:
    """Stub detector produces deterministic output."""

    def test_same_prompt_same_result(self, monkeypatch):
        """Identical prompt → identical moment."""
        monkeypatch.setenv("DETECTOR_TEST_MODE", "1")

        from bible_cc_plugin.daemon.detector import call_detection_llm

        config = _default_config()
        r1 = call_detection_llm("same prompt", config, phase=1)
        r2 = call_detection_llm("same prompt", config, phase=1)
        assert len(r1) == len(r2)
        if r1:
            assert r1[0].title == r2[0].title
            assert r1[0].narrative == r2[0].narrative

    def test_no_moment_marker_returns_empty(self, monkeypatch):
        """Prompt with NO_MOMENT always returns []."""
        monkeypatch.setenv("DETECTOR_TEST_MODE", "1")

        from bible_cc_plugin.daemon.detector import call_detection_llm

        config = _default_config()
        for _ in range(3):
            assert (
                call_detection_llm("some NO_MOMENT text", config, phase=1) == []
            )


# ══════════════════════════════════════════════════════════════════════════════
# Intent tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDetectorNeverCrashes:
    """Intent: 防御性 — unexpected exceptions must never propagate."""

    def test_unexpected_exception_returns_empty(self, monkeypatch):
        """Even RuntimeError from SDK → catch → return [], no crash."""
        monkeypatch.delenv("DETECTOR_TEST_MODE", raising=False)

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = MagicMock(side_effect=RuntimeError("unexpected"))

        with patch(
            "bible_cc_plugin.daemon.detector._create_client", return_value=mock_client
        ):
            from bible_cc_plugin.daemon.detector import detect_moments

            config = _default_config()
            turns = [_make_turn("user", content="hello")]
            result = detect_moments(
                turns, known_moments=None, phase=1, config=config
            )
            assert result == []


# ══════════════════════════════════════════════════════════════════════════════
# Feature 2c.2: Phase 2 Prompt
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildPhase2Prompt:
    """build_phase2_prompt() — retrospective prompt with known moments."""

    def test_contains_known_moments(self):
        """Known moments appear in 'ALREADY detected' section."""
        from bible_cc_plugin.daemon.detector import (
            MomentCandidate,
            build_phase2_prompt,
        )

        known = [
            MomentCandidate(type="decision", title="Use Postgres", narrative="...")
        ]
        turns = [_make_turn("user", content="started work")]
        prompt = build_phase2_prompt(turns, known)
        assert "ALREADY detected" in prompt or "already detected" in prompt.lower()
        assert "Use Postgres" in prompt

    def test_contains_dont_re_report(self):
        """Prompt instructs LLM NOT to re-report known moments."""
        from bible_cc_plugin.daemon.detector import (
            MomentCandidate,
            build_phase2_prompt,
        )

        known = [MomentCandidate(type="decision", title="T", narrative="N")]
        turns = [_make_turn("user", content="x")]
        prompt = build_phase2_prompt(turns, known)
        assert "Do NOT re-report" in prompt or "do not re-report" in prompt.lower()

    def test_excludes_session_start(self):
        """Phase 2 prompt must NOT include SESSION_START type."""
        from bible_cc_plugin.daemon.detector import build_phase2_prompt

        turns = [_make_turn("user", content="hello")]
        prompt = build_phase2_prompt(turns, known_moments=[])
        assert "SESSION_START" not in prompt


class TestBuildPhase2PromptTruncation:
    """Truncation: prompt must stay ≤ max_input_chars, omit ≥ 0, per-turn cap."""

    def _build_short_turn(self, i: int, chars: int = 100) -> dict:
        return _make_turn("user", content=f"turn {i} " + "x" * (chars - 10))

    def test_within_budget_no_truncation(self):
        """Prompt within budget — all turns included, no omission note."""
        from bible_cc_plugin.daemon.detector import build_phase2_prompt

        turns = [self._build_short_turn(i, 200) for i in range(5)]
        prompt = build_phase2_prompt(turns, [], max_input_chars=20000)
        assert "turns omitted" not in prompt
        assert "Turn 1" in prompt
        assert "Turn 5" in prompt

    def test_long_session_truncated_head_and_tail(self):
        """Long session overflow → head + omission + tail, still ≤ budget."""
        from bible_cc_plugin.daemon.detector import build_phase2_prompt

        # 200 turns × 300 chars = 60k chars → exceeds 10k budget
        turns = [self._build_short_turn(i, 300) for i in range(200)]
        prompt = build_phase2_prompt(turns, [], max_input_chars=10000)
        assert "turns omitted" in prompt
        assert "Turn 1" in prompt
        assert f"Turn {len(turns)}" in prompt
        assert len(prompt) <= 10000, (
            f"prompt={len(prompt)} > budget={10000}"
        )

    def test_single_huge_turn_truncated_per_turn(self):
        """One turn with 50k chars → per-turn content cap applied."""
        from bible_cc_plugin.daemon.detector import build_phase2_prompt

        huge = "a" * 50000
        turns = [
            _make_turn("user", content="intro"),
            _make_turn("assistant", tool_name="Bash", tool_output=huge),
            _make_turn("user", content="outro"),
        ]
        prompt = build_phase2_prompt(turns, [], max_input_chars=10000)
        assert huge not in prompt  # full 50k not present
        assert len(prompt) <= 10000, (
            f"prompt={len(prompt)} > budget=10000"
        )

    def test_short_session_no_negative_omitted(self):
        """3-turn session with tiny budget → omitted ≥ 0, no crash."""
        from bible_cc_plugin.daemon.detector import build_phase2_prompt

        turns = [self._build_short_turn(i, 500) for i in range(3)]
        prompt = build_phase2_prompt(turns, [], max_input_chars=500)
        # Must not crash; prompt must respect the configured budget.
        assert len(prompt) <= 500, (
            f"prompt={len(prompt)} > budget=500"
        )
        # No negative omission note
        assert "[-" not in prompt, "prompt contains negative omitted count"

    def test_known_moments_always_preserved(self):
        """Even when truncated, known moments section is always present."""
        from bible_cc_plugin.daemon.detector import (
            MomentCandidate,
            build_phase2_prompt,
        )

        known = [
            MomentCandidate(type="decision", title="D1", narrative="N1"),
            MomentCandidate(type="accomplishment", title="A1", narrative="N2"),
        ]
        turns = [self._build_short_turn(i, 500) for i in range(100)]
        prompt = build_phase2_prompt(turns, known, max_input_chars=2000)
        assert "D1" in prompt
        assert "A1" in prompt
        assert "Do NOT re-report" in prompt

    def test_large_known_moments_still_respect_budget(self):
        """Oversized known-moment preamble must not break strict budget."""
        from bible_cc_plugin.daemon.detector import (
            MomentCandidate,
            build_phase2_prompt,
        )

        known = [
            MomentCandidate(
                type="decision",
                title=f"Decision {i} " + "x" * 2000,
                narrative="N",
            )
            for i in range(50)
        ]
        turns = [self._build_short_turn(i, 300) for i in range(20)]
        prompt = build_phase2_prompt(turns, known, max_input_chars=32000)

        assert len(prompt) <= 32000
        assert "already-detected moments omitted" in prompt


class TestNoRealAPICallInCI:
    """Intent: CI 零成本 — stub mode prevents any real API usage."""

    def test_stub_mode_never_creates_real_client(self, monkeypatch):
        """With DETECTOR_TEST_MODE=true, stub path short-circuits immediately."""
        monkeypatch.setenv("DETECTOR_TEST_MODE", "1")

        from bible_cc_plugin.daemon.detector import call_detection_llm

        config = _default_config()
        # This must complete without touching network or env vars
        result = call_detection_llm("test", config, phase=1)
        assert isinstance(result, list)
