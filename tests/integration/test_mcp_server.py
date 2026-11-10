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


# ---------------------------------------------------------------------------
# Phase 4c — recall integration additions
# ---------------------------------------------------------------------------


class TestMcpRecallIntegration:
    @pytest.mark.asyncio
    async def test_mcp_parallel_tool_calls(self, mcp_env):
        import asyncio

        from bible_cc_plugin.mcp.server import _handle_tool

        contents = await asyncio.gather(
            _handle_tool("bible_memory_search", {"query": "*"}),
            _handle_tool("bible_knowledge_search", {"query": "*", "tag": "design"}),
            _handle_tool("bible_skill_search", {"query": "*"}),
        )
        payloads = [_payload(c) for c in contents]
        assert [p["total"] for p in payloads] == [1, 1, 1]
        assert all(p.get("error") is None for p in payloads)

    def test_mcp_startup_diagnostics(self, mcp_env, tmp_path):
        import os
        import subprocess
        import sys
        import time

        log_path = tmp_path / "mcp-startup.log"
        env = {
            **os.environ,
            "BIBLE_ATLAS_BASE_URL": mcp_env,
            "BIBLE_CC_LOG_FILE": str(log_path),
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "bible_cc_plugin.mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            deadline = time.time() + 10
            text = ""
            while time.time() < deadline:
                text = log_path.read_text() if log_path.exists() else ""
                if "health=OK" in text:
                    break
                time.sleep(0.1)

            assert "starting on stdio transport" in text
            assert "registered 8 tools" in text
            assert "health=OK" in text
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    @pytest.mark.asyncio
    async def test_mcp_tool_call_logging(self, mcp_env, monkeypatch, tmp_path):
        import json

        log_path = tmp_path / "daemon.log"
        monkeypatch.setenv("BIBLE_CC_LOG_FILE", str(log_path))

        from bible_cc_plugin.mcp.server import _handle_tool

        # ok
        await _handle_tool("bible_memory_search", {"query": "*"})
        # postponed
        await _handle_tool("bible_memory_delete", {"storage_path": "/x"})
        # config_error
        monkeypatch.delenv("BIBLE_ATLAS_BASE_URL", raising=False)
        await _handle_tool("bible_memory_search", {"query": "x"})
        # ok (save) — restore base URL first
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", mcp_env)
        await _handle_tool(
            "bible_memory_save",
            {"messages": [{"role": "user", "content": "SECRET BODY"}], "title": "T"},
        )

        calls_path = tmp_path / "mcp-calls.jsonl"
        calls = [json.loads(line) for line in calls_path.read_text().splitlines() if line.strip()]
        assert [c["status"] for c in calls] == ["ok", "postponed", "config_error", "ok"]
        for record in calls:
            assert {
                "time",
                "tool",
                "args_summary",
                "result_summary",
                "latency_ms",
                "status",
            } <= set(record)

        save = calls[-1]
        assert "SECRET BODY" not in save["args_summary"]
        assert save["content_len"] > 0
        assert save["task_id"]

    @pytest.mark.asyncio
    async def test_mcp_bible_recovery(self, monkeypatch, tmp_path):
        import os
        import subprocess

        from tests.conftest import (
            BIBLE_ATLAS,
            BIBLE_PY,
            _find_free_port,
            _wait_bible_health,
        )

        if not BIBLE_PY.exists():
            pytest.skip(f"BiBLE-Atlas venv not found at {BIBLE_PY}")

        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", base_url)
        monkeypatch.setenv("BIBLE_CC_LOG_FILE", str(tmp_path / "daemon.log"))

        from bible_cc_plugin.mcp.server import _handle_tool

        first = _payload(await _handle_tool("bible_memory_search", {"query": "*"}))
        assert first.get("error")

        log_fh = open(tmp_path / "second-server.log", "w")
        proc = subprocess.Popen(
            [str(BIBLE_PY), "-m", "bible.test_mode.server", "--addr", f"127.0.0.1:{port}"],
            cwd=str(BIBLE_ATLAS),
            stdout=log_fh,
            stderr=log_fh,
            env={**os.environ, "PYTHONPATH": str(BIBLE_ATLAS)},
        )
        try:
            _wait_bible_health(base_url)
            second = _payload(await _handle_tool("bible_memory_search", {"query": "*"}))
            assert second.get("error") is None
            assert second["total"] >= 1
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            log_fh.close()
