"""Contract tests — /daemon/consult response schema and graceful degradation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

EXPECTED_HIT_KEYS = {
    "domain",
    "doc_id",
    "section_id",
    "section_title",
    "content",
    "score",
}


@pytest.fixture
def consult_env(tmp_path, monkeypatch, bible_test_url):
    """Point the daemon at a temp DB + test-mode server and reset globals."""
    monkeypatch.setenv("BIBLE_CC_DB_PATH", str(tmp_path / "daemon.db"))
    monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", bible_test_url)
    monkeypatch.setenv("DETECTOR_TEST_MODE", "1")

    import bible_cc_plugin.daemon.server as server_mod
    from bible_cc_plugin.daemon.buffer import session_seq

    async def _noop() -> None:
        return None

    monkeypatch.setattr(server_mod, "_connectivity_monitor", _noop)
    server_mod._db_conn = None
    server_mod._db_error = None
    server_mod._threshold_state.clear()
    server_mod._session_start_state.clear()
    server_mod._phase2_events.clear()
    server_mod._flush_locks.clear()
    server_mod._flush_log.clear()
    server_mod._config = server_mod.load_config()
    server_mod._app_config = server_mod._config
    session_seq.clear()

    with TestClient(server_mod.app) as client:
        yield server_mod, client

    if server_mod._db_conn is not None:
        server_mod._db_conn.close()
    server_mod._db_conn = None
    server_mod._db_error = None
    server_mod._threshold_state.clear()
    server_mod._session_start_state.clear()
    server_mod._phase2_events.clear()
    server_mod._flush_locks.clear()
    server_mod._config = server_mod.load_config()
    server_mod._app_config = server_mod._config
    session_seq.clear()


def _seed_user_turn(server_mod, session_id: str, text: str) -> None:
    from bible_cc_plugin.daemon.buffer import insert_session, insert_turn_user

    conn = server_mod._get_db()
    insert_session(conn, session_id)
    insert_turn_user(conn, session_id, text)


class TestConsultContract:
    def test_consult_response_schema(self, consult_env):
        _, client = consult_env
        r = client.post("/daemon/consult", json={"session_id": "s1", "query": "*"})
        assert r.status_code == 200

        data = r.json()
        assert set(data) == {"context", "query_used", "hits"}
        assert isinstance(data["context"], str)
        assert isinstance(data["query_used"], str)
        assert isinstance(data["hits"], list)
        assert data["context"].startswith("<relevant-memories>")
        assert data["context"].endswith("</relevant-memories>")
        scores = [h["score"] for h in data["hits"]]
        assert scores == sorted(scores, reverse=True)

    def test_consult_empty_query_schema(self, consult_env):
        server_mod, client = consult_env

        # No session / no turns → 200 with an explanatory context, not an error.
        r = client.post("/daemon/consult", json={"session_id": "unknown", "query": ""})
        assert r.status_code == 200
        data = r.json()
        assert data["hits"] == []
        assert "no query" in data["context"].lower()

        _seed_user_turn(server_mod, "s-empty", "How do we deploy BiBLE?")
        r = client.post("/daemon/consult", json={"session_id": "s-empty", "query": ""})
        assert r.status_code == 200
        data = r.json()
        assert data["query_used"] == "test search query"
        assert data["hits"]

    def test_consult_hit_fields(self, consult_env):
        _, client = consult_env
        r = client.post("/daemon/consult", json={"session_id": "s1", "query": "*"})
        assert r.status_code == 200

        hits = r.json()["hits"]
        assert hits
        for hit in hits:
            assert set(hit) == EXPECTED_HIT_KEYS

        by_domain = {h["domain"]: h for h in hits}
        assert set(by_domain) == {"memory", "knowledge", "skill"}

        memory = by_domain["memory"]
        assert memory["doc_id"] == "memory_fixture_001"
        assert memory["section_id"] == ""
        assert memory["section_title"] == "Fixture Memory"
        assert memory["content"] == "Built-in memory fixture result."

        skill = by_domain["skill"]
        assert skill["doc_id"] == "skill_fixture_001"
        assert skill["section_id"] == ""
        assert skill["section_title"] == "skill-standard"

        knowledge = by_domain["knowledge"]
        assert knowledge["doc_id"] == "design_fixture_001"
        assert knowledge["section_id"] == "1"
        assert knowledge["section_title"] == "Test Mode Design"

    def test_consult_error_schema(self, consult_env, monkeypatch):
        from bible_cc_plugin.daemon.client import BiBLEClient, BibleUnreachableError

        failing = AsyncMock(side_effect=BibleUnreachableError("down"))
        monkeypatch.setattr(BiBLEClient, "search_memory", failing)
        monkeypatch.setattr(BiBLEClient, "search_knowledge_base", failing)
        monkeypatch.setattr(BiBLEClient, "search_skill", failing)

        _, client = consult_env
        r = client.post("/daemon/consult", json={"session_id": "s1", "query": "x"})
        assert r.status_code == 200

        data = r.json()
        assert "error" not in data
        assert data["hits"] == []
        assert "unreachable" in data["context"].lower()
