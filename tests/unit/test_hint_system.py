"""Unit tests for hint_system.py — format_hint + format_error_hint.

Phase 2d.2 + 2d.3 — all tests [Unit] [Pre].
Pure functions, no DB, no HTTP.
"""

from __future__ import annotations


def _make_moment(type="decision", title="Test", narrative="Test narrative"):
    from bible_cc_plugin.daemon.detector import MomentCandidate

    return MomentCandidate(type=type, title=title, narrative=narrative)


class TestFormatHint:
    """format_hint() — four modes per CLAUDE.md."""

    def test_quote_with_command(self):
        from bible_cc_plugin.hint_system import format_hint

        m = _make_moment(title="PostgreSQL")
        result = format_hint(m, "quote_with_command")
        assert "PostgreSQL" in result
        assert "Decision" in result
        assert "/bible-cc:review" in result
        assert "⎿ ⏳" in result

    def test_quote_only(self):
        from bible_cc_plugin.hint_system import format_hint

        m = _make_moment(title="PostgreSQL")
        result = format_hint(m, "quote_only")
        assert "PostgreSQL" in result
        assert "/bible-cc:review" not in result

    def test_command_only(self):
        from bible_cc_plugin.hint_system import format_hint

        m = _make_moment()
        result = format_hint(m, "command_only")
        assert "Key moment captured" in result
        assert "/bible-cc:review" in result
        assert m.title not in result

    def test_narrative(self):
        from bible_cc_plugin.hint_system import format_hint

        m = _make_moment(narrative="Chose PostgreSQL for concurrent writes.")
        result = format_hint(m, "narrative")
        assert m.narrative in result
        assert len(result) <= len(m.narrative) + 50

    def test_sanitizes_special_chars(self):
        from bible_cc_plugin.hint_system import format_hint

        m = _make_moment(title='Use "PostgreSQL" for auth')
        result = format_hint(m, "quote_with_command")
        assert result.count('"') <= 2

    def test_always_includes_enough_context(self):
        """Intent: all formats contain at least type + title."""
        from bible_cc_plugin.hint_system import format_hint

        m = _make_moment(type="accomplishment", title="Rate limiting done")
        for mode in ("quote_with_command", "quote_only", "command_only", "narrative"):
            result = format_hint(m, mode)
            assert m.title in result or "Accomplishment" in result, (
                f"mode={mode} missing context"
            )


class TestFormatErrorHint:
    """format_error_hint() — error → stderr-ready hint."""

    def test_port_conflict_hint(self):
        from bible_cc_plugin.hint_system import format_error_hint

        result = format_error_hint("port_conflict", "9777 occupied by pid 1234")
        assert "❌" in result
        assert "9777" in result
        assert "bible-cc" in result.lower()
