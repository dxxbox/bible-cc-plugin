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

    def test_tool_input_schema_matches_api(self):
        import inspect

        from bible_cc_plugin.daemon.client import BiBLEClient
        from bible_cc_plugin.mcp.server import TOOLS

        by_name = {t["name"]: t for t in TOOLS}
        search_params = set(inspect.signature(BiBLEClient.search_memory).parameters) - {"self"}
        kb_params = set(inspect.signature(BiBLEClient.search_knowledge_base).parameters) - {"self"}

        memory_props = set(by_name["bible_memory_search"]["inputSchema"]["properties"])
        skill_props = set(by_name["bible_skill_search"]["inputSchema"]["properties"])
        kb_props = set(by_name["bible_knowledge_search"]["inputSchema"]["properties"])

        assert memory_props <= search_params
        assert skill_props <= search_params
        assert kb_props <= kb_params
        assert by_name["bible_memory_search"]["inputSchema"]["properties"]["tag"]["default"] == (
            "memory"
        )
        assert by_name["bible_skill_search"]["inputSchema"]["properties"]["tag"]["default"] == (
            "skill"
        )
        assert by_name["bible_memory_search"]["inputSchema"]["properties"]["top_k"]["default"] == 8
        assert set(by_name["bible_knowledge_search"]["inputSchema"]["required"]) == {
            "query",
            "tag",
        }

        assert set(by_name["bible_memory_save"]["inputSchema"]["properties"]) == {
            "messages",
            "title",
            "abstract",
        }
        assert by_name["bible_memory_get"]["inputSchema"]["required"] == ["storage_path"]
        assert by_name["bible_skill_get"]["inputSchema"]["required"] == ["storage_path"]

    @pytest.mark.asyncio
    async def test_tool_error_response_schema(self, monkeypatch):
        from bible_cc_plugin.mcp.server import _handle_tool
        from tests.conftest import _find_free_port

        def _assert_error_shape(data: dict) -> None:
            for key in ("error", "detail", "suggestion"):
                assert isinstance(data.get(key), str), f"missing {key}: {data}"

        monkeypatch.delenv("BIBLE_ATLAS_BASE_URL", raising=False)
        data = json.loads((await _handle_tool("bible_memory_search", {"query": "x"}))[0].text)
        _assert_error_shape(data)

        data = json.loads(
            (await _handle_tool("bible_memory_delete", {"storage_path": "/x"}))[0].text
        )
        _assert_error_shape(data)

        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", f"http://127.0.0.1:{_find_free_port()}")
        data = json.loads((await _handle_tool("bible_memory_search", {"query": "x"}))[0].text)
        _assert_error_shape(data)

    def test_tool_list_stable(self):
        from bible_cc_plugin.mcp.server import TOOLS

        names = [t["name"] for t in TOOLS]
        assert names == [
            "bible_memory_search",
            "bible_memory_save",
            "bible_memory_get",
            "bible_knowledge_search",
            "bible_skill_search",
            "bible_skill_get",
            "bible_memory_delete",
            "bible_knowledge_list",
        ]
        assert len(names) == len(set(names))
        assert all(t["description"] for t in TOOLS)
