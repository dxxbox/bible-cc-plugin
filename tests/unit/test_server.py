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

    conn = server_mod._get_db()
    assert conn is not None, f"DB init failed: {server_mod._db_error}"

    with TestClient(server_mod.app) as c:
        yield c

    conn.close()
    server_mod._db_conn = None
    server_mod._db_error = None


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
