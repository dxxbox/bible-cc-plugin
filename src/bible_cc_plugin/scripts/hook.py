#!/usr/bin/env python3
"""Phase 0 minimal hook bridge — daemon auto-start on SessionStart.

Design: 02-interfaces.md §2 (hook conventions). Phase 0 implements only
session-start (idempotent daemon start). Other hooks are silent pass-through
until Phase 1 (turn/user, turn/tool) and Phase 2 (session-end).

Graceful degradation: hook failures must never block Claude Code.
"""

from __future__ import annotations

import argparse
import logging
import subprocess  # TODO: remove after deleting _ensure_daemon
import sys
import time  # TODO: remove after deleting _ensure_daemon
from pathlib import Path

import httpx

from bible_cc_plugin.config import load_config
from bible_cc_plugin.daemon.daemon_launcher import ensure_daemon_started

logger = logging.getLogger("bible-cc.hook")
logging.basicConfig(
    level=logging.WARNING,
    format="[bible-cc] %(levelname)s: %(message)s",
    stream=sys.stderr,
)

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


def _ensure_daemon(port: int) -> None:
    """Idempotent: start daemon if not already running.

    Checks health endpoint first. If unreachable, spawns uvicorn and
    waits up to 5s for health check. Daemon output goes to
    ~/.bible-cc/daemon.log. On failure, the log tail is printed.
    Graceful degradation: failure prints warning to stderr
    but does NOT exit non-zero.
    """
    base_url = f"http://127.0.0.1:{port}"
    try:
        r = _local_client(timeout=2).get(f"{base_url}/daemon/health")
        if r.status_code == 200:
            return  # already running
    except Exception:
        pass

    # Ensure log directory exists
    _DAEMON_LOG.parent.mkdir(parents=True, exist_ok=True)

    # Open log file for append — survives daemon restarts
    log_fh = open(str(_DAEMON_LOG), "a")

    # Start uvicorn in background, output → log file
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "bible_cc_plugin.daemon.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "info",
            ],
            stdout=log_fh,
            stderr=log_fh,
        )
    except Exception as e:
        print(f"[bible-cc] WARNING: daemon start failed: {e}", file=sys.stderr)
        log_fh.close()
        return

    # Wait for health check
    deadline = time.time() + 5
    while time.time() < deadline:
        ok = False
        try:
            r = _local_client(timeout=1).get(f"{base_url}/daemon/health")
            ok = r.status_code == 200
        except httpx.ConnectError:
            logger.debug("daemon not ready (connection refused)")
        except Exception:
            logger.warning("health check error: %s", sys.exc_info()[1])
        if ok:
            log_fh.close()
            return  # started successfully
        time.sleep(0.3)

    # Timeout — show log tail for diagnosis
    log_fh.close()
    tail = _tail_log(_DAEMON_LOG)
    print("[bible-cc] WARNING: daemon health check timed out", file=sys.stderr)
    if tail:
        print(f"[bible-cc] Last 20 lines of {_DAEMON_LOG}:", file=sys.stderr)
        print(tail, file=sys.stderr)
    else:
        print(f"[bible-cc] (no log output at {_DAEMON_LOG})", file=sys.stderr)


# ── Phase 2a action handlers ──────────────────────────────────────────────


def _handle_session_start(config, args) -> None:
    """三步流程：ensure daemon → register session → inject context → stdout."""
    base_url = f"http://127.0.0.1:{config.daemon.port}"
    if not args.session_id:
        print("[bible-cc] WARNING: session-start missing --session-id", file=sys.stderr)
        return

    # 1. Ensure daemon running
    ok = ensure_daemon_started(config.daemon.port, _DAEMON_LOG)
    if not ok:
        print("[bible-cc] WARNING: daemon failed to start", file=sys.stderr)
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
        print(f"[hook:session-start] POST /session/start... OK (is_new={is_new})", file=sys.stderr)
    except Exception as e:
        print(f"[bible-cc] WARNING: /session/start failed: {e}", file=sys.stderr)
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
        print("[hook:session-start] POST /context/inject... OK", file=sys.stderr)
    except Exception as e:
        print(f"[bible-cc] WARNING: /context/inject failed: {e}", file=sys.stderr)

    print("[hook:session-start] DONE", file=sys.stderr)


def _handle_turn_user(config, args) -> None:
    """POST /turn/user {session_id, message}."""
    if not args.session_id:
        print("[bible-cc] WARNING: turn-user missing --session-id", file=sys.stderr)
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
        print(f"[hook:turn-user] {sid} msg_len={mlen} → OK turn_id={tid}", file=sys.stderr)
    except Exception as e:
        print(f"[hook:turn-user] daemon unreachable → skipping ({e})", file=sys.stderr)


def _handle_turn_tool(config, args) -> None:
    """POST /turn/tool {session_id, tool_name, arguments, output}."""
    if not args.session_id or not args.tool:
        print("[bible-cc] WARNING: turn-tool missing --session-id or --tool", file=sys.stderr)
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
        print(f"[hook:turn-tool] {sid} {args.tool} out={olen} → OK", file=sys.stderr)
    except Exception as e:
        print(f"[hook:turn-tool] daemon unreachable → skipping ({e})", file=sys.stderr)


def _handle_session_end(config, args) -> None:
    """POST /session/end {session_id}."""
    if not args.session_id:
        print("[bible-cc] WARNING: session-end missing --session-id", file=sys.stderr)
        return
    base_url = f"http://127.0.0.1:{config.daemon.port}"
    try:
        r = _local_client().post(
            f"{base_url}/session/end",
            json={"session_id": args.session_id},
        )
        r.raise_for_status()
        print("[hook:session-end] POST /session/end... OK", file=sys.stderr)
    except Exception as e:
        print(f"[hook:session-end] daemon unreachable → skipping ({e})", file=sys.stderr)


# ── main ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="bible-cc hook bridge")
    parser.add_argument(
        "action",
        choices=["session-start", "turn-user", "turn-tool", "session-end"],
    )
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--message", default=None)
    parser.add_argument("--tool", default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    try:
        config = load_config()
    except Exception as e:
        print(f"[bible-cc] WARNING: config load failed: {e}", file=sys.stderr)
        sys.exit(0)

    if args.action == "session-start":
        _handle_session_start(config, args)
    elif args.action == "turn-user":
        _handle_turn_user(config, args)
    elif args.action == "turn-tool":
        _handle_turn_tool(config, args)
    elif args.action == "session-end":
        _handle_session_end(config, args)


if __name__ == "__main__":
    main()
