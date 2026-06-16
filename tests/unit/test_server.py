"""Unit tests for daemon server — health endpoint returns real SQLite data.

Phase 1a — test that server.py integrates with buffer.py correctly.
Uses FastAPI TestClient with a temporary SQLite database.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Return a TestClient pointed at a temporary SQLite database."""
    db_path = str(tmp_path / "daemon.db")
    monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

    import bible_cc_plugin.daemon.server as server_mod

    server_mod._db_conn = None
    server_mod._db_error = None

    # Clear per-session seq counters to avoid cross-test pollution
    from bible_cc_plugin.daemon.buffer import session_seq

    session_seq.clear()

    conn = server_mod._get_db()
    assert conn is not None, f"DB init failed: {server_mod._db_error}"

    with TestClient(server_mod.app) as c:
        yield c

    conn.close()
    server_mod._db_conn = None
    server_mod._db_error = None
    session_seq.clear()


class TestHealthWithRealSQLite:
    """health endpoint must return real SQLite values, not hardcoded zeros."""

    def test_schema_version_ge_one(self, client):
        r = client.get("/daemon/health")
        assert r.status_code == 200
        data = r.json()
        assert data["sqlite"]["schema_version"] >= 1, (
            f"schema_version={data['sqlite']['schema_version']}, expected >= 1"
        )

    def test_integrity_is_ok(self, client):
        r = client.get("/daemon/health")
        data = r.json()
        assert data["sqlite"]["integrity"] == "ok"

    def test_size_bytes_positive(self, client):
        r = client.get("/daemon/health")
        data = r.json()
        assert data["sqlite"]["size_bytes"] > 0, (
            f"size_bytes={data['sqlite']['size_bytes']}, expected > 0"
        )

    def test_sessions_active_is_zero_with_no_sessions(self, client):
        """On a fresh DB with no sessions created, active must be 0."""
        r = client.get("/daemon/health")
        data = r.json()
        assert data["sessions"]["active"] == 0

    def test_buffer_structure_is_int(self, client):
        r = client.get("/daemon/health")
        data = r.json()
        assert isinstance(data["buffer"]["total_turns"], int)
        assert isinstance(data["buffer"]["pending_moments"], int)


class TestHealthDegraded:
    """health must not crash when SQLite is unavailable."""

    def test_health_still_returns_200_when_db_fails(self, tmp_path, monkeypatch):
        """If DB path is unwritable, health still returns 200 (degraded)."""
        monkeypatch.setenv("BIBLE_CC_DB_PATH", "/nonexistent/path/daemon.db")

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None

        with TestClient(server_mod.app) as c:
            r = c.get("/daemon/health")
            assert r.status_code == 200, "health must never crash"
            assert "sqlite" in r.json()

        server_mod._db_conn = None
        server_mod._db_error = None


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1b: Session / Turn endpoints
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionStart:
    """POST /session/start — create session, crash recovery scan."""

    def test_creates_new_session(self, client):
        r = client.post("/session/start", json={"session_id": "sess-1"})
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == "sess-1"
        assert data["is_new"] is True

    def test_idempotent_returns_is_new_false(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        r = client.post("/session/start", json={"session_id": "sess-1"})
        assert r.status_code == 200
        assert r.json()["is_new"] is False

    def test_detects_unclosed_sessions(self, client):
        client.post("/session/start", json={"session_id": "sess-old"})
        r = client.post("/session/start", json={"session_id": "sess-new"})
        assert r.status_code == 200
        recovery = r.json()["recovery"]
        assert recovery is not None
        assert recovery["unclosed_sessions_found"] >= 1

    def test_no_recovery_when_none_unclosed(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        client.post("/session/end", json={"session_id": "sess-1"})
        r = client.post("/session/start", json={"session_id": "sess-2"})
        assert r.json()["recovery"] is None

    def test_missing_session_id_returns_422(self, client):
        r = client.post("/session/start", json={})
        assert r.status_code in (400, 422)


class TestSessionEnd:
    """POST /session/end — mark session completed."""

    def test_marks_session_completed(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        r = client.post("/session/end", json={"session_id": "sess-1"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "completed"
        assert data["moments_flushed"] == 0

    def test_already_completed_returns_gracefully(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        client.post("/session/end", json={"session_id": "sess-1"})
        r = client.post("/session/end", json={"session_id": "sess-1"})
        assert r.status_code == 200
        assert r.json()["status"] in ("completed", "already_completed")

    def test_unknown_session_returns_404(self, client):
        r = client.post("/session/end", json={"session_id": "nonexistent"})
        assert r.status_code == 404

    def test_missing_session_id_returns_422(self, client):
        r = client.post("/session/end", json={})
        assert r.status_code in (400, 422)

    def test_queues_phase2_detection(self, client):
        """Active session end → detection='queued'."""
        client.post("/session/start", json={"session_id": "s2c1"})
        r = client.post("/session/end", json={"session_id": "s2c1"})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert r.json().get("detection") == "queued"

    def test_capture_disabled_no_queue(self, client):
        """enabled=false → detection=null."""
        import bible_cc_plugin.daemon.server as server_mod

        server_mod._app_config.capture.enabled = False
        client.post("/session/start", json={"session_id": "s2c1-d"})
        r = client.post("/session/end", json={"session_id": "s2c1-d"})
        assert r.json().get("detection") is None

        server_mod._app_config.capture.enabled = True

    def test_already_completed_no_queue(self, client):
        """Already completed → no queue."""
        client.post("/session/start", json={"session_id": "s2c1-ac"})
        client.post("/session/end", json={"session_id": "s2c1-ac"})
        r = client.post("/session/end", json={"session_id": "s2c1-ac"})
        assert r.json()["status"] == "already_completed"
        assert r.json().get("detection") is None

    def test_returns_before_detection_completes(self, client):
        """Endpoint returns <200ms."""
        import time

        client.post("/session/start", json={"session_id": "s2c1-async"})
        start = time.monotonic()
        r = client.post("/session/end", json={"session_id": "s2c1-async"})
        elapsed = (time.monotonic() - start) * 1000
        assert r.status_code == 200
        assert elapsed < 200, f"/session/end took {elapsed:.0f}ms"

    def test_resets_threshold_counter(self, client):
        """End clears threshold state for resource cleanup."""
        import bible_cc_plugin.daemon.server as server_mod

        client.post("/session/start", json={"session_id": "s2c1-rst"})
        for _ in range(3):
            client.post(
                "/turn/user", json={"session_id": "s2c1-rst", "message": "m"}
            )
        client.post("/session/end", json={"session_id": "s2c1-rst"})
        assert "s2c1-rst" not in server_mod._threshold_state


class TestTurnEndpoints:
    """POST /turn/user + POST /turn/tool — buffer turns."""

    def test_turn_user_creates_turn(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        r = client.post("/turn/user", json={"session_id": "sess-1", "message": "hello"})
        assert r.status_code == 200
        data = r.json()
        assert data["turn_id"] >= 1
        assert isinstance(data["queued"], bool)

    def test_turn_user_seq_increments(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        r1 = client.post("/turn/user", json={"session_id": "sess-1", "message": "m1"})
        r2 = client.post("/turn/user", json={"session_id": "sess-1", "message": "m2"})
        assert r1.json()["turn_id"] == 1
        assert r2.json()["turn_id"] == 2

    def test_turn_user_unknown_session_returns_400(self, client):
        r = client.post("/turn/user", json={"session_id": "nonexistent", "message": "hi"})
        assert r.status_code == 400

    def test_turn_user_completed_session_returns_400(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        client.post("/session/end", json={"session_id": "sess-1"})
        r = client.post("/turn/user", json={"session_id": "sess-1", "message": "hi"})
        assert r.status_code == 400

    def test_turn_tool_stores_full_output(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        big_output = "X" * 10000
        r = client.post(
            "/turn/tool",
            json={
                "session_id": "sess-1",
                "tool_name": "read_file",
                "arguments": {"path": "/f.py"},
                "output": big_output,
            },
        )
        assert r.status_code == 200
        assert r.json()["turn_id"] >= 1

    def test_turn_tool_seq_increments(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        client.post(
            "/turn/tool",
            json={
                "session_id": "sess-1",
                "tool_name": "t1",
                "arguments": {},
                "output": "o1",
            },
        )
        r2 = client.post(
            "/turn/tool",
            json={
                "session_id": "sess-1",
                "tool_name": "t2",
                "arguments": {},
                "output": "o2",
            },
        )
        assert r2.json()["turn_id"] == 2

    def test_turn_endpoints_return_quickly(self, client):
        """Intent: turn endpoints must return < 100ms."""
        import time

        client.post("/session/start", json={"session_id": "sess-1"})
        start = time.monotonic()
        client.post("/turn/user", json={"session_id": "sess-1", "message": "hi"})
        elapsed = (time.monotonic() - start) * 1000
        assert elapsed < 200, f"turn/user took {elapsed:.0f}ms"

    def test_turn_user_queues_on_threshold(self, client):
        """8th turn triggers threshold → queued=true."""
        import bible_cc_plugin.daemon.server as server_mod

        client.post("/session/start", json={"session_id": "s2b4"})
        # 7 turns → queued=false
        for _ in range(7):
            r = client.post(
                "/turn/user", json={"session_id": "s2b4", "message": "msg"}
            )
            assert r.json()["queued"] is False
        # 8th turn → threshold → queued=true
        r = client.post(
            "/turn/user", json={"session_id": "s2b4", "message": "trigger"}
        )
        assert r.json()["queued"] is True
        # Clean up threshold state
        server_mod.reset_threshold("s2b4")

    def test_turn_user_no_queue_when_capture_disabled(self, client):
        """capture.enabled=false → never queues."""
        import bible_cc_plugin.daemon.server as server_mod

        server_mod._app_config.capture.enabled = False
        client.post("/session/start", json={"session_id": "s2b4-d"})
        for _ in range(10):
            r = client.post(
                "/turn/user", json={"session_id": "s2b4-d", "message": "msg"}
            )
            assert r.json()["queued"] is False

        server_mod._app_config.capture.enabled = True
        server_mod.reset_threshold("s2b4-d")


class TestSessionsList:
    """GET /daemon/sessions — list active/completed sessions."""

    def test_returns_empty_list_initially(self, client):
        r = client.get("/daemon/sessions")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) or "sessions" in data

    def test_lists_active_and_completed(self, client):
        client.post("/session/start", json={"session_id": "a"})
        client.post("/session/start", json={"session_id": "b"})
        client.post("/session/end", json={"session_id": "b"})

        r = client.get("/daemon/sessions")
        assert r.status_code == 200
        data = r.json()
        sessions = data if isinstance(data, list) else data.get("sessions", [])
        assert len(sessions) >= 2


class TestEndpointIntent:
    """Intent: Phase boundaries, error propagation."""

    def test_end_session_does_no_llm_or_bible_call(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        r = client.post("/session/end", json={"session_id": "sess-1"})
        assert r.status_code == 200
        assert r.json()["moments_flushed"] == 0

    def test_turn_tool_accepts_output_not_tool_result(self, client):
        """The field name must be 'output', not 'tool_result'."""
        client.post("/session/start", json={"session_id": "sess-1"})
        r = client.post(
            "/turn/tool",
            json={
                "session_id": "sess-1",
                "tool_name": "test",
                "arguments": {},
                "output": "correct-field-name",
            },
        )
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1c: Context Injection
# ═══════════════════════════════════════════════════════════════════════════


class TestContextInject:
    """POST /context/inject — three-scenario branching."""

    def test_new_session_skip_fallback(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        r = client.post(
            "/context/inject",
            json={"session_id": "sess-1", "user_message": "hi"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "context" in data
        assert "sources" in data

    def test_clear_scenario_has_turns_summary(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        client.post("/turn/user", json={"session_id": "sess-1", "message": "important work"})
        r = client.post(
            "/context/inject",
            json={"session_id": "sess-1", "user_message": "continue"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["sources"]["turns"] >= 1

    def test_crash_recovery_scenario(self, client):
        client.post("/session/start", json={"session_id": "old"})
        r = client.post("/session/start", json={"session_id": "new"})
        recovery = r.json().get("recovery")
        assert recovery is not None

        r = client.post(
            "/context/inject",
            json={"session_id": "new", "user_message": "hello"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["sources"]["crash_recovery"] >= 0

    def test_disabled_injection_returns_empty(self, client):
        client.post("/session/start", json={"session_id": "sess-1"})
        r = client.post(
            "/context/inject",
            json={"session_id": "sess-1", "user_message": "hi"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "context" in data
        assert isinstance(data["sources"], dict)

    def test_missing_session_id_returns_422(self, client):
        r = client.post("/context/inject", json={})
        assert r.status_code in (400, 422)

    def test_empty_fallback_returns_xml_block(self, client):
        """When inject_fallback='empty', new session returns <relevant-memories></relevant-memories>."""
        import bible_cc_plugin.daemon.server as server_mod

        server_mod._config.injection.inject_fallback = "empty"
        client.post("/session/start", json={"session_id": "sess-empty"})
        r = client.post(
            "/context/inject",
            json={"session_id": "sess-empty", "user_message": "hi"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["context"] == "<relevant-memories></relevant-memories>"
        assert data["sources"]["turns"] == 0
        assert data["sources"]["moments"] == 0

    def test_skip_fallback_returns_empty_string(self, client):
        """When inject_fallback='skip', new session returns empty string."""
        import bible_cc_plugin.daemon.server as server_mod

        server_mod._config.injection.inject_fallback = "skip"
        client.post("/session/start", json={"session_id": "sess-skip"})
        r = client.post(
            "/context/inject",
            json={"session_id": "sess-skip", "user_message": "hi"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["context"] == ""
        assert data["sources"]["turns"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1d: Operability
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2b.3: Detection Pipeline
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectionPipeline:
    """_process_detection_task — full pipeline: turns→LLM→hash→INSERT."""

    @pytest.mark.asyncio
    async def test_stores_moment(self, tmp_path, monkeypatch):
        """Mock detector returns 1 candidate → written to moments table."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import insert_session, insert_turn_user, session_seq
        session_seq.clear()

        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "s1")
        # Must have turns for detection to work
        insert_turn_user(conn, "s1", "hello")

        from bible_cc_plugin.daemon.detector import MomentCandidate

        def mock_detect(turns, known_moments, phase, config):
            return [MomentCandidate(type="decision", title="T", narrative="N")]

        with patch(
            "bible_cc_plugin.daemon.detector.detect_moments", mock_detect
        ):
            task = {"session_id": "s1", "phase": 1}
            await server_mod._process_detection_task(task)

        from bible_cc_plugin.daemon.buffer import get_moments_by_session

        moments = get_moments_by_session(conn, "s1")
        assert len(moments) == 1
        assert moments[0]["moment_type"] == "decision"

        conn.close()
        server_mod._db_conn = None
        server_mod._db_error = None
        session_seq.clear()

    @pytest.mark.asyncio
    async def test_dedup_same_hash(self, tmp_path, monkeypatch):
        """Duplicate detection → content-hash collides → only 1 row."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import insert_session, insert_turn_user, session_seq
        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "s1")
        insert_turn_user(conn, "s1", "hello")

        from bible_cc_plugin.daemon.detector import MomentCandidate

        def mock_detect(turns, known_moments, phase, config):
            return [MomentCandidate(type="decision", title="D", narrative="N")]

        with patch(
            "bible_cc_plugin.daemon.detector.detect_moments", mock_detect
        ):
            task = {"session_id": "s1", "phase": 1}
            await server_mod._process_detection_task(task)
            await server_mod._process_detection_task(task)

        from bible_cc_plugin.daemon.buffer import get_moments_by_session

        moments = get_moments_by_session(conn, "s1")
        assert len(moments) == 1

        conn.close()
        server_mod._db_conn = None
        session_seq.clear()

    @pytest.mark.asyncio
    async def test_none_result_skips(self, tmp_path, monkeypatch):
        """Detector returns [] → no moment written."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import insert_session, session_seq
        session_seq.clear()
        conn = server_mod._get_db()
        insert_session(conn, "s1")

        def mock_detect(turns, known_moments, phase, config):
            return []

        with patch(
            "bible_cc_plugin.daemon.detector.detect_moments", mock_detect
        ):
            await server_mod._process_detection_task({"session_id": "s1", "phase": 1})

        from bible_cc_plugin.daemon.buffer import get_moments_by_session

        moments = get_moments_by_session(conn, "s1")
        assert len(moments) == 0

        conn.close()
        server_mod._db_conn = None
        session_seq.clear()

    @pytest.mark.asyncio
    async def test_capture_disabled_skips(self, tmp_path, monkeypatch):
        """capture.enabled=false → early return, detector not called."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        server_mod._app_config.capture.enabled = False
        call_count = 0

        def mock_detect(turns, known_moments, phase, config):
            nonlocal call_count
            call_count += 1
            return []

        with patch(
            "bible_cc_plugin.daemon.detector.detect_moments", mock_detect
        ):
            await server_mod._process_detection_task({"session_id": "s1", "phase": 1})
            assert call_count == 0

        server_mod._app_config.capture.enabled = True
        server_mod._db_conn = None

    @pytest.mark.asyncio
    async def test_non_key_moment_types_filtered(self, tmp_path, monkeypatch):
        """Non-key type (bug_fix) → filtered, not stored."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import insert_session, session_seq
        session_seq.clear()
        conn = server_mod._get_db()
        insert_session(conn, "s1")

        from bible_cc_plugin.daemon.detector import MomentCandidate

        def mock_detect(turns, known_moments, phase, config):
            return [MomentCandidate(type="bug_fix", title="F", narrative="N")]

        with patch(
            "bible_cc_plugin.daemon.detector.detect_moments", mock_detect
        ):
            await server_mod._process_detection_task({"session_id": "s1", "phase": 1})

        from bible_cc_plugin.daemon.buffer import get_moments_by_session

        moments = get_moments_by_session(conn, "s1")
        assert len(moments) == 0

        conn.close()
        server_mod._db_conn = None
        session_seq.clear()


class TestDebugDetectionEndpoints:
    """Debug endpoints for detection history — only when BIBLE_CC_DEBUG=true."""

    def test_requires_debug_mode(self, client):
        """Without debug mode, endpoint returns 404."""
        r = client.get("/daemon/debug/detections?session_id=x")
        assert r.status_code == 404

    def test_returns_history(self, tmp_path, monkeypatch):
        """With debug mode, returns detection records."""
        import bible_cc_plugin.daemon.server as server_mod

        server_mod._debug_mode = True
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        server_mod._db_conn = None
        server_mod._db_error = None

        from bible_cc_plugin.daemon.buffer import (
            compute_content_hash,
            insert_moment,
            insert_session,
            session_seq,
        )

        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "det-s1")
        ch = compute_content_hash("det-s1", "Test", "Narrative")
        insert_moment(conn, "det-s1", "decision", "Test", "Narrative", ch, phase="1")

        with TestClient(server_mod.app) as c:
            r = c.get("/daemon/debug/detections?session_id=det-s1")
            assert r.status_code == 200
            data = r.json()
            assert "detections" in data
            assert len(data["detections"]) >= 1

        server_mod._debug_mode = False
        server_mod._db_conn = None
        server_mod._db_error = None
        session_seq.clear()

    def test_stats_returns_counters(self, tmp_path, monkeypatch):
        """GET /daemon/debug/detections/stats returns aggregated counters."""
        import bible_cc_plugin.daemon.server as server_mod

        server_mod._debug_mode = True
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        server_mod._db_conn = None
        server_mod._db_error = None

        from bible_cc_plugin.daemon.buffer import session_seq

        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None

        with TestClient(server_mod.app) as c:
            r = c.get("/daemon/debug/detections/stats")
            assert r.status_code == 200
            data = r.json()
            assert "total" in data
            assert "phase1" in data
            assert "dedup_hits" in data
            assert "avg_latency_ms" in data

        server_mod._debug_mode = False
        server_mod._db_conn = None
        server_mod._db_error = None
        session_seq.clear()


class TestRequestIDMiddleware:
    """1d.4: every response must include X-Request-ID header."""

    def test_response_has_request_id_header(self, client):
        r = client.get("/daemon/health")
        assert r.status_code == 200
        assert "x-request-id" in r.headers

    def test_request_id_unique_per_request(self, client):
        ids = {client.get("/daemon/health").headers["x-request-id"] for _ in range(5)}
        assert len(ids) == 5, "each request must have a unique request-id"

    def test_error_responses_also_have_request_id(self, client):
        r = client.post("/session/start", json={})
        assert r.status_code in (400, 422)
        assert "x-request-id" in r.headers, "error responses must include request-id"


class TestVerboseHealth:
    """1d.3: GET /daemon/health?verbose=true adds diagnostic fields."""

    def test_verbose_health_has_additional_fields(self, client):
        r = client.get("/daemon/health?verbose=true")
        assert r.status_code == 200
        data = r.json()
        # Standard fields still present
        assert data["status"] == "ok"
        # Verbose extras
        assert "startup_timings" in data
        assert "sqlite_detailed" in data or "sqlite" in data

    def test_standard_health_still_works(self, client):
        r = client.get("/daemon/health")
        assert r.status_code == 200
        assert "status" in r.json()


class TestDebugEndpoints:
    """1d.2: debug endpoints only available in debug mode."""

    def test_debug_schema_returns_ddl(self, client):
        """Requires --debug mode for the daemon, so 404 is expected in tests."""
        r = client.get("/daemon/debug/schema")
        # Without --debug, should 404
        assert r.status_code == 404

    def test_debug_tables_requires_debug_mode(self, client):
        r = client.get("/daemon/debug/tables/sessions?limit=5")
        assert r.status_code == 404

    def test_debug_turns_requires_debug_mode(self, client):
        r = client.get("/daemon/debug/turns?session_id=test&limit=10")
        assert r.status_code == 404
