"""Unit tests for MCP server — tool schemas + Phase 3b real client calls.

All tests [Unit] [Pre] — mock BiBLE via pytest-httpx.
"""

from __future__ import annotations

import json

import pytest


def _tool_names_from_server():
    from bible_cc_plugin.mcp.server import TOOLS

    return {t["name"] for t in TOOLS}


async def _invoke_tool(name: str, arguments: dict) -> dict:
    from bible_cc_plugin.mcp.server import _handle_tool

    result = await _handle_tool(name, arguments)
    text = result[0].text
    return json.loads(text)


class TestMCPToolRegistration:
    """All 8 tools registered with correct inputSchema."""

    def test_all_8_tools_registered(self):
        names = _tool_names_from_server()
        assert len(names) == 8, f"expected 8 tools, got {len(names)}: {names}"

    def test_active_tools_have_input_schema(self):
        from bible_cc_plugin.mcp.server import TOOLS

        postponed = {"bible_memory_delete", "bible_knowledge_list"}
        for t in TOOLS:
            if t["name"] in postponed:
                continue
            schema = t.get("inputSchema", {})
            assert "properties" in schema, f"{t['name']} missing inputSchema.properties"
            props = schema.get("properties", {})
            assert len(props) >= 1, f"{t['name']} has no properties"

    def test_postponed_tools_have_unavailable_description(self):
        from bible_cc_plugin.mcp.server import TOOLS

        postponed = [
            t for t in TOOLS
            if t["name"] in {"bible_memory_delete", "bible_knowledge_list"}
        ]
        for t in postponed:
            assert "not yet available" in t["description"].lower()


class TestMCPDegradation:
    """Degradation paths: unreachable, postponed, unconfigured."""

    @pytest.mark.asyncio
    async def test_search_success(self, httpx_mock, monkeypatch):
        """bible_memory_search returns results via real client."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            json={"total": 1, "results": {"memory": [{"doc_id": "1", "score": 0.9}]}},
            status_code=200,
        )
        result = await _invoke_tool("bible_memory_search", {"query": "test"})
        assert result["total"] == 1
        assert len(result["results"]) == 1

    @pytest.mark.asyncio
    async def test_unreachable_returns_structured_error(self, httpx_mock, monkeypatch):
        """BiBLE 500 → structured error with suggestion."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            status_code=500,
        )
        result = await _invoke_tool("bible_memory_search", {"query": "test"})
        assert "error" in result
        assert "unreachable" in result.get("error", "").lower()
        assert "suggestion" in result

    @pytest.mark.asyncio
    async def test_postponed_tool_returns_not_available(self):
        result = await _invoke_tool("bible_memory_delete", {"storage_path": "/x"})
        assert "not yet available" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_memory_save_guard_clause(self, monkeypatch):
        """Empty messages → error without calling client."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        result = await _invoke_tool("bible_memory_save", {"messages": []})
        assert "No messages" in result.get("error", "")


class TestMCPLogging:
    """F4.6: MCP call tracing."""

    @pytest.mark.asyncio
    async def test_search_logs_result_summary(self, httpx_mock, monkeypatch):
        """Search tool returns structured result."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/memory",
            json={"total": 0, "results": {"memory": []}},
            status_code=200,
        )
        result = await _invoke_tool("bible_memory_search", {"query": "auth"})
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_postponed_tool_returns_not_available(self):
        """Postponed tool returns not-yet-available error."""
        result = await _invoke_tool("bible_memory_delete", {"storage_path": "/x"})
        assert "not yet available" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_unconfigured_tool_returns_setup_guidance(self):
        """BIBLE_ATLAS_BASE_URL not set → setup suggestion."""
        import os

        old = os.environ.pop("BIBLE_ATLAS_BASE_URL", None)
        try:
            result = await _invoke_tool("bible_memory_search", {"query": "test"})
            assert "not configured" in result.get("error", "").lower()
        finally:
            if old:
                os.environ["BIBLE_ATLAS_BASE_URL"] = old


class TestMCPServerStartup:
    """Server starts without BIBLE_ATLAS_BASE_URL."""

    def test_server_module_imports_without_env(self, monkeypatch):
        """Server module imports cleanly without BIBLE_ATLAS_BASE_URL."""
        monkeypatch.delenv("BIBLE_ATLAS_BASE_URL", raising=False)
        import bible_cc_plugin.mcp.server as mcp_mod

        assert mcp_mod.TOOLS is not None
        assert len(mcp_mod.TOOLS) == 8

    @pytest.mark.asyncio
    async def test_tool_handler_detects_missing_base_url(self):
        import os

        old = os.environ.pop("BIBLE_ATLAS_BASE_URL", None)
        try:
            result = await _invoke_tool("bible_memory_search", {"query": "test"})
            assert "not configured" in result.get("error", "").lower()
        finally:
            if old:
                os.environ["BIBLE_ATLAS_BASE_URL"] = old


# ---------------------------------------------------------------------------
# Phase 3b — Full tool coverage
# ---------------------------------------------------------------------------


class TestMCPSave:
    @pytest.mark.asyncio
    async def test_memory_save_success(self, httpx_mock, monkeypatch):
        """Multipart import → returns task_id."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/import/memory",
            json={"task_id": "task-save-1", "status": "queued"},
            status_code=202,
        )
        result = await _invoke_tool("bible_memory_save", {
            "messages": [{"role": "user", "content": "hello"}],
            "title": "Test",
        })
        assert result["task_id"] == "task-save-1"
        assert result["status"] == "queued"


class TestMCPSearch:
    @pytest.mark.asyncio
    async def test_knowledge_search_success(self, httpx_mock, monkeypatch):
        """Returns KB results via client."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/knowledge-base",
            json={"total": 1, "results": {"knowledge_base": [{"doc_id": "kb1"}]}},
            status_code=200,
        )
        result = await _invoke_tool("bible_knowledge_search", {
            "query": "design", "tag": "design",
        })
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_skill_search_success(self, httpx_mock, monkeypatch):
        """Returns skill results via client."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/search/skill",
            json={"total": 1, "results": {"skill": [{"doc_id": "s1"}]}},
            status_code=200,
        )
        result = await _invoke_tool("bible_skill_search", {"query": "tdd"})
        assert result["total"] == 1


class TestMCPDownload:
    """3-step async download: submit → poll → artifact."""

    @pytest.mark.asyncio
    async def test_memory_get_success(self, httpx_mock, monkeypatch):
        """Submit → poll completed → artifact returned."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/download/memory/file",
            json={"task_id": "dl-1"},
            status_code=202,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/dl-1",
            json={"status": "completed", "result": {"artifact_id": "art-1"}},
            status_code=200,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/download/memory/artifact/art-1",
            content=b'{"title":"Test Memory"}',
            status_code=200,
        )
        result = await _invoke_tool("bible_memory_get", {"storage_path": "/m/doc.json"})
        assert "Test Memory" in result.get("content", "")

    @pytest.mark.asyncio
    async def test_memory_get_poll_failed(self, httpx_mock, monkeypatch):
        """Task failed → structured error with task_id."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/download/memory/file",
            json={"task_id": "dl-fail"},
            status_code=202,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/dl-fail",
            json={"status": "failed", "result": {"error": "parse error"}},
            status_code=200,
        )
        result = await _invoke_tool("bible_memory_get", {"storage_path": "/m/bad.json"})
        assert "failed" in result.get("error", "").lower()
        assert result.get("task_id") == "dl-fail"

    @pytest.mark.asyncio
    async def test_skill_get_success(self, httpx_mock, monkeypatch):
        """Skill download: submit → poll completed → artifact."""
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555")
        httpx_mock.add_response(
            method="POST",
            url="http://localhost:5555/api/download/skill/file",
            json={"task_id": "dl-skill"},
            status_code=202,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/control/admin/tasks/dl-skill",
            json={"status": "completed", "result": {"artifact_id": "art-skill"}},
            status_code=200,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:5555/api/download/skill/artifact/art-skill",
            content=b"# TDD Skill\n\ntest first",
            status_code=200,
        )
        result = await _invoke_tool("bible_skill_get", {"storage_path": "/s/tdd.skill"})
        assert "test first" in result.get("content", "")


class TestMCPPostponed:
    """Postponed tools unchanged after Phase 3b."""

    @pytest.mark.asyncio
    async def test_memory_delete_unchanged(self):
        result = await _invoke_tool("bible_memory_delete", {"storage_path": "/x"})
        assert "not yet available" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_knowledge_list_unchanged(self):
        result = await _invoke_tool("bible_knowledge_list", {"tag": "design"})
        assert "not yet available" in result.get("error", "").lower()
