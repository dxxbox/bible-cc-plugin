"""Contract tests — BiBLE V4 API response schemas (against test-mode server)."""

from __future__ import annotations

import json

import httpx
import pytest


@pytest.fixture
def api(bible_test_url):
    with httpx.Client(trust_env=False, base_url=bible_test_url, timeout=10) as client:
        yield client


def _memory_files(
    memory_id: str = "contract-memory-1",
) -> list[tuple[str, tuple[str, bytes, str]]]:
    meta = {
        "memory_id": memory_id,
        "title": "Contract test memory",
        "abstract": "Schema contract for import.",
        "overview": "Contract overview.",
        "created_at": "2026-11-06T00:00:00Z",
        "updated_at": "2026-11-06T00:00:00Z",
        "source_client": "bible-cc",
        "language": "zh",
    }
    moments = {"moments": [], "session_id": "contract-session"}
    return [
        ("files", ("meta.json", json.dumps(meta).encode(), "application/json")),
        ("files", ("moments.json", json.dumps(moments).encode(), "application/json")),
    ]


class TestBibleApiContract:
    def test_health_check_schema(self, api):
        r = api.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"

    def test_import_memory_schema(self, api):
        r = api.post(
            "/api/import/memory",
            data={"kb_index": "bible-cc", "tag": "memory"},
            files=_memory_files(),
        )
        assert r.status_code == 202
        body = r.json()
        assert isinstance(body["task_id"], str)
        assert body.get("status") in ("queued", "completed")

    def test_search_memory_schema(self, api):
        r = api.post("/api/search/memory", json={"query": "x", "tag": "memory"})
        assert r.status_code == 200
        body = r.json()
        assert "results" in body
        assert isinstance(body["results"]["memory"], list)

    def test_search_knowledge_base_schema(self, api):
        r = api.post("/api/search/knowledge-base", json={"query": "design", "tag": "design"})
        assert r.status_code == 200
        hits = r.json()["results"]["knowledge_base"]
        assert hits
        first = hits[0]
        for key in ("doc_id", "section_title", "score", "content"):
            assert key in first

    def test_search_skill_schema(self, api):
        r = api.post("/api/search/skill", json={"query": "skill-standard", "tag": "skill"})
        assert r.status_code == 200
        hits = r.json()["results"]["skill"]
        assert hits
        assert hits[0]["name"] == "skill-standard"
        assert "related_storage_paths" in hits[0]

    def test_download_file_schema(self, api):
        r = api.post(
            "/api/download/memory/file",
            json={"storage_path": "memory/x.json", "tag": "memory"},
        )
        assert r.status_code == 202
        body = r.json()
        assert isinstance(body["task_id"], str)

    def test_download_artifact_schema(self, api):
        r = api.get("/api/download/memory/artifact/missing")
        assert r.status_code in (404, 410)
        body = r.json()
        assert body["code"]
        assert body["message"]

    def test_error_400_schema(self, api):
        r = api.post("/api/search/memory", json={"query": "", "tag": "memory"})
        assert r.status_code == 400
        body = r.json()
        assert body["code"] == "INVALID_ARGUMENT"
        assert body["message"]

    def test_get_task_status_schema(self, api):
        imported = api.post(
            "/api/import/memory",
            data={"kb_index": "bible-cc", "tag": "memory"},
            files=_memory_files(),
        ).json()
        r = api.get(f"/api/control/admin/tasks/{imported['task_id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == imported["task_id"]
        assert body["status"] in ("queued", "running", "completed", "failed")

    def test_search_hit_structure_invariant(self, api):
        """All domain hits share doc_id/section_title/score/content."""
        kb = api.post(
            "/api/search/knowledge-base", json={"query": "design", "tag": "design"}
        ).json()["results"]["knowledge_base"]
        for hit in kb:
            for key in ("doc_id", "section_title", "score", "content"):
                assert key in hit
