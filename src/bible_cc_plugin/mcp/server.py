"""MCP Server — BiBLE Atlas tools via stdio transport.

All 8 tools registered (6 active + 2 postponed).  Active tools call BiBLE
Atlas through ``client.py``; failures return structured errors instead of
crashing the server.

Phase 4b: startup diagnostics + structured per-call tracking appended to
``mcp-calls.jsonl`` next to the daemon log (read by
``GET /daemon/debug/mcp-calls``).  No HTTP communication with the daemon.

Design: 02-interfaces.md §3, 06-recall/mcp-tools.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from bible_cc_plugin.daemon.client import BiBLEClient, BiBLEError, BibleUnreachableError


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
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s %(message)s"))
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

# ── Call tracking (Phase 4b) ────────────────────────────────────────────────
#
# MCP server and daemon are independent processes (no HTTP between them).
# Each tool call appends one JSON line; the daemon reads the file tail via
# GET /daemon/debug/mcp-calls.  Path follows BIBLE_CC_LOG_FILE so the test
# autouse fixture isolates it to a temp directory without a new env var.

_MCP_CALLS_FILENAME = "mcp-calls.jsonl"
_MCP_CALLS_MAX_BYTES = 5 * 1024 * 1024


def _mcp_calls_path() -> Path:
    """Return the JSONL call-record path (derived from the daemon log path)."""
    log_file = os.getenv("BIBLE_CC_LOG_FILE") or str(Path.home() / ".bible-cc" / "daemon.log")
    return Path(log_file).expanduser().parent / _MCP_CALLS_FILENAME


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _summarize_args(name: str, arguments: dict) -> str:
    """Production-safe, truncated argument summary for logs and JSONL.

    ``bible_memory_save`` never includes message bodies — only title,
    message count, and total content length.
    """
    if name == _TOOL_MEMORY_SAVE:
        messages = arguments.get("messages") or []
        total_chars = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
        summary = (
            f"title={arguments.get('title', '')}, "
            f"messages={len(messages)}, content_len={total_chars}"
        )
        return summary[:200]

    parts = []
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > 60:
            value = value[:60] + "…"
        parts.append(f"{key}={value}")
    return ", ".join(parts)[:200]


def _input_content_len(name: str, arguments: dict) -> int | None:
    """Content length for save operations (message bodies), else None."""
    if name != _TOOL_MEMORY_SAVE:
        return None
    messages = arguments.get("messages") or []
    return sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))


def _append_jsonl(path: Path, entry: dict) -> None:
    """Append one JSON line, rotating to ``.1`` past the size threshold."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.stat().st_size > _MCP_CALLS_MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _record_mcp_call(
    *,
    tool: str,
    args_summary: str,
    result_summary: str,
    latency_ms: int,
    status: str,
    task_id: str | None = None,
    content_len: int | None = None,
) -> None:
    """Best-effort append of one structured call record."""
    record = {
        "time": _utc_now_iso(),
        "tool": tool,
        "args_summary": args_summary,
        "result_summary": result_summary,
        "latency_ms": latency_ms,
        "status": status,
    }
    if task_id:
        record["task_id"] = task_id
    if content_len is not None:
        record["content_len"] = content_len
    try:
        _append_jsonl(_mcp_calls_path(), record)
    except Exception as exc:  # best-effort — never break the tool call
        _logger.debug("mcp call record failed: %s", exc)


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
        "description": ("[POSTPONED] Delete a memory — not yet available in BiBLE V4 API."),
        "inputSchema": {
            "type": "object",
            "properties": {"storage_path": {"type": "string"}},
            "required": ["storage_path"],
        },
    },
    {
        "name": _TOOL_KNOWLEDGE_LIST,
        "description": ("[POSTPONED] List knowledge entries — not yet available in BiBLE V4 API."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── Tool dispatcher ─────────────────────────────────────────────────────────


async def _handle_tool(name: str, arguments: dict) -> list:
    """Dispatch tool call → list of TextContent.

    Logging format per Phase 4 F4.6:
      [mcp:tool] bible_memory_search(query=test, top_k=5) → 1 hits (300ms)
      [mcp:tool] ERROR: bible_memory_search → reason (100ms)

    Every outcome is also appended to ``mcp-calls.jsonl`` (Phase 4b).
    """
    start = time.monotonic()
    args_summary = _summarize_args(name, arguments)
    input_content_len = _input_content_len(name, arguments)

    def _record(
        status: str,
        result_summary: str,
        *,
        task_id: str | None = None,
        content_len: int | None = None,
    ) -> None:
        _record_mcp_call(
            tool=name,
            args_summary=args_summary,
            result_summary=result_summary,
            latency_ms=int((time.monotonic() - start) * 1000),
            status=status,
            task_id=task_id,
            content_len=input_content_len if content_len is None else content_len,
        )

    # Postponed tools — permanent "not yet available"
    if name in _POSTPONED:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _logger.warning(
            "[mcp:tool] POSTPONED: %s → not yet available (%dms)",
            name,
            elapsed_ms,
        )
        _record("postponed", "not yet available")
        return _json_content(
            {
                "error": f"Tool '{name}' is not yet available.",
                "detail": "This BiBLE V4 API endpoint has not been implemented yet.",
                "suggestion": "Check /bible-cc:status for updates.",
            }
        )

    # Missing base URL — config error
    base_url = os.getenv("BIBLE_ATLAS_BASE_URL", "")
    if not base_url:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _logger.warning(
            "[mcp:tool] CONFIG: %s → BIBLE_ATLAS_BASE_URL not set (%dms)",
            name,
            elapsed_ms,
        )
        _record("config_error", "BIBLE_ATLAS_BASE_URL not set")
        return _json_content(
            {
                "error": "BiBLE Atlas not configured.",
                "detail": "BIBLE_ATLAS_BASE_URL is not set.",
                "suggestion": (
                    "Run 'uv run python -m bible_cc_plugin.scripts.setup' "
                    "or set BIBLE_ATLAS_BASE_URL. Then check /bible-cc:status."
                ),
            }
        )

    # Phase 3b: real BiBLE client call
    try:
        async with _build_client() as client:
            result = await _dispatch(client, name, arguments)
    except BiBLEError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _logger.error(
            "[mcp:tool] ERROR: %s → BiBLEError [%s] (%dms)",
            name,
            getattr(exc, "code", "?"),
            elapsed_ms,
        )
        _record("error", f"BiBLEError [{getattr(exc, 'code', '?')}]")
        return _degradation_error(exc)
    except BibleUnreachableError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _logger.error(
            "[mcp:tool] ERROR: %s → unreachable (%dms)",
            name,
            elapsed_ms,
        )
        _record("error", "unreachable")
        return _degradation_error(exc)
    except Exception as exc:  # unexpected — never escape to stdio
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _logger.error(
            "[mcp:tool] ERROR: %s → unexpected %r (%dms)",
            name,
            exc,
            elapsed_ms,
        )
        _record("error", f"unexpected: {type(exc).__name__}")
        return _degradation_error(exc)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    result_summary = _summarize_result(name, result)
    task_id = result.get("task_id") if isinstance(result, dict) else None
    output_content_len = None
    if isinstance(result, dict) and isinstance(result.get("content"), str):
        output_content_len = len(result["content"])
    status = "error" if isinstance(result, dict) and result.get("error") else "ok"
    _logger.info(
        "[mcp:tool] %s(%s) → %s (%dms)",
        name,
        args_summary,
        result_summary,
        elapsed_ms,
    )
    _record(status, result_summary, task_id=task_id, content_len=output_content_len)
    return _json_content(result)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_client() -> BiBLEClient:
    """Create a BiBLEClient from env vars."""
    from bible_cc_plugin.config import BibleConfig

    return BiBLEClient(
        BibleConfig(
            base_url=os.getenv("BIBLE_ATLAS_BASE_URL", "http://localhost:5555"),
            token=os.getenv("BIBLE_ATLAS_TOKEN", ""),
            kb_index=os.getenv("BIBLE_CC_KB_INDEX", "bible-cc"),
        )
    )


async def _dispatch(client: BiBLEClient, name: str, args: dict) -> dict:
    """Route tool call to the appropriate client method."""
    if name == _TOOL_MEMORY_SEARCH:
        results = await client.search_memory(
            query=args["query"],
            tag=args.get("tag", "memory"),
            top_k=args.get("top_k"),
            search_type=args.get("search_type"),
        )
        return {"results": results, "total": len(results)}

    if name == _TOOL_MEMORY_SAVE:
        messages = args.get("messages", [])
        if not messages:
            return {"error": "No messages to save.", "detail": "messages is required."}
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        title = args.get("title") or "Saved memory"
        abstract = args.get("abstract") or ""
        overview = "\n".join(f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in messages)[
            :2000
        ]
        meta = {
            "memory_id": f"bible-cc-save-{int(time.time() * 1000)}",
            "title": title[:200],
            "abstract": (abstract or title)[:500],
            "overview": overview or (abstract or title),
            "created_at": now,
            "updated_at": now,
            "source_client": "bible-cc",
            "language": "zh",
        }
        payload = {
            "messages": messages,
            "title": title,
            "abstract": abstract,
            "saved_at": now,
        }
        files = [
            (
                "meta.json",
                json.dumps(meta, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
            (
                "memory-save.json",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
        ]
        result = await client.import_memory(files)
        return {"task_id": result.get("task_id"), "status": "queued"}

    if name == _TOOL_MEMORY_GET:
        result = await client.request_memory_download(
            args["storage_path"],
        )
        task_id = result.get("task_id")
        if not task_id:
            return {"error": "Download task_id missing."}
        return await _poll_download(client, "memory", task_id)

    if name == _TOOL_KNOWLEDGE_SEARCH:
        results = await client.search_knowledge_base(
            query=args["query"],
            tag=args["tag"],
            top_k=args.get("top_k"),
            search_type=args.get("search_type"),
        )
        return {"results": results, "total": len(results)}

    if name == _TOOL_SKILL_SEARCH:
        results = await client.search_skill(
            query=args["query"],
            tag=args.get("tag", "skill"),
            top_k=args.get("top_k"),
            search_type=args.get("search_type"),
        )
        return {"results": results, "total": len(results)}

    if name == _TOOL_SKILL_GET:
        result = await client.request_skill_download(
            args["storage_path"],
        )
        task_id = result.get("task_id")
        if not task_id:
            return {"error": "Download task_id missing."}
        return await _poll_download(client, "skill", task_id)

    return {"error": f"Unknown tool: {name}"}


async def _poll_download(
    client: BiBLEClient,
    domain: str,
    task_id: str,
) -> dict:
    """Poll task status every 2s up to 30 times (60s timeout)."""
    _logger.info(
        "[mcp:tool] download poll task_id=%s domain=%s (60s timeout)",
        task_id,
        domain,
    )
    for i in range(30):
        await asyncio.sleep(2)
        task = await client.get_task_status(task_id)
        status = task.get("status", "")
        if (i + 1) % 5 == 0:
            _logger.info(
                "[mcp:tool] polling task_id=%s (%ds/60s, status=%s)",
                task_id,
                (i + 1) * 2,
                status,
            )
        if status == "completed":
            artifact_id = task.get("result", {}).get("artifact_id")
            if not artifact_id:
                return {"error": "Task completed but no artifact_id.", "task_id": task_id}
            content = await client.get_download_artifact(domain, artifact_id)
            return {
                "task_id": task_id,
                "content": content.decode("utf-8", errors="replace"),
            }
        if status in ("failed", "cancelled"):
            return {
                "error": f"Download task {status}.",
                "task_id": task_id,
                "detail": task.get("result", {}).get("error", ""),
            }
    return {
        "error": "Download timeout after 60s.",
        "task_id": task_id,
        "suggestion": "Check BiBLE /bible-cc:status or retry later.",
    }


def _degradation_error(exc: Exception) -> list:
    """Map client exceptions to structured errors with distinct messages."""
    if isinstance(exc, BibleUnreachableError):
        cat, suggestion = "unreachable", "Check /bible-cc:status for connectivity."
    elif isinstance(exc, BiBLEError) and "INVALID_RESPONSE" in getattr(exc, "code", ""):
        cat, suggestion = (
            "protocol error",
            ("BiBLE returned an unexpected response. Check server version compatibility."),
        )
    elif isinstance(exc, BiBLEError):
        cat, suggestion = (
            "request error",
            (f"[{exc.code}] {exc.message}. Check your BiBLE configuration."),
        )
    else:
        cat, suggestion = "error", "Internal error."

    return _json_content(
        {
            "error": f"BiBLE Atlas {cat}.",
            "detail": str(exc),
            "suggestion": suggestion,
        }
    )


def _summarize_result(name: str, result: dict) -> str:
    """Produce a compact log summary for the result."""
    if isinstance(result, dict) and result.get("error"):
        return f"error: {str(result['error'])[:80]}"
    if "total" in result:
        return f"{result['total']} hits"
    if "task_id" in result:
        return f"task_id={result['task_id']}"
    if "content" in result:
        return f"content_len={len(result['content'])}"
    return "OK"


def _json_content(data: dict) -> list:
    """Wrap a dict as MCP TextContent list."""
    from mcp.types import TextContent

    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]


# ── Main entry point ────────────────────────────────────────────────────────


def main():
    """`uv run python -m bible_cc_plugin.mcp.server`"""
    base_url = os.getenv("BIBLE_ATLAS_BASE_URL", "")

    _logger.info("[mcp:server] starting on stdio transport")
    _logger.info(
        "[mcp:server] registered %d tools: %s",
        len(TOOLS),
        ", ".join(t["name"] for t in TOOLS),
    )

    # Best-effort BiBLE health check at startup (sync, 3s cap, never blocks).
    if base_url:
        try:
            import httpx

            r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=3)
            if r.status_code == 200:
                latency = int(r.elapsed.total_seconds() * 1000)
                _logger.info(
                    "[mcp:server] BiBLE base_url=%s, health=OK (%dms)",
                    base_url,
                    latency,
                )
            else:
                _logger.warning(
                    "[mcp:server] BiBLE base_url=%s, health=HTTP %d",
                    base_url,
                    r.status_code,
                )
                _logger.warning(
                    "[mcp:server] WARNING: tools will return structured errors until BiBLE recovers"
                )
        except Exception as e:
            _logger.warning(
                "[mcp:server] BiBLE base_url=%s, health=UNREACHABLE (%s)",
                base_url,
                e,
            )
            _logger.warning(
                "[mcp:server] WARNING: tools will return structured errors until BiBLE recovers"
            )
    else:
        _logger.warning(
            "[mcp:server] BiBLE base_url=<not configured> — tools will return config errors"
        )

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("bible-cc")

    for tool_def in TOOLS:
        name = tool_def["name"]

        def make_handler(n: str):
            async def handler(**kwargs):
                return await _handle_tool(n, kwargs)

            return handler

        mcp.tool(name=name, description=tool_def["description"])(make_handler(name))

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
