#!/usr/bin/env python3
"""Phase 0 minimal hook bridge — daemon auto-start on SessionStart.

Design: 02-interfaces.md §2 (hook conventions). Phase 0 implements only
session-start (idempotent daemon start). Other hooks are silent pass-through
until Phase 1 (turn/user, turn/tool) and Phase 2 (session-end).

Graceful degradation: hook failures must never block Claude Code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from bible_cc_plugin.config import load_config
from bible_cc_plugin.daemon.daemon_launcher import ensure_daemon_started
from bible_cc_plugin.logging_config import configure_logging, get_logger

_logger = get_logger("hook")

_DAEMON_LOG = Path.home() / ".bible-cc" / "daemon.log"


def _local_client(timeout: int = 5) -> httpx.Client:
    """httpx client that bypasses proxy for 127.0.0.1."""
    return httpx.Client(trust_env=False, timeout=timeout)


def _tail_log(path: Path, lines: int = 20) -> str:
    """Return the last N lines of a file, or empty string if file missing."""
    try:
        content = path.read_text()
        all_lines = content.splitlines()
        return "\n".join(all_lines[-lines:])
    except (OSError, FileNotFoundError):
        return ""


# ── Phase 2a action handlers ──────────────────────────────────────────────


def _handle_session_start(config, args) -> None:
    """三步流程：ensure daemon → register session → inject context → stdout.

    Daemon start is session-agnostic — it runs even when session_id is
    absent (e.g. startup event fires before a session exists).
    """
    # 1. Ensure daemon running（session-agnostic, must run first）
    ok = ensure_daemon_started(config.daemon.port, _DAEMON_LOG)
    if not ok:
        _logger.warning("daemon failed to start")
        return

    base_url = f"http://127.0.0.1:{config.daemon.port}"

    # startup event: no session_id — daemon is running, skip session ops
    if not args.session_id:
        _logger.warning(
            "session-start missing --session-id (startup event — "
            "daemon started, session registration deferred)"
        )
        return

    # 2. Register session
    try:
        r = _local_client().post(
            f"{base_url}/session/start",
            json={"session_id": args.session_id},
        )
        r.raise_for_status()
        body = r.json()
        is_new = body.get("is_new")
        _logger.info("POST /session/start... OK (is_new=%s)", is_new)
    except Exception as e:
        _logger.error("/session/start failed: %s", e)
        return

    # 3. Inject context
    try:
        r = _local_client().post(
            f"{base_url}/context/inject",
            json={"session_id": args.session_id, "user_message": args.message or ""},
        )
        r.raise_for_status()
        body = r.json()
        context = body.get("context", "")
        if context:
            print(context)  # stdout → CC inject（依赖 hooks.json inject:true）
        _logger.info("POST /context/inject... OK")
    except Exception as e:
        _logger.error("/context/inject failed: %s", e)

    _logger.info("session-start DONE")


def _handle_turn_user(config, args) -> None:
    """POST /turn/user {session_id, message}."""
    if not args.session_id:
        _logger.error("turn-user missing --session-id")
        return
    base_url = f"http://127.0.0.1:{config.daemon.port}"
    try:
        r = _local_client().post(
            f"{base_url}/turn/user",
            json={"session_id": args.session_id, "message": args.message or ""},
        )
        r.raise_for_status()
        body = r.json()
        sid = args.session_id[:8]
        mlen = len(args.message or "")
        tid = body.get("turn_id")
        _logger.info("turn-user %s msg_len=%d → OK turn_id=%s", sid, mlen, tid)
    except httpx.HTTPStatusError as e:
        _logger.warning("turn-user daemon returned %d → skipping (%s)", e.response.status_code, e)
    except Exception as e:
        _logger.warning("turn-user daemon unreachable → skipping (%s)", e)


def _handle_turn_tool(config, args) -> None:
    """POST /turn/tool {session_id, tool_name, arguments, output}."""
    if not args.session_id or not args.tool:
        _logger.error("turn-tool missing --session-id or --tool")
        return
    # Parse CLAUDE_TOOL_INPUT JSON string → dict
    arguments = {}
    if args.input:
        try:
            arguments = __import__("json").loads(args.input)
        except Exception:
            arguments = {}
    base_url = f"http://127.0.0.1:{config.daemon.port}"
    try:
        r = _local_client().post(
            f"{base_url}/turn/tool",
            json={
                "session_id": args.session_id,
                "tool_name": args.tool,
                "arguments": arguments,
                "output": args.output or "",
            },
        )
        r.raise_for_status()
        olen = len(args.output or "")
        sid = args.session_id[:8]
        cmd = arguments.get("command", "")[:80] if args.tool == "Bash" else ""
        _logger.info("turn-tool %s %s %s out=%d → OK", sid, args.tool, cmd, olen)
    except httpx.HTTPStatusError as e:
        _logger.warning("turn-tool daemon returned %d → skipping (%s)", e.response.status_code, e)
    except Exception as e:
        _logger.warning("turn-tool daemon unreachable → skipping (%s)", e)


def _handle_turn_stop(config, args) -> None:
    """Stop hook handler — no-op placeholder for Phase 2b mid-session detection.

    Claude Code Stop fires after every assistant response ("once per turn").
    This handler exists as a wiring point — Phase 2b will queue async
    moment detection here.  Currently returns immediately.
    """
    _logger.info("turn-stop — TODO(Phase 2b): queue async mid-session moment detection")


def _handle_session_end(config, args) -> None:
    """POST /session/end {session_id}."""
    if not args.session_id:
        _logger.error("session-end missing --session-id")
        return
    base_url = f"http://127.0.0.1:{config.daemon.port}"
    try:
        r = _local_client().post(
            f"{base_url}/session/end",
            json={"session_id": args.session_id},
        )
        r.raise_for_status()
        _logger.info("POST /session/end... OK")
    except Exception as e:
        _logger.warning("session-end daemon unreachable → skipping (%s)", e)


# ── main ───────────────────────────────────────────────────────────────────


def main() -> None:
    # 1. Read stdin JSON — Claude Code hooks deliver all data via stdin
    stdin_data: dict = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                stdin_data = json.loads(raw)
        except Exception:
            _logger.debug("stdin parse failed (non-JSON or empty)", exc_info=True)

    # 2. CLI args（测试/手动调用时使用；action 始终从 CLI 获取）
    parser = argparse.ArgumentParser(description="bible-cc hook bridge")
    parser.add_argument(
        "action",
        choices=["session-start", "turn-user", "turn-tool", "turn-stop", "session-end"],
    )
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--message", default=None)
    parser.add_argument("--tool", default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    # 3. Config + logging（必须在任何 _logger.info 之前，否则文件 handler 未就绪）
    try:
        config = load_config()
        configure_logging(**config.logging.model_dump())
    except Exception as e:
        _logger.error("config load failed: %s", e)
        sys.exit(0)

    # 4. Merge: CLI 优先（测试覆盖），stdin 兜底（Claude Code hook 数据）
    session_id = args.session_id or stdin_data.get("session_id", "")
    message = args.message or stdin_data.get("prompt", "")
    tool = args.tool or stdin_data.get("tool_name", "")
    tool_input = args.input
    if not tool_input and "tool_input" in stdin_data:
        tool_input = json.dumps(stdin_data["tool_input"])
    raw_output = args.output or stdin_data.get("tool_response", "")
    if isinstance(raw_output, dict):
        tool_output = json.dumps(raw_output, ensure_ascii=False)
    elif isinstance(raw_output, str):
        tool_output = raw_output
    else:
        tool_output = str(raw_output) if raw_output else ""

    merged = argparse.Namespace(
        action=args.action,
        session_id=session_id,
        message=message,
        tool=tool,
        input=tool_input,
        output=tool_output,
    )

    _logger.info(
        "hook=%s stdin_sid=%s cli_sid=%s merged_sid=%s msg_len=%d",
        args.action,
        stdin_data.get("session_id", "<none>") or "<empty>",
        args.session_id or "<none>",
        session_id or "<empty>",
        len(message or ""),
    )

    # 5. Dispatch（不变）
    if merged.action == "session-start":
        _handle_session_start(config, merged)
    elif merged.action == "turn-user":
        _handle_turn_user(config, merged)
    elif merged.action == "turn-tool":
        _handle_turn_tool(config, merged)
    elif merged.action == "turn-stop":
        _handle_turn_stop(config, merged)
    elif merged.action == "session-end":
        _handle_session_end(config, merged)


if __name__ == "__main__":
    main()
