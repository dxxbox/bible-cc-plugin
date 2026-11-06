"""Integration tests — MCP tool handlers against a real test-mode server."""

from __future__ import annotations

import json

import pytest

from .conftest import _find_free_port


def _payload(content: list) -> dict:
    return json.loads(content[0].text)


@pytest.fixture
def mcp_env(monkeypatch, bible_test_url):
    monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", bible_test_url)
    monkeypatch.setenv("BIBLE_CC_KB_INDEX", "bible-cc")
    monkeypatch.delenv("BIBLE_ATLAS_TOKEN", raising=False)
    return bible_test_url


class TestMcpTools:
    @pytest.mark.asyncio
    async def test_memory_search(self, mcp_env):
        from bible_cc_plugin.mcp.server import _handle_tool

        data = _payload(await _handle_tool("bible_memory_search", {"query": "postgres"}))
        assert isinstance(data["results"], list)
        assert data["total"] == len(data["results"])

    @pytest.mark.asyncio
    async def test_memory_save(self, mcp_env):
        from bible_cc_plugin.mcp.server import _handle_tool

        data = _payload(
            await _handle_tool(
                "bible_memory_save",
                {
                    "messages": [{"role": "user", "content": "remember this"}],
                    "title": "Integration save",
                    "abstract": "Saved by integration test",
                },
            )
        )
        assert data.get("task_id"), f"memory_save failed: {data}"

    @pytest.mark.asyncio
    async def test_memory_get_returns_structured_result_or_error(self, mcp_env):
        from bible_cc_plugin.mcp.server import _handle_tool

        data = _payload(await _handle_tool("bible_memory_get", {"storage_path": "memory/x.json"}))
        assert "task_id" in data or "error" in data

    @pytest.mark.asyncio
    async def test_knowledge_search(self, mcp_env):
        from bible_cc_plugin.mcp.server import _handle_tool

        data = _payload(
            await _handle_tool("bible_knowledge_search", {"query": "design", "tag": "design"})
        )
        assert data["results"] and data["results"][0]["section_title"]

    @pytest.mark.asyncio
    async def test_skill_search(self, mcp_env):
        from bible_cc_plugin.mcp.server import _handle_tool

        data = _payload(await _handle_tool("bible_skill_search", {"query": "skill-standard"}))
        assert data["results"] and data["results"][0]["name"] == "skill-standard"

    @pytest.mark.asyncio
    async def test_skill_get_missing_returns_structured_error(self, mcp_env):
        from bible_cc_plugin.mcp.server import _handle_tool

        data = _payload(
            await _handle_tool("bible_skill_get", {"storage_path": "skill/missing.skill"})
        )
        assert data.get("error")


class TestMcpUnreachable:
    @pytest.mark.asyncio
    async def test_bible_unreachable_returns_structured_error(self, monkeypatch):
        dead_url = f"http://127.0.0.1:{_find_free_port()}"
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", dead_url)

        from bible_cc_plugin.mcp.server import _handle_tool

        content = await _handle_tool("bible_memory_search", {"query": "x"})
        data = _payload(content)
        assert data.get("error")
        assert data.get("suggestion")
