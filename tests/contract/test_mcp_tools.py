"""Contract tests — MCP tool registration, input schemas, response structure."""

from __future__ import annotations

import json

import pytest

EXPECTED_TOOLS = {
    "bible_memory_search",
    "bible_memory_save",
    "bible_memory_get",
    "bible_knowledge_search",
    "bible_skill_search",
    "bible_skill_get",
    "bible_memory_delete",
    "bible_knowledge_list",
}


class TestMcpToolContract:
    def test_all_tools_registered(self):
        from bible_cc_plugin.mcp.server import TOOLS

        names = {t["name"] for t in TOOLS}
        assert names == EXPECTED_TOOLS
        assert len(TOOLS) == 8

    def test_input_schema_fields(self):
        from bible_cc_plugin.mcp.server import TOOLS

        by_name = {t["name"]: t for t in TOOLS}
        expected_fields = {
            "bible_memory_search": "query",
            "bible_memory_save": "messages",
            "bible_memory_get": "storage_path",
            "bible_knowledge_search": "query",
            "bible_skill_search": "query",
            "bible_skill_get": "storage_path",
        }
        for name, field in expected_fields.items():
            schema = by_name[name]["inputSchema"]
            assert schema["type"] == "object"
            assert field in schema["properties"]

    @pytest.mark.asyncio
    async def test_postponed_tools_return_not_available(self):
        from bible_cc_plugin.mcp.server import _handle_tool

        for name in ("bible_memory_delete", "bible_knowledge_list"):
            content = await _handle_tool(name, {})
            data = json.loads(content[0].text)
            assert "not yet available" in data["error"]

    @pytest.mark.asyncio
    async def test_tool_response_structure(self, bible_test_url, monkeypatch):
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", bible_test_url)

        from bible_cc_plugin.mcp.server import _handle_tool

        content = await _handle_tool("bible_memory_search", {"query": "x"})
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0].type == "text"
        data = json.loads(content[0].text)
        assert "results" in data
        assert "total" in data
