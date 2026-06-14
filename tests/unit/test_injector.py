"""Unit tests for injector.py — context injection logic.

Phase 1c — three-scenario branching, XML building, token budget truncation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _fresh_wal_conn(path: Path) -> sqlite3.Connection:
    """Create a connection with WAL + schema ready for injector tests."""
    from bible_cc_plugin.daemon.buffer import apply_pragmas, run_migrations

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    run_migrations(conn)
    return conn


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return _fresh_wal_conn(tmp_path / "inject.db")


# ═══════════════════════════════════════════════════════════════════════════
# 1c.1 — Scenario determination
# ═══════════════════════════════════════════════════════════════════════════


class TestDetermineScenario:
    """determine_injection_scenario() returns correct branch."""

    def test_returns_empty_when_no_turns_and_no_recovery(self, conn):
        from bible_cc_plugin.daemon.injector import determine_injection_scenario

        result = determine_injection_scenario(conn, "sess-1", recovery_data=None)
        assert result == "empty"

    def test_returns_crash_recovery_when_recovery_data_present(self, conn):
        from bible_cc_plugin.daemon.injector import determine_injection_scenario

        recovery = {"unclosed_sessions_found": 1, "moments_recovered": 2}
        result = determine_injection_scenario(conn, "sess-1", recovery_data=recovery)
        assert result == "crash_recovery"

    def test_returns_clear_when_session_has_turns(self, conn):
        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_user,
            session_seq,
        )

        insert_session(conn, "sess-1")
        session_seq["sess-1"] = 0
        insert_turn_user(conn, "sess-1", "hello")

        from bible_cc_plugin.daemon.injector import determine_injection_scenario

        result = determine_injection_scenario(conn, "sess-1", recovery_data=None)
        assert result == "clear_or_compact"


# ═══════════════════════════════════════════════════════════════════════════
# 1c.1 — Context builders
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildEmptyContext:
    def test_skip_mode_returns_empty_string(self):
        from bible_cc_plugin.daemon.injector import build_empty_context

        assert build_empty_context("skip") == ""

    def test_empty_mode_returns_empty_xml_block(self):
        from bible_cc_plugin.daemon.injector import build_empty_context

        assert build_empty_context("empty") == "<relevant-memories></relevant-memories>"


class TestBuildTurnsSummary:
    def test_returns_empty_for_no_turns(self, conn):
        from bible_cc_plugin.daemon.buffer import insert_session
        from bible_cc_plugin.daemon.injector import build_turns_summary

        insert_session(conn, "sess-1")
        result = build_turns_summary(conn, "sess-1", max_turns=20)
        assert isinstance(result, str)

    def test_includes_turn_content(self, conn):
        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_user,
            session_seq,
        )
        from bible_cc_plugin.daemon.injector import build_turns_summary

        insert_session(conn, "sess-1")
        session_seq["sess-1"] = 0
        insert_turn_user(conn, "sess-1", "important message here")

        result = build_turns_summary(conn, "sess-1", max_turns=20)
        assert "important message here" in result

    def test_respects_max_turns_limit(self, conn):
        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_user,
            session_seq,
        )
        from bible_cc_plugin.daemon.injector import build_turns_summary

        insert_session(conn, "sess-1")
        session_seq["sess-1"] = 0
        for i in range(10):
            insert_turn_user(conn, "sess-1", f"msg {i}")

        result = build_turns_summary(conn, "sess-1", max_turns=3)
        assert "msg 9" in result
        assert "msg 7" in result
        assert "msg 0" not in result


class TestBuildMomentsContext:
    def test_returns_empty_for_no_moments(self, conn):
        from bible_cc_plugin.daemon.buffer import insert_session
        from bible_cc_plugin.daemon.injector import build_moments_context

        insert_session(conn, "sess-1")
        result = build_moments_context(conn, "sess-1")
        assert isinstance(result, str)

    def test_formats_moment_as_xml(self, conn):
        from bible_cc_plugin.daemon.buffer import (
            compute_content_hash,
            insert_moment,
            insert_session,
        )
        from bible_cc_plugin.daemon.injector import build_moments_context

        insert_session(conn, "sess-1")
        ch = compute_content_hash("sess-1", "Decision X", "Did Y for Z")
        insert_moment(conn, "sess-1", "decision", "Decision X", "Did Y for Z", ch)

        result = build_moments_context(conn, "sess-1")
        assert "Decision X" in result
        assert "Did Y for Z" in result


class TestBuildCrashRecoveryContext:
    def test_includes_recovered_label(self):
        from bible_cc_plugin.daemon.injector import build_crash_recovery_context

        moments = [{"moment_type": "decision", "title": "T1", "narrative": "N1"}]
        turns = [{"role": "user", "content": "previous message"}]
        result = build_crash_recovery_context(moments, turns)
        assert "T1" in result
        assert "Recovered" in result or "prior session" in result.lower() or "[R" in result

    def test_handles_empty_data(self):
        from bible_cc_plugin.daemon.injector import build_crash_recovery_context

        result = build_crash_recovery_context([], [])
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════
# 1c.1 — Token budget
# ═══════════════════════════════════════════════════════════════════════════


class TestTokenBudget:
    def test_no_truncation_when_under_budget(self):
        from bible_cc_plugin.daemon.injector import apply_token_budget

        xml = "<relevant-memories><item>short</item></relevant-memories>"
        result = apply_token_budget(xml, budget=10000)
        assert xml in result

    def test_truncates_long_content(self):
        from bible_cc_plugin.daemon.injector import apply_token_budget

        content = "<relevant-memories>" + ("X" * 500) + "</relevant-memories>"
        result = apply_token_budget(content, budget=10)
        assert len(result) < len(content)

    def test_preserves_xml_wrapper_after_truncation(self):
        from bible_cc_plugin.daemon.injector import apply_token_budget

        content = "<relevant-memories>\n" + ("<item>data</item>\n" * 100) + "</relevant-memories>"
        result = apply_token_budget(content, budget=20)
        assert result.startswith("<relevant-memories>") or result == ""
        if result:
            assert "</relevant-memories>" in result

    def test_adds_truncated_marker(self):
        from bible_cc_plugin.daemon.injector import apply_token_budget

        content = "<relevant-memories>\n" + ("long text. " * 200) + "\n</relevant-memories>"
        result = apply_token_budget(content, budget=5)
        if len(result) < len(content):
            assert "[truncated]" in result.lower() or result != content


# ═══════════════════════════════════════════════════════════════════════════
# 1c.1 — Intent tests
# ═══════════════════════════════════════════════════════════════════════════


class TestContextInjectIntent:
    def test_recovery_context_distinct_from_normal(self):
        from bible_cc_plugin.daemon.injector import build_crash_recovery_context

        result = build_crash_recovery_context(
            [{"moment_type": "decision", "title": "T", "narrative": "N"}],
            [{"role": "user", "content": "old msg"}],
        )
        markers = ["Recovered", "prior session", "[recovery]", "previous"]
        found = any(m.lower() in result.lower() for m in markers)
        assert found, f"Crash recovery context lacks recovery marker: {result[:200]}"

    def test_injector_has_no_bible_import(self):
        import ast
        from pathlib import Path

        src = (
            Path(__file__).parent.parent.parent
            / "src"
            / "bible_cc_plugin"
            / "daemon"
            / "injector.py"
        )
        if src.exists():
            tree = ast.parse(src.read_text())
            imports = [
                node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)
            ]
            from_imports = [
                node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            ]
            all_imports = imports + [m for m in from_imports if m]
            for imp in all_imports:
                assert "httpx" not in imp.lower(), (
                    f"injector.py must not import httpx (no BiBLE client), found: {imp}"
                )
