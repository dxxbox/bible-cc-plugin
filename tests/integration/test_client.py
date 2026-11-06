"""Integration tests — BiBLEClient against a real BiBLE test-mode server."""

from __future__ import annotations

import json

import pytest

from bible_cc_plugin.config import BibleConfig
from bible_cc_plugin.daemon.client import (
    BiBLEClient,
    BiBLEError,
    BibleUnreachableError,
)

from .conftest import _find_free_port


def _config(url: str, *, token: str | None = None) -> BibleConfig:
    return BibleConfig(base_url=url, token=token, kb_index="bible-cc")


def _memory_files(memory_id: str = "it-memory-1") -> list[tuple[str, bytes, str]]:
    meta = {
        "memory_id": memory_id,
        "title": "Integration test memory",
        "abstract": "Testing the import contract end to end.",
        "overview": "Integration test overview.",
        "created_at": "2026-11-06T00:00:00Z",
        "updated_at": "2026-11-06T00:00:00Z",
        "source_client": "bible-cc",
        "language": "zh",
    }
    moments = {"moments": [], "session_id": "it-session"}
    return [
        ("meta.json", json.dumps(meta).encode(), "application/json"),
        ("moments.json", json.dumps(moments).encode(), "application/json"),
    ]


class TestClientHealth:
    @pytest.mark.asyncio
    async def test_health_check_returns_latency(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url)) as client:
            result = await client.check_health()
        assert result.reachable is True
        assert result.latency_ms is not None
        assert result.base_url == bible_test_url


class TestClientImport:
    @pytest.mark.asyncio
    async def test_import_memory_returns_task_id(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url)) as client:
            data = await client.import_memory(_memory_files(), tag="memory")
        assert data["task_id"]
        assert data["status"] in ("queued", "completed")


class TestClientSearch:
    @pytest.mark.asyncio
    async def test_search_memory_returns_results_key(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url)) as client:
            hits = await client.search_memory("postgres", tag="memory")
        assert isinstance(hits, list)

    @pytest.mark.asyncio
    async def test_search_knowledge_base_fixture_hit(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url)) as client:
            hits = await client.search_knowledge_base("design", tag="design")
        assert hits and hits[0]["section_title"]

    @pytest.mark.asyncio
    async def test_search_skill_fixture_hit(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url)) as client:
            hits = await client.search_skill("skill-standard", tag="skill")
        assert hits and hits[0]["name"] == "skill-standard"


class TestClientDownload:
    @pytest.mark.asyncio
    async def test_memory_download_returns_task_id(self, bible_test_url):
        """Test mode serves a builtin download task for MEMORY."""
        async with BiBLEClient(_config(bible_test_url)) as client:
            data = await client.request_memory_download("memory/missing.json")
        assert data["task_id"]

    @pytest.mark.asyncio
    async def test_skill_download_missing_returns_structured_error(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url)) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.request_skill_download("skill/missing.skill")
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "SKILL_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_download_artifact_missing_returns_error(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url)) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.get_download_artifact("memory", "missing-artifact")
        assert exc_info.value.status_code in (404, 410)


class TestClientTaskStatus:
    @pytest.mark.asyncio
    async def test_get_task_status_polls(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url)) as client:
            imported = await client.import_memory(_memory_files(), tag="memory")
            status = await client.get_task_status(imported["task_id"])
        assert status["task_id"] == imported["task_id"]
        assert status["status"] in ("queued", "running", "completed")


class TestClientFailurePaths:
    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        dead_url = f"http://127.0.0.1:{_find_free_port()}"
        async with BiBLEClient(_config(dead_url)) as client:
            with pytest.raises(BibleUnreachableError):
                await client.search_memory("x")

    @pytest.mark.asyncio
    async def test_auth_token_request_succeeds(self, bible_test_url):
        async with BiBLEClient(_config(bible_test_url, token="dummy")) as client:
            result = await client.check_health()
        assert result.reachable is True
