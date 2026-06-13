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
import subprocess
import sys
import time
from pathlib import Path

import httpx

from bible_cc_plugin.config import load_config

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


def main() -> None:
    parser = argparse.ArgumentParser(description="bible-cc hook bridge")
    parser.add_argument(
        "action",
        choices=["session-start", "turn-user", "turn-tool", "session-end"],
    )
    args = parser.parse_args()

    if args.action == "session-start":
        try:
            config = load_config()
            _ensure_daemon(config.daemon.port)
        except Exception as e:
            print(f"[bible-cc] WARNING: session-start hook failed: {e}", file=sys.stderr)
        # Phase 0: no context injection yet (Phase 1 adds /context/inject).
        # Print nothing — empty inject output.

    # turn-user, turn-tool, session-end: silent pass-through (Phase 1+).


if __name__ == "__main__":
    main()
