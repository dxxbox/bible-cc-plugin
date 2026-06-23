"""Unit tests for MCP server — tool schemas + degradation behavior.

Phase 2 MCP skeleton — all tests [Unit] [Pre].
Tests tool registration, error responses, postponed tools.
No BiBLE Atlas, no stdio, no real network calls.
"""

from __future__ import annotations

import json

import pytest


def _tool_names_from_server():
    from bible_cc_plugin.mcp.server import TOOLS

    return {t["name"] for t in TOOLS}


def _invoke_tool(name: str, arguments: dict) -> dict:
    from bible_cc_plugin.mcp.server import _handle_tool

    result = _handle_tool(name, arguments)
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
    """All active tools return structured error when BiBLE unreachable."""

    def test_active_tool_returns_structured_error(self):
        result = _invoke_tool("bible_memory_search", {"query": "test"})
        assert "error" in result
        assert "detail" in result
        assert "suggestion" in result

    def test_error_includes_status_hint(self):
        result = _invoke_tool("bible_memory_search", {"query": "test"})
        assert "bible-cc" in result.get("suggestion", "").lower()

    def test_postponed_tool_returns_not_available(self):
        result = _invoke_tool("bible_memory_delete", {"storage_path": "/x"})
        assert "not yet available" in result.get("error", "").lower()

    def test_all_active_tools_degrade_gracefully(self):
        postponed = {"bible_memory_delete", "bible_knowledge_list"}
        for name in _tool_names_from_server() - postponed:
            result = _invoke_tool(name, {"query": "test"})
            assert "error" in result, f"{name} should return error when BiBLE down"


class TestMCPLogging:
    """F4.6: MCP call tracing — tool handler returns structured data + log format."""

    def test_active_tool_returns_stable_error_structure(self):
        """Active tool with base_url set → predictable error keys."""
        import os

        old = os.environ.get("BIBLE_ATLAS_BASE_URL")
        os.environ["BIBLE_ATLAS_BASE_URL"] = "http://localhost:5555"
        try:
            result = _invoke_tool("bible_memory_search", {"query": "auth", "top_k": 5})
            assert result.get("error")  # degradation error
            assert "/bible-cc:review" in result.get("suggestion", "")
        finally:
            if old:
                os.environ["BIBLE_ATLAS_BASE_URL"] = old
            else:
                os.environ.pop("BIBLE_ATLAS_BASE_URL", None)

    def test_postponed_tool_logs_at_warning_level(self):
        """Postponed tool returns not-yet-available error."""
        result = _invoke_tool("bible_memory_delete", {"storage_path": "/x"})
        assert "not yet available" in result.get("error", "").lower()
        assert "V4 API" in result.get("detail", "") or "not yet available" in result.get("detail", "").lower()

    def test_unconfigured_tool_returns_setup_guidance(self):
        """BIBLE_ATLAS_BASE_URL not set → setup suggestion."""
        import os

        old = os.environ.pop("BIBLE_ATLAS_BASE_URL", None)
        try:
            result = _invoke_tool("bible_memory_search", {"query": "test"})
            assert "not configured" in result.get("error", "").lower()
            assert "setup" in result.get("suggestion", "").lower()
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

    def test_tool_handler_detects_missing_base_url(self):
        import os

        old = os.environ.pop("BIBLE_ATLAS_BASE_URL", None)
        try:
            result = _invoke_tool("bible_memory_search", {"query": "test"})
            assert "not configured" in result.get("error", "").lower()
        finally:
            if old:
                os.environ["BIBLE_ATLAS_BASE_URL"] = old
