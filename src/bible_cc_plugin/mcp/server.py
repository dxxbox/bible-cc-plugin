"""MCP Server — BiBLE Atlas tools via stdio transport.

Phase 2: framework skeleton. All 8 tools registered with proper inputSchema.
Active tools return structured degradation errors (BiBLE not yet connected).
Postponed tools return "not yet available".

Phase 3/4: replace with real BiBLE API calls via client.py.

Design: 02-interfaces.md §3, 06-recall/mcp-tools.md.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path


def _setup_logging():
    """Configure MCP logging to append to ~/.bible-cc/daemon.log.

    Phase 0 rule D: all components write to one log file.
    MCP server is a standalone stdio process — cannot rely on daemon's
    file handler, so we open it explicitly.
    """
    log_path = Path(os.getenv("BIBLE_CC_LOG_FILE", str(Path.home() / ".bible-cc" / "daemon.log")))
    log_path = log_path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("bible_cc.mcp")
    logger.setLevel(logging.DEBUG if os.getenv("BIBLE_CC_DEBUG") else logging.INFO)

    # File handler — append mode
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(name)s] %(levelname)s %(message)s"
    ))
    logger.addHandler(fh)

    # Also log to stderr for MCP diagnostics
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)  # stderr only gets warnings+
    sh.setFormatter(logging.Formatter("[%(name)s] %(levelname)s %(message)s"))
    logger.addHandler(sh)

    return logger


_logger = _setup_logging()

# ── Tool name constants ─────────────────────────────────────────────────────

_TOOL_MEMORY_SEARCH = "bible_memory_search"
_TOOL_MEMORY_SAVE = "bible_memory_save"
_TOOL_MEMORY_GET = "bible_memory_get"
_TOOL_KNOWLEDGE_SEARCH = "bible_knowledge_search"
_TOOL_SKILL_SEARCH = "bible_skill_search"
_TOOL_SKILL_GET = "bible_skill_get"
_TOOL_MEMORY_DELETE = "bible_memory_delete"
_TOOL_KNOWLEDGE_LIST = "bible_knowledge_list"

_POSTPONED = {_TOOL_MEMORY_DELETE, _TOOL_KNOWLEDGE_LIST}

# ── Tool definitions (inputSchema per 06-recall/mcp-tools.md §1) ────────────

TOOLS: list[dict] = [
    {
        "name": _TOOL_MEMORY_SEARCH,
        "description": "Search personal memory in BiBLE Atlas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "tag": {"type": "string", "default": "memory"},
                "top_k": {"type": "integer", "default": 8},
                "search_type": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": _TOOL_MEMORY_SAVE,
        "description": "Manually save a memory to BiBLE Atlas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "messages": {"type": "array", "description": "Conversation messages"},
                "title": {"type": "string"},
                "abstract": {"type": "string"},
            },
            "required": ["messages"],
        },
    },
    {
        "name": _TOOL_MEMORY_GET,
        "description": "Download a memory file from BiBLE Atlas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "storage_path": {"type": "string", "description": "Memory file path"},
            },
            "required": ["storage_path"],
        },
    },
    {
        "name": _TOOL_KNOWLEDGE_SEARCH,
        "description": "Search knowledge base in BiBLE Atlas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "tag": {"type": "string", "default": "knowledge"},
                "top_k": {"type": "integer", "default": 8},
            },
            "required": ["query", "tag"],
        },
    },
    {
        "name": _TOOL_SKILL_SEARCH,
        "description": "Search skills in BiBLE Atlas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "tag": {"type": "string", "default": "skill"},
                "top_k": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": _TOOL_SKILL_GET,
        "description": "Download a skill file from BiBLE Atlas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "storage_path": {"type": "string", "description": "Skill file path"},
            },
            "required": ["storage_path"],
        },
    },
    {
        "name": _TOOL_MEMORY_DELETE,
        "description": (
            "[POSTPONED] Delete a memory — "
            "not yet available in BiBLE V4 API."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"storage_path": {"type": "string"}},
            "required": ["storage_path"],
        },
    },
    {
        "name": _TOOL_KNOWLEDGE_LIST,
        "description": (
            "[POSTPONED] List knowledge entries — "
            "not yet available in BiBLE V4 API."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── Tool dispatcher ─────────────────────────────────────────────────────────


def _handle_tool(name: str, arguments: dict) -> list:
    """Dispatch tool call → list of TextContent.

    Logging format per Phase 4 F4.6:
      [mcp:tool] bible_memory_search(query="...", top_k=5) → 0 hits (0.3s)
      [mcp:tool] ERROR: bible_memory_search → reason (0.1s)
    """
    start = time.monotonic()

    # Postponed tools — permanent "not yet available"
    if name in _POSTPONED:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _logger.warning(
            "[mcp:tool] POSTPONED: %s → not yet available (%dms)",
            name, elapsed_ms,
        )
        return _json_content({
            "error": f"Tool '{name}' is not yet available.",
            "detail": "This BiBLE V4 API endpoint has not been implemented yet.",
            "suggestion": "Check /bible-cc:status for updates.",
        })

    # Missing base URL — config error
    base_url = os.getenv("BIBLE_ATLAS_BASE_URL", "")
    if not base_url:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _logger.warning(
            "[mcp:tool] CONFIG: %s → BIBLE_ATLAS_BASE_URL not set (%dms)",
            name, elapsed_ms,
        )
        return _json_content({
            "error": "BiBLE Atlas not configured.",
            "detail": "BIBLE_ATLAS_BASE_URL is not set.",
            "suggestion": (
                "Run 'uv run python -m bible_cc_plugin.scripts.setup' "
                "or set BIBLE_ATLAS_BASE_URL. Then check /bible-cc:status."
            ),
        })

    # Phase 2 degradation — BiBLE connected in Phase 3/4
    elapsed_ms = int((time.monotonic() - start) * 1000)
    args_summary = ", ".join(
        f"{k}={repr(v)[:60]}" for k, v in arguments.items()
    )
    _logger.info(
        "[mcp:tool] %s(%s) → degradation (base_url=%s, %dms)",
        name, args_summary, base_url, elapsed_ms,
    )
    return _json_content({
        "error": "BiBLE Atlas not yet connected (Phase 3).",
        "detail": (
            f"MCP tool '{name}' is registered but BiBLE integration "
            "is scheduled for Phase 3/4. Local capture is working."
        ),
        "suggestion": (
            "Use /bible-cc:review to manage locally captured moments. "
            "Use /bible-cc:status for system health."
        ),
    })


def _json_content(data: dict) -> list:
    """Wrap a dict as MCP TextContent list."""
    from mcp.types import TextContent

    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]


# ── Main entry point ────────────────────────────────────────────────────────


def main():
    """`uv run python -m bible_cc_plugin.mcp.server`"""
    base_url = os.getenv("BIBLE_ATLAS_BASE_URL", "")

    _logger.info("[mcp:server] starting on stdio — %d tools registered", len(TOOLS))
    _logger.info("[mcp:server] BiBLE base_url=%s", base_url or "<not configured>")
    _logger.info(
        "[mcp:server] tools: %s",
        ", ".join(t["name"] for t in TOOLS),
    )

    # Best-effort BiBLE health check at startup
    if base_url:
        try:
            import httpx
            r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=3)
            if r.status_code == 200:
                latency = int(r.elapsed.total_seconds() * 1000)
                _logger.info("[mcp:server] BiBLE health=OK (%dms)", latency)
            else:
                _logger.warning(
                    "[mcp:server] BiBLE health=HTTP %d (%s)", r.status_code, base_url
                )
        except Exception as e:
            _logger.warning("[mcp:server] BiBLE health=UNREACHABLE (%s)", e)
    else:
        _logger.warning("[mcp:server] BiBLE health=SKIP (base_url not configured)")

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("bible-cc")

    for tool_def in TOOLS:
        name = tool_def["name"]

        def make_handler(n: str):
            def handler(**kwargs):
                return _handle_tool(n, kwargs)
            return handler

        mcp.tool(name=name, description=tool_def["description"])(make_handler(name))

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
