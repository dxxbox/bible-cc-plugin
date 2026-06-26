#!/usr/bin/env python3
"""Phase 0 minimal hook bridge — daemon auto-start on SessionStart.

Design: 02-interfaces.md §2 (hook conventions). Phase 0 implements only
session-start (idempotent daemon start). Other hooks are silent pass-through
until Phase 1 (turn/user, turn/tool) and Phase 2 (session-end).

Graceful degradation: hook failures must never block Claude Code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

from bible_cc_plugin.config import load_config
from bible_cc_plugin.daemon.daemon_launcher import ensure_daemon_started
from bible_cc_plugin.logging_config import configure_logging, get_logger

_logger = get_logger("hook")

_HINT_WATCH_TTL_SECONDS = 8.0
_STOP_HINT_WAIT_SECONDS = 0.5
_HINT_POLL_INTERVAL_SECONDS = 0.25

def _resolve_log_path(config) -> Path:
    """Return the expanded log file path from config."""
    return Path(config.logging.file).expanduser()


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


# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_daemon_error_body(response: httpx.Response) -> tuple[str, str]:
    """Extract (code, message) from daemon error response.

    Supports multiple formats in priority order:
    1. daemon envelope: {"error": {"code": "...", "message": "..."}}
    2. FastAPI default:  {"detail": "..."}
    3. Generic:          {"message": "..."}
    4. Fallback:         raw response.text[:200]
    """
    try:
        body = response.json()
        if isinstance(body, dict):
            err = body.get("error", {})
            if isinstance(err, dict) and err.get("message"):
                return (str(err.get("code", "")), str(err["message"]))
            if "detail" in body:
                return ("", str(body["detail"]))
            if "message" in body:
                return ("", str(body["message"]))
        # JSON parsed but no known message key — use raw JSON string
        text = response.text.strip()[:200]
    except Exception:
        text = (response.text or "").strip()[:200]
    return ("", text)


# ── Phase 2a action handlers ──────────────────────────────────────────────


def _handle_session_start(config, args) -> None:
    """三步流程：ensure daemon → register session → inject context → stdout.

    Daemon start is session-agnostic — it runs even when session_id is
    absent (e.g. startup event fires before a session exists).
    """
    # 1. Ensure daemon running（session-agnostic, must run first）
    ok = ensure_daemon_started(config.daemon.port, _resolve_log_path(config))
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


def _hint_cursor_path(session_id: str) -> Path:
    """Return the path to the per-session hint cursor file."""
    return Path.home() / ".bible-cc" / f".hint_cursor_{session_id}"


def _read_hint_cursor(session_id: str) -> int:
    """Return the last hinted moment id, or 0 if none."""
    try:
        return int(_hint_cursor_path(session_id).read_text().strip())
    except Exception:
        return 0


def _write_hint_cursor(session_id: str, moment_id: int) -> None:
    """Persist the last hinted moment id."""
    try:
        _write_text_atomic(_hint_cursor_path(session_id), str(moment_id))
    except Exception:
        pass


def _write_text_atomic(path: Path, text: str) -> None:
    """Atomically write a small state file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(text)
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _safe_session_key(session_id: str) -> str:
    """Derive a filesystem-safe key for best-effort hook state files."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _hint_watch_path(session_id: str) -> Path:
    """Return the path to the per-session queued-detection watch file."""
    return Path.home() / ".bible-cc" / f".hint_watch_{_safe_session_key(session_id)}"


def _write_hint_watch(session_id: str) -> None:
    """Remember that a queued detection may produce a hint shortly."""
    try:
        p = _hint_watch_path(session_id)
        _write_text_atomic(
            p,
            json.dumps(
                {
                    "cursor": _read_hint_cursor(session_id),
                    "expires_at": time.time() + _HINT_WATCH_TTL_SECONDS,
                }
            )
        )
    except Exception:
        pass


def _read_hint_watch(session_id: str) -> dict | None:
    """Return active queued-detection watch metadata, or None."""
    p = _hint_watch_path(session_id)
    try:
        data = json.loads(p.read_text())
        expires_at = float(data.get("expires_at", 0))
        if expires_at <= time.time():
            _clear_hint_watch(session_id)
            return None
        return {"cursor": int(data.get("cursor", 0)), "expires_at": expires_at}
    except FileNotFoundError:
        return None
    except Exception:
        _logger.debug("invalid hint watch state: %s", p, exc_info=True)
        _clear_hint_watch(session_id)
        return None


def _clear_hint_watch(session_id: str) -> None:
    """Remove queued-detection watch metadata."""
    try:
        _hint_watch_path(session_id).unlink(missing_ok=True)
    except Exception:
        pass


def _print_hints(
    session_id: str,
    base_url: str,
    hint_format: str,
    timeout: float = 1.0,
    wait_seconds: float = 0.0,
    poll_interval: float = _HINT_POLL_INTERVAL_SECONDS,
) -> int:
    """Fetch moments → format_hint → stdout (best-effort).

    Only prints moments with id > last hinted cursor, then updates cursor.
    Prevents the same moment being hinted on every turn.
    Per-moment errors are isolated — one bad moment won't block others.
    GET / JSON failures are caught at the outer level and logged.

    When wait_seconds > 0, poll briefly for a late async detection result.
    """
    deadline = time.monotonic() + max(0.0, wait_seconds)
    moments = []
    while True:
        try:
            r = _local_client(timeout=timeout).get(
                f"{base_url}/daemon/moments",
                params={"session_id": session_id},
            )
            r.raise_for_status()
            moments = r.json().get("moments", [])
        except Exception:
            _logger.warning("_print_hints: GET /daemon/moments failed", exc_info=True)
            return 0

        cursor = _read_hint_cursor(session_id)
        if any(m.get("id", 0) > cursor for m in moments):
            break
        if time.monotonic() >= deadline:
            return 0
        time.sleep(max(0.0, poll_interval))

    if not moments:
        return 0

    cursor = _read_hint_cursor(session_id)
    from bible_cc_plugin.daemon.detector import MomentCandidate
    from bible_cc_plugin.hint_system import format_hint

    max_id = cursor
    printed = 0
    for m in moments:
        mid = m.get("id", 0)
        if mid <= cursor:
            continue
        try:
            candidate = MomentCandidate(
                type=str(m.get("moment_type") or m.get("type") or ""),
                title=str(m.get("title") or ""),
                narrative=str(m.get("narrative") or ""),
                tool_summary=str(m.get("tool_summary") or ""),
            )
            hint = format_hint(candidate, hint_format)
            print(hint, flush=True)
            printed += 1
            if mid > max_id:
                max_id = mid
        except Exception:
            _logger.warning("_print_hints: format failed for moment", exc_info=True)

    if max_id > cursor:
        _write_hint_cursor(session_id, max_id)
    return printed


def _should_poll_hints(config) -> bool:
    """Return whether hook handlers should poll pending moment hints."""
    return bool(config.capture.enabled and config.capture.mid_session_detection)


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
        if _should_poll_hints(config):
            printed = _print_hints(args.session_id, base_url, config.capture.hint_format)
            if body.get("queued") and printed == 0:
                _write_hint_watch(args.session_id)
    except httpx.HTTPStatusError as e:
        code, msg = _parse_daemon_error_body(e.response)
        _logger.warning(
            "turn-user daemon returned %d code=%s message=%r → skipping",
            e.response.status_code, code, msg,
        )
        # Self-healing: if session was never registered, recover and retry once.
        if e.response.status_code == 400 and "session not found" in msg.lower():
            _logger.info(
                "turn-user attempting session recovery via /session/start for %s",
                args.session_id[:8],
            )
            try:
                rr = _local_client().post(
                    f"{base_url}/session/start",
                    json={"session_id": args.session_id},
                )
                rr.raise_for_status()
                _logger.info("turn-user recovery OK — retrying current turn")
                r2 = _local_client().post(
                    f"{base_url}/turn/user",
                    json={"session_id": args.session_id, "message": args.message or ""},
                )
                r2.raise_for_status()
                body2 = r2.json()
                sid2 = args.session_id[:8]
                mlen2 = len(args.message or "")
                tid2 = body2.get("turn_id")
                _logger.info(
                    "turn-user %s msg_len=%d → OK turn_id=%s (recovered)",
                    sid2, mlen2, tid2,
                )
                if _should_poll_hints(config):
                    printed = _print_hints(
                        args.session_id, base_url, config.capture.hint_format
                    )
                    if body2.get("queued") and printed == 0:
                        _write_hint_watch(args.session_id)
            except Exception as recovery_err:
                _logger.warning(
                    "turn-user recovery failed: %s — turn skipped", recovery_err,
                )
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
        body = r.json()
        olen = len(args.output or "")
        sid = args.session_id[:8]
        cmd = arguments.get("command", "")[:80] if args.tool == "Bash" else ""
        _logger.info("turn-tool %s %s %s out=%d → OK", sid, args.tool, cmd, olen)
        if _should_poll_hints(config):
            printed = _print_hints(args.session_id, base_url, config.capture.hint_format)
            if body.get("queued") and printed == 0:
                _write_hint_watch(args.session_id)
    except httpx.HTTPStatusError as e:
        code, msg = _parse_daemon_error_body(e.response)
        _logger.warning(
            "turn-tool daemon returned %d code=%s message=%r → skipping",
            e.response.status_code, code, msg,
        )
        # Self-healing: if session was never registered, recover and retry once.
        if e.response.status_code == 400 and "session not found" in msg.lower():
            _logger.info(
                "turn-tool attempting session recovery via /session/start for %s",
                args.session_id[:8],
            )
            try:
                rr = _local_client().post(
                    f"{base_url}/session/start",
                    json={"session_id": args.session_id},
                )
                rr.raise_for_status()
                _logger.info("turn-tool recovery OK — retrying current turn")
                r2 = _local_client().post(
                    f"{base_url}/turn/tool",
                    json={
                        "session_id": args.session_id,
                        "tool_name": args.tool,
                        "arguments": arguments,
                        "output": args.output or "",
                    },
                )
                r2.raise_for_status()
                body2 = r2.json()
                olen2 = len(args.output or "")
                sid2 = args.session_id[:8]
                cmd2 = arguments.get("command", "")[:80] if args.tool == "Bash" else ""
                _logger.info(
                    "turn-tool %s %s %s out=%d → OK (recovered)",
                    sid2, args.tool, cmd2, olen2,
                )
                if _should_poll_hints(config):
                    printed = _print_hints(
                        args.session_id, base_url, config.capture.hint_format
                    )
                    if body2.get("queued") and printed == 0:
                        _write_hint_watch(args.session_id)
            except Exception as recovery_err:
                _logger.warning(
                    "turn-tool recovery failed: %s — turn skipped", recovery_err,
                )
    except Exception as e:
        _logger.warning("turn-tool daemon unreachable → skipping (%s)", e)


def _handle_turn_stop(config, args) -> None:
    """Stop hook handler — poll for hints after assistant responses.

    Claude Code Stop fires after every assistant response ("once per turn").
    Detection runs asynchronously after user/tool hooks, so Stop is a useful
    low-cost place to surface any moments that finished in the background.
    """
    if not args.session_id:
        _logger.warning("turn-stop missing --session-id")
        return
    if not _should_poll_hints(config):
        _logger.debug("turn-stop %s → hint polling disabled", args.session_id[:8])
        return
    base_url = f"http://127.0.0.1:{config.daemon.port}"
    watch = _read_hint_watch(args.session_id)
    if watch is not None and _read_hint_cursor(args.session_id) > watch["cursor"]:
        _clear_hint_watch(args.session_id)
        watch = None
    wait_seconds = 0.0
    if watch is not None:
        wait_seconds = min(
            _STOP_HINT_WAIT_SECONDS,
            max(0.0, watch["expires_at"] - time.time()),
        )
    printed = _print_hints(
        args.session_id,
        base_url,
        config.capture.hint_format,
        wait_seconds=wait_seconds,
    )
    if watch is not None:
        if printed > 0 or _read_hint_cursor(args.session_id) > watch["cursor"]:
            _clear_hint_watch(args.session_id)
        elif watch["expires_at"] <= time.time():
            _clear_hint_watch(args.session_id)
    _logger.info("turn-stop %s → hints polled", args.session_id[:8])


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
    except httpx.HTTPStatusError as e:
        code, msg = _parse_daemon_error_body(e.response)
        if e.response.status_code == 404 and "session not found" in msg.lower():
            _logger.warning(
                "session-end failed: session %s was never registered "
                "(daemon may have restarted after SessionStart or session may "
                "have been lost)",
                args.session_id,
            )
        else:
            _logger.warning(
                "session-end failed: HTTP %d — %s",
                e.response.status_code,
                msg or "(no detail)",
            )
    except httpx.RequestError:
        # httpx.RequestError covers ConnectError, TimeoutException,
        # NetworkError, etc. — all mean daemon is truly unreachable.
        _logger.warning("session-end daemon unreachable → skipping")
    except Exception as e:
        _logger.warning("session-end failed: %s", e)


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

    _logger.debug(
        "hook=%s stdin_sid=%s cli_sid=%s merged_sid=%s msg_len=%d stdin_keys=%s",
        args.action,
        stdin_data.get("session_id", "<none>") or "<empty>",
        args.session_id or "<none>",
        session_id or "<empty>",
        len(message or ""),
        sorted(stdin_data.keys()) if stdin_data else "[]",
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
