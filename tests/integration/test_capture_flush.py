"""Integration tests — capture → flush pipeline against a real test-mode server."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import _find_free_port


def _free_port_url() -> str:
    return f"http://127.0.0.1:{_find_free_port()}"


@pytest.fixture
def flush_env(tmp_path, monkeypatch, bible_test_url):
    """Point the daemon module at a temp DB and the real test-mode server."""
    monkeypatch.setenv("BIBLE_CC_DB_PATH", str(tmp_path / "daemon.db"))
    monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", bible_test_url)

    import bible_cc_plugin.daemon.server as server_mod

    server_mod._db_conn = None
    server_mod._db_error = None
    server_mod._flush_locks.clear()
    server_mod._flush_log.clear()
    server_mod._app_config = server_mod.load_config()
    yield server_mod

    if server_mod._db_conn is not None:
        server_mod._db_conn.close()
    server_mod._db_conn = None
    server_mod._db_error = None
    server_mod._flush_locks.clear()


def _seed_session(server_mod, session_id: str, *, title: str = "Test moment"):
    from bible_cc_plugin.daemon.buffer import (
        compute_content_hash,
        insert_moment,
        insert_session,
    )

    conn = server_mod._get_db()
    insert_session(conn, session_id)
    ch = compute_content_hash(session_id, title, "narrative")
    insert_moment(conn, session_id, "lesson", title, "narrative", ch)
    return conn, ch


class TestFlushPipeline:
    @pytest.mark.asyncio
    async def test_full_flush_pipeline(self, flush_env):
        server_mod = flush_env
        conn, _ = _seed_session(server_mod, "it-flush-ok")

        result = await server_mod._flush_session_moments("it-flush-ok")

        assert result.flushed_count == 1
        assert result.task_id
        row = conn.execute(
            "SELECT flushed, import_task_id FROM moments WHERE session_id=?",
            ("it-flush-ok",),
        ).fetchone()
        assert row["flushed"] == 1
        assert row["import_task_id"] == result.task_id

    @pytest.mark.asyncio
    async def test_flush_failure_retry(self, flush_env, monkeypatch):
        server_mod = flush_env
        conn, _ = _seed_session(server_mod, "it-flush-fail")

        dead_url = _free_port_url()
        cfg = server_mod._app_config
        bible = cfg.bible.model_copy(update={"base_url": dead_url})
        monkeypatch.setattr(server_mod, "_app_config", cfg.model_copy(update={"bible": bible}))

        result = await server_mod._flush_session_moments("it-flush-fail")

        assert result.flushed_count == 0
        assert result.retryable is True
        row = conn.execute(
            "SELECT flushed, retry_count FROM moments WHERE session_id=?",
            ("it-flush-fail",),
        ).fetchone()
        assert row["flushed"] == 0
        assert row["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_mid_session_upload(self, flush_env):
        server_mod = flush_env
        conn, _ = _seed_session(server_mod, "it-flush-mid")
        moment_id = conn.execute(
            "SELECT id FROM moments WHERE session_id=?", ("it-flush-mid",)
        ).fetchone()["id"]

        result = await server_mod._flush_single_moment(conn, moment_id)

        assert result.flushed_count == 1
        row = conn.execute("SELECT flushed FROM moments WHERE id=?", (moment_id,)).fetchone()
        assert row["flushed"] == 1

    @pytest.mark.asyncio
    async def test_flush_idempotent(self, flush_env):
        server_mod = flush_env
        _seed_session(server_mod, "it-flush-idem")

        first = await server_mod._flush_session_moments("it-flush-idem")
        second = await server_mod._flush_session_moments("it-flush-idem")

        assert first.flushed_count == 1
        assert second.flushed_count == 0
        assert second.task_id is None

    def test_manual_flush_endpoint(self, flush_env):
        server_mod = flush_env
        _seed_session(server_mod, "it-flush-endpoint")

        with TestClient(server_mod.app) as client:
            r = client.post("/daemon/session/flush", json={"session_id": "it-flush-endpoint"})

        assert r.status_code == 200
        body = r.json()
        assert body["moments_flushed"] == 1
        assert body["flush_error"] is None

    @pytest.mark.asyncio
    async def test_poll_confirms_submitted_moments(self, flush_env):
        server_mod = flush_env
        conn, _ = _seed_session(server_mod, "it-flush-poll")

        flushed = await server_mod._flush_session_moments("it-flush-poll")
        assert flushed.task_id

        result = await server_mod._poll_flush_results("it-flush-poll", max_attempts=1)
        assert result["confirmed"] == 1

        row = conn.execute(
            "SELECT flushed FROM moments WHERE session_id=?", ("it-flush-poll",)
        ).fetchone()
        assert row["flushed"] == 2
