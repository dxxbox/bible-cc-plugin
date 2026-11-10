"""Integration tests — /daemon/consult against the real BiBLE test-mode server."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


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


def _patch_searches(monkeypatch, *, memory, knowledge, skill):
    from bible_cc_plugin.daemon.client import BiBLEClient

    monkeypatch.setattr(BiBLEClient, "search_memory", memory)
    monkeypatch.setattr(BiBLEClient, "search_knowledge_base", knowledge)
    monkeypatch.setattr(BiBLEClient, "search_skill", skill)


class TestConsultRealServer:
    def test_consult_with_query(self, consult_env):
        _, client = consult_env
        r = client.post("/daemon/consult", json={"session_id": "s1", "query": "*"})
        assert r.status_code == 200

        data = r.json()
        assert {h["domain"] for h in data["hits"]} == {"memory", "knowledge", "skill"}
        assert data["context"].startswith("<relevant-memories>")
        assert data["context"].endswith("</relevant-memories>")
        scores = [h["score"] for h in data["hits"]]
        assert scores == sorted(scores, reverse=True)

    def test_consult_empty_query(self, consult_env):
        server_mod, client = consult_env
        _seed_user_turn(server_mod, "s-empty", "How do we deploy BiBLE?")
        r = client.post("/daemon/consult", json={"session_id": "s-empty", "query": ""})
        assert r.status_code == 200

        data = r.json()
        assert data["query_used"] == "test search query"
        # knowledge fixture matches tag=design regardless of query.
        assert data["hits"]
        assert any(h["domain"] == "knowledge" for h in data["hits"])


class TestConsultMocked:
    def test_consult_single_domain(self, consult_env, monkeypatch):
        memory = AsyncMock(return_value=[{"doc_id": "m1", "title": "T", "score": 0.9}])
        knowledge = AsyncMock(return_value=[])
        skill = AsyncMock(return_value=[])
        _patch_searches(monkeypatch, memory=memory, knowledge=knowledge, skill=skill)

        _, client = consult_env
        r = client.post(
            "/daemon/consult",
            json={"session_id": "s", "query": "x", "domains": ["memory"]},
        )
        assert r.status_code == 200

        data = r.json()
        assert {h["domain"] for h in data["hits"]} == {"memory"}
        memory.assert_awaited_once_with("x", "memory", top_k=5)
        knowledge.assert_not_awaited()
        skill.assert_not_awaited()

    def test_consult_limit(self, consult_env, monkeypatch):
        def _ten(prefix: str) -> list[dict]:
            return [
                {"doc_id": f"{prefix}{i}", "title": f"{prefix}{i}", "score": 0.9} for i in range(10)
            ]

        memory = AsyncMock(return_value=_ten("m"))
        knowledge = AsyncMock(return_value=_ten("d"))
        skill = AsyncMock(return_value=_ten("s"))
        _patch_searches(monkeypatch, memory=memory, knowledge=knowledge, skill=skill)

        _, client = consult_env
        r = client.post("/daemon/consult", json={"session_id": "s", "query": "x", "limit": 3})
        assert r.status_code == 200

        data = r.json()
        assert len(data["hits"]) <= 9
        for domain in ("memory", "knowledge", "skill"):
            assert sum(1 for h in data["hits"] if h["domain"] == domain) <= 3
        memory.assert_awaited_once_with("x", "memory", top_k=3)
        knowledge.assert_awaited_once_with("x", "design", top_k=3)
        skill.assert_awaited_once_with("x", "skill", top_k=3)

    def test_consult_result_sorting(self, consult_env, monkeypatch):
        memory = AsyncMock(
            return_value=[
                {"doc_id": "m1", "title": "low", "score": 0.4},
                {"doc_id": "m1", "title": "high", "score": 0.9},
                {"doc_id": "m2", "title": "other", "score": 0.6},
            ]
        )
        knowledge = AsyncMock(
            return_value=[
                {"doc_id": "d1", "section_id": "1", "score": 0.8},
                {"doc_id": "m1", "section_id": "2", "score": 0.5},
            ]
        )
        skill = AsyncMock(return_value=[{"doc_id": "s1", "name": "S", "score": 0.7}])
        _patch_searches(monkeypatch, memory=memory, knowledge=knowledge, skill=skill)

        _, client = consult_env
        r = client.post("/daemon/consult", json={"session_id": "s", "query": "x"})
        assert r.status_code == 200

        hits = r.json()["hits"]
        assert [h["score"] for h in hits] == [0.9, 0.8, 0.7, 0.6, 0.5]

        memory_m1 = [h for h in hits if h["domain"] == "memory" and h["doc_id"] == "m1"]
        assert len(memory_m1) == 1
        assert memory_m1[0]["score"] == 0.9

        # Cross-domain same doc_id is kept — dedup key includes domain.
        assert len([h for h in hits if h["doc_id"] == "m1"]) == 2

    def test_consult_llm_fallback(self, consult_env, monkeypatch):
        monkeypatch.delenv("DETECTOR_TEST_MODE", raising=False)
        monkeypatch.setattr(
            "bible_cc_plugin.daemon.detector.summarize_query",
            lambda turns, cfg: None,
        )
        memory = AsyncMock(return_value=[])
        knowledge = AsyncMock(return_value=[])
        skill = AsyncMock(return_value=[])
        _patch_searches(monkeypatch, memory=memory, knowledge=knowledge, skill=skill)

        server_mod, client = consult_env
        _seed_user_turn(server_mod, "s-fallback", "PostgreSQL auth decision")
        r = client.post(
            "/daemon/consult",
            json={"session_id": "s-fallback", "query": ""},
        )
        assert r.status_code == 200

        data = r.json()
        assert data["query_used"] == "PostgreSQL auth decision"
        memory.assert_awaited_once()
        knowledge.assert_awaited_once()
        skill.assert_awaited_once()

    def test_consult_slow_domain_times_out(self, consult_env, monkeypatch):
        import asyncio

        async def _slow(*args, **kwargs):
            await asyncio.sleep(0.5)
            return [{"doc_id": "slow", "title": "S", "score": 0.9}]

        memory = AsyncMock(side_effect=_slow)
        knowledge = AsyncMock(return_value=[{"doc_id": "d1", "section_id": "1", "score": 0.8}])
        skill = AsyncMock(return_value=[{"doc_id": "s1", "name": "N", "score": 0.7}])
        _patch_searches(monkeypatch, memory=memory, knowledge=knowledge, skill=skill)

        import bible_cc_plugin.daemon.server as server_mod

        monkeypatch.setattr(server_mod, "_CONSULT_SEARCH_TIMEOUT_S", 0.05)

        _, client = consult_env
        r = client.post("/daemon/consult", json={"session_id": "s", "query": "x"})
        assert r.status_code == 200

        data = r.json()
        assert {h["domain"] for h in data["hits"]} == {"knowledge", "skill"}

    def test_consult_parallel_isolation(self, consult_env, monkeypatch):
        from bible_cc_plugin.daemon.client import BibleUnreachableError

        memory = AsyncMock(side_effect=BibleUnreachableError("down"))
        knowledge = AsyncMock(
            return_value=[
                {
                    "doc_id": "d1",
                    "section_id": "1",
                    "section_title": "S",
                    "content": "C",
                    "score": 0.8,
                }
            ]
        )
        skill = AsyncMock(
            return_value=[{"doc_id": "s1", "name": "N", "description": "D", "score": 0.7}]
        )
        _patch_searches(monkeypatch, memory=memory, knowledge=knowledge, skill=skill)

        _, client = consult_env
        r = client.post("/daemon/consult", json={"session_id": "s", "query": "x"})
        assert r.status_code == 200

        data = r.json()
        assert {h["domain"] for h in data["hits"]} == {"knowledge", "skill"}
        assert data["context"].startswith("<relevant-memories>")
