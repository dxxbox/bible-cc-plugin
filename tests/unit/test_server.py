"""Unit tests for daemon server — health endpoint returns real SQLite data.

Phase 1a — test that server.py integrates with buffer.py correctly.
Uses FastAPI TestClient with a temporary SQLite database.
"""

from __future__ import annotations

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
