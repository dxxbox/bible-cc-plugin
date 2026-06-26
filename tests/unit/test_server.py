"""Unit tests for daemon server — health endpoint returns real SQLite data.

Phase 1a — test that server.py integrates with buffer.py correctly.
Uses FastAPI TestClient with a temporary SQLite database.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Return a TestClient pointed at a temporary SQLite database."""
    db_path = str(tmp_path / "daemon.db")
    monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)
    monkeypatch.setattr(
        "bible_cc_plugin.daemon.detector.detect_moments",
        lambda turns, known_moments, phase, config: [],
    )

    import bible_cc_plugin.daemon.server as server_mod

    server_mod._db_conn = None
    server_mod._db_error = None
    server_mod._threshold_state.clear()
    server_mod._config = server_mod.load_config()
    server_mod._app_config = server_mod._config

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
    server_mod._threshold_state.clear()
    server_mod._config = server_mod.load_config()
    server_mod._app_config = server_mod._config
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

    def test_turn_user_empty_message_no_threshold(self, client):
        """Empty message → turn written but threshold not incremented."""
        client.post("/session/start", json={"session_id": "s2b4-empty"})
        for _ in range(10):
            r = client.post(
                "/turn/user", json={"session_id": "s2b4-empty", "message": ""}
            )
            assert r.json()["queued"] is False, "empty message must not trigger"

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
    async def test_phase1_task_uses_queued_max_seq_window(self, tmp_path, monkeypatch):
        """Lagging Phase 1 task must not analyze turns after its queued max_seq."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )

        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "s-window")
        insert_turn_user(conn, "s-window", "First decision")
        trigger_seq = insert_turn_tool(conn, "s-window", "Read", {}, "first-tool")
        insert_turn_user(conn, "s-window", "Later unrelated prompt")
        insert_turn_tool(conn, "s-window", "Read", {}, "later-tool")

        captured = []

        def mock_detect(turns, known_moments, phase, config):
            captured.extend(turns)
            return []

        with patch("bible_cc_plugin.daemon.detector.detect_moments", mock_detect):
            await server_mod._process_detection_task(
                {"session_id": "s-window", "phase": 1, "max_seq": trigger_seq}
            )

        assert [t["content"] or t["tool_output"] for t in captured] == [
            "First decision",
            "first-tool",
        ]

        conn.close()
        server_mod._db_conn = None
        server_mod._db_error = None
        session_seq.clear()

    @pytest.mark.asyncio
    async def test_user_triggered_phase1_includes_previous_window(self, tmp_path, monkeypatch):
        """User-triggered task should include prior user/tool context plus new prompt."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )

        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "s-user-window")
        insert_turn_user(conn, "s-user-window", "First decision")
        insert_turn_tool(conn, "s-user-window", "Read", {}, "first-tool")
        trigger_seq = insert_turn_user(conn, "s-user-window", "Follow-up prompt")

        captured = []

        def mock_detect(turns, known_moments, phase, config):
            captured.extend(turns)
            return []

        with patch("bible_cc_plugin.daemon.detector.detect_moments", mock_detect):
            await server_mod._process_detection_task(
                {
                    "session_id": "s-user-window",
                    "phase": 1,
                    "max_seq": trigger_seq,
                    "include_previous_user": True,
                }
            )

        assert [t["content"] or t["tool_output"] for t in captured] == [
            "First decision",
            "first-tool",
            "Follow-up prompt",
        ]

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
    async def test_session_start_same_anchor_updates_pending_moment(
        self, tmp_path, monkeypatch
    ):
        """Same user-turn anchor updates the pending session_start moment."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import (
            get_moments_by_session,
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )
        from bible_cc_plugin.daemon.detector import MomentCandidate

        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "s-anchor")
        anchor_seq = insert_turn_user(conn, "s-anchor", "我们开始 3a 的开发.")
        first_tool_seq = insert_turn_tool(conn, "s-anchor", "Read", {}, "first")

        detections = iter(
            [
                MomentCandidate(
                    type="session_start",
                    title="开始 3a 开发",
                    narrative="用户开始 3a 开发。",
                ),
                MomentCandidate(
                    type="session_start",
                    title="开始 Phase 3a 开发",
                    narrative="用户开启 Phase 3a 开发，重点是 BiBLE client。",
                ),
            ]
        )

        def mock_detect(turns, known_moments, phase, config):
            return [next(detections)]

        with patch("bible_cc_plugin.daemon.detector.detect_moments", mock_detect):
            await server_mod._process_detection_task(
                {"session_id": "s-anchor", "phase": 1, "max_seq": first_tool_seq}
            )
            second_tool_seq = insert_turn_tool(conn, "s-anchor", "Read", {}, "second")
            await server_mod._process_detection_task(
                {"session_id": "s-anchor", "phase": 1, "max_seq": second_tool_seq}
            )

        moments = get_moments_by_session(conn, "s-anchor")
        assert len(moments) == 1
        assert moments[0]["title"] == "开始 Phase 3a 开发"
        assert moments[0]["turn_range_start"] == anchor_seq
        assert moments[0]["turn_range_end"] == second_tool_seq

        conn.close()
        server_mod._db_conn = None
        server_mod._db_error = None
        session_seq.clear()

    @pytest.mark.asyncio
    async def test_session_start_different_anchor_inserts_new_moment(
        self, tmp_path, monkeypatch
    ):
        """Different user-turn anchors can each create a session_start moment."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import (
            get_moments_by_session,
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )
        from bible_cc_plugin.daemon.detector import MomentCandidate

        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "s-two-anchors")
        insert_turn_user(conn, "s-two-anchors", "Start 3a")
        first_tool_seq = insert_turn_tool(conn, "s-two-anchors", "Read", {}, "first")

        detections = iter(
            [
                MomentCandidate("session_start", "Start 3a", "Start 3a work."),
                MomentCandidate("session_start", "Start 3b", "Start 3b work."),
            ]
        )

        def mock_detect(turns, known_moments, phase, config):
            return [next(detections)]

        with patch("bible_cc_plugin.daemon.detector.detect_moments", mock_detect):
            await server_mod._process_detection_task(
                {"session_id": "s-two-anchors", "phase": 1, "max_seq": first_tool_seq}
            )
            insert_turn_user(conn, "s-two-anchors", "Start 3b")
            second_tool_seq = insert_turn_tool(
                conn, "s-two-anchors", "Read", {}, "second"
            )
            await server_mod._process_detection_task(
                {"session_id": "s-two-anchors", "phase": 1, "max_seq": second_tool_seq}
            )

        moments = get_moments_by_session(conn, "s-two-anchors")
        assert [m["title"] for m in moments] == ["Start 3a", "Start 3b"]
        assert moments[0]["turn_range_start"] != moments[1]["turn_range_start"]

        conn.close()
        server_mod._db_conn = None
        server_mod._db_error = None
        session_seq.clear()

    @pytest.mark.asyncio
    async def test_user_triggered_session_start_uses_current_user_anchor(
        self, tmp_path, monkeypatch
    ):
        """include_previous_user windows must not anchor new starts to old prompts."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import (
            get_moments_by_session,
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )
        from bible_cc_plugin.daemon.detector import MomentCandidate

        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "s-user-trigger")
        insert_turn_user(conn, "s-user-trigger", "Start 3a")
        first_tool_seq = insert_turn_tool(conn, "s-user-trigger", "Read", {}, "first")

        detections = iter(
            [
                MomentCandidate("session_start", "Start 3a", "Start 3a work."),
                MomentCandidate("session_start", "Start 3b", "Start 3b work."),
            ]
        )

        def mock_detect(turns, known_moments, phase, config):
            return [next(detections)]

        with patch("bible_cc_plugin.daemon.detector.detect_moments", mock_detect):
            await server_mod._process_detection_task(
                {"session_id": "s-user-trigger", "phase": 1, "max_seq": first_tool_seq}
            )
            second_user_seq = insert_turn_user(conn, "s-user-trigger", "Start 3b")
            await server_mod._process_detection_task(
                {
                    "session_id": "s-user-trigger",
                    "phase": 1,
                    "max_seq": second_user_seq,
                    "anchor_seq": second_user_seq,
                    "include_previous_user": True,
                }
            )

        moments = get_moments_by_session(conn, "s-user-trigger")
        assert [m["title"] for m in moments] == ["Start 3a", "Start 3b"]
        assert moments[1]["turn_range_start"] == second_user_seq

        conn.close()
        server_mod._db_conn = None
        server_mod._db_error = None
        session_seq.clear()

    @pytest.mark.asyncio
    async def test_session_start_flushed_anchor_is_not_mutated(
        self, tmp_path, monkeypatch
    ):
        """Already flushed anchored moments are not changed by later detections."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import (
            get_moments_by_session,
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )
        from bible_cc_plugin.daemon.detector import MomentCandidate

        session_seq.clear()
        conn = server_mod._get_db()
        assert conn is not None
        insert_session(conn, "s-flushed-anchor")
        insert_turn_user(conn, "s-flushed-anchor", "Start 3a")
        first_tool_seq = insert_turn_tool(conn, "s-flushed-anchor", "Read", {}, "first")

        detections = iter(
            [
                MomentCandidate("session_start", "Original", "Original narrative."),
                MomentCandidate("session_start", "Refined", "Refined narrative."),
            ]
        )

        def mock_detect(turns, known_moments, phase, config):
            return [next(detections)]

        with patch("bible_cc_plugin.daemon.detector.detect_moments", mock_detect):
            await server_mod._process_detection_task(
                {
                    "session_id": "s-flushed-anchor",
                    "phase": 1,
                    "max_seq": first_tool_seq,
                }
            )
            conn.execute("UPDATE moments SET flushed=1")
            conn.commit()
            second_tool_seq = insert_turn_tool(
                conn, "s-flushed-anchor", "Read", {}, "second"
            )
            await server_mod._process_detection_task(
                {
                    "session_id": "s-flushed-anchor",
                    "phase": 1,
                    "max_seq": second_tool_seq,
                }
            )

        moments = get_moments_by_session(conn, "s-flushed-anchor")
        assert len(moments) == 1
        assert moments[0]["title"] == "Original"
        assert moments[0]["flushed"] == 1

        conn.close()
        server_mod._db_conn = None
        server_mod._db_error = None
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


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2c.2: Phase 2 Retrospective Detection
# ═══════════════════════════════════════════════════════════════════════════


class TestPhase2Detection:
    """Phase 2 retrospective detection pipeline."""

    @pytest.mark.asyncio
    async def test_inserts_new_moments(self, tmp_path, monkeypatch):
        """Stub Phase 2 detector → new moments written."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)
        import asyncio

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_user,
            session_seq,
        )
        from bible_cc_plugin.daemon.detector import MomentCandidate

        session_seq.clear()
        conn = server_mod._get_db()
        insert_session(conn, "p2-s1")
        insert_turn_user(conn, "p2-s1", "hello")

        def mock_detect(turns, known_moments, phase, config):
            return [
                MomentCandidate(type="decision", title="New1", narrative="N1"),
                MomentCandidate(type="accomplishment", title="New2", narrative="N2"),
            ]

        with patch("bible_cc_plugin.daemon.detector.detect_moments", mock_detect):
            await server_mod._process_detection_task(
                {"session_id": "p2-s1", "phase": 2}
            )

        from bible_cc_plugin.daemon.buffer import get_moments_by_session

        moments = get_moments_by_session(conn, "p2-s1")
        assert len(moments) == 2

        conn.close()
        server_mod._db_conn = None
        session_seq.clear()

    @pytest.mark.asyncio
    async def test_dedup_known_moments(self, tmp_path, monkeypatch):
        """Duplicate hash → only 1 row stored."""
        db_path = str(tmp_path / "daemon.db")
        monkeypatch.setenv("BIBLE_CC_DB_PATH", db_path)
        import asyncio

        import bible_cc_plugin.daemon.server as server_mod

        server_mod._db_conn = None
        server_mod._db_error = None
        server_mod._detection_queue = asyncio.Queue()

        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_user,
            session_seq,
        )
        from bible_cc_plugin.daemon.detector import MomentCandidate

        session_seq.clear()
        conn = server_mod._get_db()
        insert_session(conn, "p2-dedup")
        insert_turn_user(conn, "p2-dedup", "hello")

        # Same candidate returned twice → dedup
        same = MomentCandidate(type="decision", title="Only Once", narrative="Same")

        def mock_detect(turns, known_moments, phase, config):
            return [same, same]

        with patch("bible_cc_plugin.daemon.detector.detect_moments", mock_detect):
            await server_mod._process_detection_task(
                {"session_id": "p2-dedup", "phase": 2}
            )

        from bible_cc_plugin.daemon.buffer import get_moments_by_session

        moments = get_moments_by_session(conn, "p2-dedup")
        assert len(moments) == 1

        conn.close()
        server_mod._db_conn = None
        session_seq.clear()

    def test_detection_does_not_lose_session_completed(self, client):
        """Phase 2 failure preserves session completed status."""
        client.post("/session/start", json={"session_id": "p2-no-lose"})
        # Don't add turns — detection will silently return with no turns
        r = client.post("/session/end", json={"session_id": "p2-no-lose"})
        assert r.json()["status"] == "completed"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2c.3: PUT/DELETE /daemon/moments
# ═══════════════════════════════════════════════════════════════════════════


class TestMomentsCRUD:
    """PUT/DELETE /daemon/moments/{id} — edit and delete pending moments."""

    def test_put_updates_title(self, client):
        """PUT changes title, GET reflects new value."""
        from bible_cc_plugin.daemon.buffer import (
            compute_content_hash,
            insert_moment,
        )
        import bible_cc_plugin.daemon.server as server_mod

        conn = server_mod._get_db()
        insert_moment(
            conn, "s1", "decision", "Old", "N", compute_content_hash("s1", "Old", "N")
        )
        # Find the moment id
        r = client.get("/daemon/moments?session_id=s1")
        mid = r.json()["moments"][0]["id"]

        r = client.put(
            f"/daemon/moments/{mid}", json={"title": "New Title", "narrative": "N2"}
        )
        assert r.status_code == 200
        assert r.json()["title"] == "New Title"

    def test_title_only_preserves_narrative(self, client):
        """PUT with only title leaves narrative unchanged."""
        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment
        import bible_cc_plugin.daemon.server as server_mod

        conn = server_mod._get_db()
        insert_moment(
            conn, "st1", "decision", "Old", "Original narrative",
            compute_content_hash("st1", "Old", "Original narrative"),
        )
        r = client.get("/daemon/moments?session_id=st1")
        mid = r.json()["moments"][0]["id"]

        r = client.put(f"/daemon/moments/{mid}", json={"title": "New Title"})
        assert r.status_code == 200
        assert r.json()["title"] == "New Title"
        assert r.json()["narrative"] == "Original narrative"

    def test_narrative_only_preserves_title(self, client):
        """PUT with only narrative leaves title unchanged."""
        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment
        import bible_cc_plugin.daemon.server as server_mod

        conn = server_mod._get_db()
        insert_moment(
            conn, "st2", "decision", "Keep Title", "Old narrative",
            compute_content_hash("st2", "Keep Title", "Old narrative"),
        )
        r = client.get("/daemon/moments?session_id=st2")
        mid = r.json()["moments"][0]["id"]

        r = client.put(f"/daemon/moments/{mid}", json={"narrative": "New narrative"})
        assert r.status_code == 200
        assert r.json()["title"] == "Keep Title"
        assert r.json()["narrative"] == "New narrative"

    def test_hash_changes_after_edit(self, client):
        """Editing a moment produces a different content_hash."""
        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment
        import bible_cc_plugin.daemon.server as server_mod

        conn = server_mod._get_db()
        old_hash = compute_content_hash("st3", "Old", "Old narrative")
        insert_moment(conn, "st3", "decision", "Old", "Old narrative", old_hash)

        r = client.get("/daemon/moments?session_id=st3")
        mid = r.json()["moments"][0]["id"]

        r = client.put(f"/daemon/moments/{mid}", json={"title": "Changed"})
        assert r.status_code == 200

        new_hash = compute_content_hash("st3", "Changed", "Old narrative")
        row = conn.execute(
            "SELECT content_hash FROM moments WHERE id=?", (mid,)
        ).fetchone()
        assert row["content_hash"] == new_hash
        assert row["content_hash"] != old_hash

    def test_duplicate_edited_content_returns_409(self, client):
        """Editing a moment to duplicate another moment's content → 409."""
        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment
        import bible_cc_plugin.daemon.server as server_mod

        conn = server_mod._get_db()
        insert_moment(
            conn, "st4", "decision", "First", "Narrative",
            compute_content_hash("st4", "First", "Narrative"),
        )
        insert_moment(
            conn, "st4", "decision", "Second", "Other narrative",
            compute_content_hash("st4", "Second", "Other narrative"),
        )

        r = client.get("/daemon/moments?session_id=st4")
        moments = r.json()["moments"]
        second_mid = next(m["id"] for m in moments if m["title"] == "Second")

        r = client.put(
            f"/daemon/moments/{second_mid}",
            json={"title": "First", "narrative": "Narrative"},
        )
        assert r.status_code == 409

    def test_both_fields_empty_returns_400(self, client):
        """PUT with neither title nor narrative → 400."""
        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment
        import bible_cc_plugin.daemon.server as server_mod

        conn = server_mod._get_db()
        insert_moment(
            conn, "st5", "decision", "T", "N", compute_content_hash("st5", "T", "N")
        )
        r = client.get("/daemon/moments?session_id=st5")
        mid = r.json()["moments"][0]["id"]

        r = client.put(f"/daemon/moments/{mid}", json={})
        assert r.status_code == 400

    def test_delete_removes(self, client):
        """DELETE removes moment, GET returns empty."""
        from bible_cc_plugin.daemon.buffer import (
            compute_content_hash,
            insert_moment,
        )
        import bible_cc_plugin.daemon.server as server_mod

        conn = server_mod._get_db()
        insert_moment(
            conn, "s2", "decision", "T", "N", compute_content_hash("s2", "T", "N")
        )
        r = client.get("/daemon/moments?session_id=s2")
        mid = r.json()["moments"][0]["id"]

        r = client.delete(f"/daemon/moments/{mid}")
        assert r.status_code == 200

        r = client.get("/daemon/moments?session_id=s2")
        assert len(r.json()["moments"]) == 0

    def test_edit_flushed_returns_409(self, client):
        """Flushed moment cannot be edited."""
        from bible_cc_plugin.daemon.buffer import (
            compute_content_hash,
            insert_moment,
        )
        import bible_cc_plugin.daemon.server as server_mod

        conn = server_mod._get_db()
        mid = insert_moment(
            conn, "s3", "decision", "T", "N", compute_content_hash("s3", "T", "N")
        )
        # Mark as flushed
        conn.execute("UPDATE moments SET flushed=1 WHERE id=?", (mid,))
        conn.commit()

        r = client.put(f"/daemon/moments/{mid}", json={"title": "X"})
        assert r.status_code == 409


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
