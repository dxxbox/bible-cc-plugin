"""Unit tests for BiBLEClient — mock all BiBLE API responses via pytest-httpx."""

from __future__ import annotations

import json

import pytest

from bible_cc_plugin.config import BibleConfig
from bible_cc_plugin.daemon.client import (
    BiBLEClient,
    BiBLEError,
    BibleUnreachableError,
    HealthResult,
    _parse_json,
    _safe_json,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bible_config(**overrides) -> BibleConfig:
    kwargs = {"base_url": "http://localhost:5555", "token": None, "kb_index": "bible-cc"}
    kwargs.update(overrides)
    return BibleConfig(**kwargs)


def _connect_error():
    """Create a ConnectError without needing a real network call."""
    import httpx as _httpx

    try:
        raise _httpx.ConnectError("connection refused")
    except _httpx.ConnectError as exc:
        return exc


# ---------------------------------------------------------------------------
# Test _safe_json (error-path tolerant parsing)
# ---------------------------------------------------------------------------


class TestSafeJson:
    def test_valid_json(self):
        import httpx

        resp = httpx.Response(200, json={"ok": True})
        assert _safe_json(resp) == {"ok": True}

    def test_empty_body(self):
        import httpx

        resp = httpx.Response(200, content=b"")
        assert _safe_json(resp) == {}

    def test_invalid_json(self):
        import httpx

        resp = httpx.Response(200, content=b"not json")
        assert _safe_json(resp) == {}


# ---------------------------------------------------------------------------
# Test _parse_json (success-path strict parsing)
# ---------------------------------------------------------------------------


class TestParseJson:
    def test_valid_json(self):
        import httpx

        resp = httpx.Response(200, json={"ok": True})
        assert _parse_json(resp) == {"ok": True}

    def test_empty_body_raises(self):
        import httpx

        resp = httpx.Response(200, content=b"")
        with pytest.raises(BiBLEError) as exc_info:
            _parse_json(resp)
        assert exc_info.value.code == "INVALID_RESPONSE"

    def test_invalid_json_raises(self):
        import httpx

        resp = httpx.Response(200, content=b"not json")
        with pytest.raises(BiBLEError) as exc_info:
            _parse_json(resp)
        assert exc_info.value.code == "INVALID_RESPONSE"


# ---------------------------------------------------------------------------
# Test BiBLEError
# ---------------------------------------------------------------------------


class TestBiBLEErrorType:
    def test_code_and_message(self):
        err = BiBLEError(code="AUTH_FAILED", message="Invalid token", status_code=401)
        assert err.code == "AUTH_FAILED"
        assert err.message == "Invalid token"
        assert err.status_code == 401

    def test_str_representation(self):
        err = BiBLEError(code="NOT_FOUND", message="Session not found", status_code=404)
        assert str(err) == "[NOT_FOUND] Session not found"


# ---------------------------------------------------------------------------
# Test Health
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_returns_reachable(self, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/health",
            json={"status": "ok"},
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.check_health()
        assert isinstance(result, HealthResult)
        assert result.reachable is True

    @pytest.mark.asyncio
    async def test_unhealthy_status_returns_unreachable(self, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/health",
            json={"status": "error"},
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.check_health()
        assert result.reachable is False

    @pytest.mark.asyncio
    async def test_5xx_returns_unreachable_not_raise(self, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/health",
            status_code=500,
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.check_health()
        assert result.reachable is False

    @pytest.mark.asyncio
    async def test_connection_error_returns_unreachable(self, httpx_mock):
        httpx_mock.add_exception(
            _connect_error(),
            method="GET",
            url="http://localhost:5555/health",
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.check_health()
        assert result.reachable is False

    @pytest.mark.asyncio
    async def test_non_json_2xx_returns_unreachable(self, httpx_mock):
        """2xx health with non-JSON body → reachable=False, no crash."""
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/health",
            content=b"not json",
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.check_health()
        assert result.reachable is False


# ---------------------------------------------------------------------------
# Test Import
# ---------------------------------------------------------------------------


class TestImportMemory:
    @pytest.mark.asyncio
    async def test_success_returns_task_id(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            json={"success": True, "task_id": "task-001", "status": "queued"},
            status_code=202,
        )
        files = [("moment.json", b'{"title":"test"}', "application/json")]
        async with BiBLEClient(_bible_config()) as client:
            result = await client.import_memory(files)
        assert result["task_id"] == "task-001"
        assert result["status"] == "queued"

    @pytest.mark.asyncio
    async def test_uses_config_kb_index_when_not_specified(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            json={"success": True, "task_id": "task-002", "status": "queued"},
            status_code=202,
        )
        files = [("moment.json", b'{"title":"test2"}', "application/json")]
        async with BiBLEClient(_bible_config(kb_index="my-index")) as client:
            result = await client.import_memory(files)
        assert result["task_id"] == "task-002"

    @pytest.mark.asyncio
    async def test_explicit_kb_index_overrides_config(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            json={"task_id": "task-003"},
            status_code=202,
        )
        async with BiBLEClient(_bible_config(kb_index="config-index")) as client:
            result = await client.import_memory(
                [("m.json", b"{}", "application/json")], kb_index="explicit-index",
            )
        assert result["task_id"] == "task-003"

    @pytest.mark.asyncio
    async def test_empty_files_raises_bible_error(self, httpx_mock):
        """Empty files list → BiBLEError, consistent with caller catch pattern."""
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError, match="at least one file"):
                await client.import_memory([])

    @pytest.mark.asyncio
    async def test_4xx_raises_bible_error(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            json={"error": {"code": "BAD_REQUEST", "message": "Missing kb_index"}},
            status_code=400,
        )
        files = [("moment.json", b"{}", "application/json")]
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.import_memory(files)
            assert exc_info.value.code == "BAD_REQUEST"

    @pytest.mark.asyncio
    async def test_non_json_2xx_raises(self, httpx_mock):
        """2xx with non-JSON body raises BiBLEError, not silently empty."""
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            content=b"not json",
            status_code=202,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.import_memory(
                    [("m.json", b"{}", "application/json")],
                )
            assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_missing_task_id_raises(self, httpx_mock):
        """Valid JSON without task_id raises BiBLEError."""
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            json={"success": True},
            status_code=202,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.import_memory(
                    [("m.json", b"{}", "application/json")],
                )
            assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_optional_import_fields_sent(self, httpx_mock):
        """parser_script/vector_model/parser_context are sent when non-None."""
        body_sent: bytes = b""

        def capture(request):
            import httpx

            nonlocal body_sent
            body_sent = request.content
            return httpx.Response(202, json={"task_id": "task-opt"})

        httpx_mock.add_callback(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            callback=capture,
        )
        async with BiBLEClient(_bible_config()) as client:
            await client.import_memory(
                [("m.json", b"{}", "application/json")],
                parser_script=("parse_memory.py", b"def parse(): pass\n", "text/x-python"),
                vector_model="text-embedding-3",
                parser_context='{"chunk_size": 512}',
            )
        # parser_script is sent as its own multipart field, not files[]
        body_str = body_sent.decode("utf-8", errors="replace")
        assert 'name="parser_script"' in body_str
        assert "def parse" in body_str  # parser_script file content
        assert "text-embedding-3" in body_str
        assert "chunk_size" in body_str

    @pytest.mark.asyncio
    async def test_optional_import_fields_omitted_when_none(self, httpx_mock):
        """parser_script/vector_model/parser_context are not in body when None."""
        body_sent: bytes = b""

        def capture(request):
            import httpx

            nonlocal body_sent
            body_sent = request.content
            return httpx.Response(202, json={"task_id": "task-none"})

        httpx_mock.add_callback(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            callback=capture,
        )
        async with BiBLEClient(_bible_config()) as client:
            await client.import_memory(
                [("m.json", b"{}", "application/json")],
            )
        body_str = body_sent.decode("utf-8", errors="replace")
        assert "parser_script" not in body_str
        assert "vector_model" not in body_str
        assert "parser_context" not in body_str


# ---------------------------------------------------------------------------
# Test Search
# ---------------------------------------------------------------------------


class TestSearchMemory:
    @pytest.mark.asyncio
    async def test_returns_results_list(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            json={
                "success": True,
                "domain": "MEMORY",
                "total": 2,
                "results": {
                    "memory": [
                        {"doc_id": "1", "section_title": "Postgres decision", "score": 0.9},
                        {"doc_id": "2", "section_title": "Redis cache", "score": 0.7},
                    ]
                },
            },
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            results = await client.search_memory("PostgreSQL")
        assert len(results) == 2
        assert results[0]["doc_id"] == "1"

    @pytest.mark.asyncio
    async def test_empty_results(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            json={"success": True, "domain": "MEMORY", "total": 0, "results": {"memory": []}},
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            results = await client.search_memory("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_passes_optional_params(self, httpx_mock):
        body_sent: dict = {}

        def capture(request):
            import httpx

            nonlocal body_sent
            body_sent = json.loads(request.content)
            return httpx.Response(200, json={"total": 0, "results": {"memory": []}})

        httpx_mock.add_callback(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            callback=capture,
        )
        async with BiBLEClient(_bible_config()) as client:
            await client.search_memory(
                "query", top_k=5, search_type="hybrid", kb_index="kb-x",
            )
        assert body_sent["top_k"] == 5
        assert body_sent["search_type"] == "hybrid"
        assert body_sent["kb_index"] == "kb-x"

    @pytest.mark.asyncio
    async def test_non_json_2xx_raises(self, httpx_mock):
        """2xx with non-JSON body raises BiBLEError, not silently []."""
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            content=b"not json",
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.search_memory("query")
            assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_missing_results_key_raises(self, httpx_mock):
        """Valid JSON without 'results' key raises BiBLEError."""
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            json={"success": True, "total": 0},
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.search_memory("query")
            assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_wrong_domain_key_raises(self, httpx_mock):
        """Valid JSON with results but missing memory key raises BiBLEError."""
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            json={"success": True, "total": 0, "results": {"knowledge_base": []}},
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.search_memory("query")
            assert exc_info.value.code == "INVALID_RESPONSE"


class TestSearchKnowledgeBase:
    @pytest.mark.asyncio
    async def test_returns_kb_results(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/knowledge-base",
            json={
                "success": True,
                "domain": "KNOWLEDGE_BASE",
                "total": 1,
                "results": {
                    "knowledge_base": [
                        {"doc_id": "kb1", "section_title": "Design doc", "score": 0.85}
                    ]
                },
            },
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            results = await client.search_knowledge_base("architecture", tag="design")
        assert len(results) == 1
        assert results[0]["doc_id"] == "kb1"


class TestSearchSkill:
    @pytest.mark.asyncio
    async def test_returns_skill_results(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/skill",
            json={
                "success": True,
                "domain": "SKILL",
                "total": 1,
                "results": {
                    "skill": [
                        {"doc_id": "s1", "section_title": "TDD skill", "score": 0.95}
                    ]
                },
            },
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            results = await client.search_skill("testing")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_default_tag_is_skill(self, httpx_mock):
        body_sent: dict = {}

        def capture(request):
            import httpx

            nonlocal body_sent
            body_sent = json.loads(request.content)
            return httpx.Response(200, json={"total": 0, "results": {"skill": []}})

        httpx_mock.add_callback(
            method="POST", url="http://localhost:5555/api/search/skill", callback=capture,
        )
        async with BiBLEClient(_bible_config()) as client:
            await client.search_skill("query")
        assert body_sent["tag"] == "skill"


# ---------------------------------------------------------------------------
# Test Download Submit
# ---------------------------------------------------------------------------


class TestDownloadSubmit:
    @pytest.mark.asyncio
    async def test_memory_download_returns_task_id(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/download/memory/file",
            json={"success": True, "task_id": "dl-task-1"},
            status_code=202,
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.request_memory_download("/memories/doc1.json")
        assert result["task_id"] == "dl-task-1"

    @pytest.mark.asyncio
    async def test_skill_download_returns_task_id(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/download/skill/file",
            json={"success": True, "task_id": "dl-task-2"},
            status_code=202,
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.request_skill_download("/skills/s1.json")
        assert result["task_id"] == "dl-task-2"

    @pytest.mark.asyncio
    async def test_download_name_optional_passed(self, httpx_mock):
        body_sent: dict = {}

        def capture(request):
            import httpx

            nonlocal body_sent
            body_sent = json.loads(request.content)
            return httpx.Response(202, json={"task_id": "dl-task-3"})

        httpx_mock.add_callback(
            method="POST",
            url="http://localhost:5555/api/download/memory/file",
            callback=capture,
        )
        async with BiBLEClient(_bible_config()) as client:
            await client.request_memory_download(
                "/memories/doc1.json", download_name="my-memory.json",
            )
        assert body_sent["download_name"] == "my-memory.json"

    @pytest.mark.asyncio
    async def test_download_name_omitted_when_none(self, httpx_mock):
        body_sent: dict = {}

        def capture(request):
            import httpx

            nonlocal body_sent
            body_sent = json.loads(request.content)
            return httpx.Response(202, json={"task_id": "dl-task-4"})

        httpx_mock.add_callback(
            method="POST",
            url="http://localhost:5555/api/download/memory/file",
            callback=capture,
        )
        async with BiBLEClient(_bible_config()) as client:
            await client.request_memory_download("/memories/doc1.json")
        assert "download_name" not in body_sent

    @pytest.mark.asyncio
    async def test_non_json_2xx_raises(self, httpx_mock):
        """2xx with non-JSON body raises BiBLEError, not silently {}."""
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/download/memory/file",
            content=b"not json",
            status_code=202,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.request_memory_download("/memories/doc1.json")
            assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_missing_task_id_raises(self, httpx_mock):
        """Valid JSON without task_id raises BiBLEError."""
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/download/memory/file",
            json={"success": True},
            status_code=202,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.request_memory_download("/memories/doc1.json")
            assert exc_info.value.code == "INVALID_RESPONSE"


# ---------------------------------------------------------------------------
# Test Task Status
# ---------------------------------------------------------------------------


class TestTaskStatus:
    @pytest.mark.asyncio
    async def test_returns_task_record(self, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/task-123",
            json={
                "task_id": "task-123",
                "status": "completed",
                "artifact_id": "art-456",
            },
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.get_task_status("task-123")
        assert result["status"] == "completed"
        assert result["artifact_id"] == "art-456"

    @pytest.mark.asyncio
    async def test_queued_status(self, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/task-queued",
            json={"task_id": "task-queued", "status": "queued"},
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            result = await client.get_task_status("task-queued")
        assert result["status"] == "queued"

    @pytest.mark.asyncio
    async def test_non_json_2xx_raises(self, httpx_mock):
        """2xx with non-JSON body raises BiBLEError, not silently {}."""
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/task-x",
            content=b"not json",
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.get_task_status("task-x")
            assert exc_info.value.code == "INVALID_RESPONSE"

    @pytest.mark.asyncio
    async def test_missing_status_raises(self, httpx_mock):
        """Valid JSON without 'status' field raises BiBLEError."""
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/task-x",
            json={"task_id": "task-x"},
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.get_task_status("task-x")
            assert exc_info.value.code == "INVALID_RESPONSE"


# ---------------------------------------------------------------------------
# Test Download Artifact
# ---------------------------------------------------------------------------


class TestDownloadArtifact:
    @pytest.mark.asyncio
    async def test_returns_bytes(self, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/download/memory/artifact/art-1",
            content=b'{"title":"Test Memory"}',
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            content = await client.get_download_artifact("memory", "art-1")
        assert isinstance(content, bytes)
        assert b"Test Memory" in content

    @pytest.mark.asyncio
    async def test_large_file(self, httpx_mock):
        large = b"x" * 1_000_000
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/download/skill/artifact/art-large",
            content=large,
            status_code=200,
        )
        async with BiBLEClient(_bible_config()) as client:
            content = await client.get_download_artifact("skill", "art-large")
        assert len(content) == 1_000_000


# ---------------------------------------------------------------------------
# Test Error Handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_400_raises_bible_error(self, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/bad",
            json={"error": {"code": "NOT_FOUND", "message": "Task not found"}},
            status_code=404,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.get_task_status("bad")
            assert exc_info.value.code == "NOT_FOUND"
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_401_raises_bible_error(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            json={"error": {"code": "UNAUTHORIZED", "message": "Invalid token"}},
            status_code=401,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.search_memory("query")
            assert exc_info.value.code == "UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_500_raises_unreachable_error(self, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/t",
            status_code=500,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BibleUnreachableError):
                await client.get_task_status("t")

    @pytest.mark.asyncio
    async def test_503_raises_unreachable_error(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            status_code=503,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BibleUnreachableError):
                await client.search_memory("query")

    @pytest.mark.asyncio
    async def test_timeout_raises_unreachable(self, httpx_mock):
        import httpx as _httpx

        httpx_mock.add_exception(
            _httpx.ReadTimeout("read timeout"),
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/t",
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BibleUnreachableError) as exc_info:
                await client.get_task_status("t")
            assert "Timeout" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connect_error_raises_unreachable(self, httpx_mock):
        httpx_mock.add_exception(
            _connect_error(),
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/t",
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BibleUnreachableError):
                await client.get_task_status("t")

    @pytest.mark.asyncio
    async def test_remote_protocol_error_raises_unreachable(self, httpx_mock):
        """RemoteProtocolError → BibleUnreachableError (previously leaked)."""
        import httpx as _httpx

        httpx_mock.add_exception(
            _httpx.RemoteProtocolError("peer closed connection"),
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/t",
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BibleUnreachableError) as exc_info:
                await client.get_task_status("t")
            assert "RemoteProtocolError" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_pool_timeout_raises_unreachable(self, httpx_mock):
        """PoolTimeout → BibleUnreachableError (previously leaked)."""
        import httpx as _httpx

        httpx_mock.add_exception(
            _httpx.PoolTimeout("pool timeout"),
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/t",
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BibleUnreachableError):
                await client.get_task_status("t")

    @pytest.mark.asyncio
    async def test_non_json_error_body(self, httpx_mock):
        """4xx with non-JSON body should not crash — falls back to raw text."""
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            content=b"Internal Server Error",
            status_code=400,
        )
        async with BiBLEClient(_bible_config()) as client:
            with pytest.raises(BiBLEError) as exc_info:
                await client.search_memory("query")
            assert exc_info.value.code == "UNKNOWN"


# ---------------------------------------------------------------------------
# Test Auth
# ---------------------------------------------------------------------------


class TestAuth:
    @pytest.mark.asyncio
    async def test_token_sends_bearer_header(self, httpx_mock):
        headers_sent: dict = {}

        def capture(request):
            import httpx

            nonlocal headers_sent
            headers_sent = dict(request.headers)
            return httpx.Response(200, json={"status": "ok", "total": 0, "results": {"memory": []}})

        httpx_mock.add_callback(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            callback=capture,
        )
        async with BiBLEClient(_bible_config(token="secret-token")) as client:
            await client.search_memory("query")
        assert headers_sent.get("authorization") == "Bearer secret-token"

    @pytest.mark.asyncio
    async def test_no_token_does_not_send_authorization(self, httpx_mock):
        headers_sent: dict = {}

        def capture(request):
            import httpx

            nonlocal headers_sent
            headers_sent = dict(request.headers)
            return httpx.Response(200, json={"status": "ok", "total": 0, "results": {"memory": []}})

        httpx_mock.add_callback(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            callback=capture,
        )
        async with BiBLEClient(_bible_config(token=None)) as client:
            await client.search_memory("query")
        assert "authorization" not in {k.lower() for k in headers_sent}

    @pytest.mark.asyncio
    async def test_empty_token_does_not_send_authorization(self, httpx_mock):
        headers_sent: dict = {}

        def capture(request):
            import httpx

            nonlocal headers_sent
            headers_sent = dict(request.headers)
            return httpx.Response(200, json={"status": "ok", "total": 0, "results": {"memory": []}})

        httpx_mock.add_callback(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            callback=capture,
        )
        async with BiBLEClient(_bible_config(token="")) as client:
            await client.search_memory("query")
        assert "authorization" not in {k.lower() for k in headers_sent}


# ---------------------------------------------------------------------------
# Test Client Lifecycle
# ---------------------------------------------------------------------------


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self):
        async with BiBLEClient(_bible_config()) as client:
            assert client._client is None  # lazy — not created yet
            _ = client._http  # trigger lazy creation
            assert client._client is not None
        # After __aexit__, client should be closed
        assert client._client is None

    @pytest.mark.asyncio
    async def test_explicit_aclose(self):
        client = BiBLEClient(_bible_config())
        _ = client._http
        assert client._client is not None
        await client.aclose()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_repeated_aclose_is_safe(self):
        client = BiBLEClient(_bible_config())
        await client.aclose()
        await client.aclose()  # should not raise
        assert client._client is None

    def test_constructor_accepts_bible_config(self):
        config = _bible_config(base_url="http://example.com:8080", token="tok", kb_index="kb")
        client = BiBLEClient(config)
        assert client._base_url == "http://example.com:8080"
        assert client._token == "tok"
        assert client._kb_index == "kb"

    def test_trailing_slash_stripped(self):
        """_strip_trailing_slash works directly (BibleConfig validator also
        handles this, but the client keeps it as defense-in-depth)."""
        from bible_cc_plugin.daemon.client import _strip_trailing_slash

        assert _strip_trailing_slash("http://example.com/") == "http://example.com"
        assert _strip_trailing_slash("http://example.com") == "http://example.com"
        assert _strip_trailing_slash("http://example.com/path/") == "http://example.com/path"
