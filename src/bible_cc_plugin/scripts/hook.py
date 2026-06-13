#!/usr/bin/env python3
"""Phase 0 minimal hook bridge — daemon auto-start on SessionStart.

Design: 02-interfaces.md §2 (hook conventions). Phase 0 implements only
session-start (idempotent daemon start). Other hooks are silent pass-through
until Phase 1 (turn/user, turn/tool) and Phase 2 (session-end).

Graceful degradation: hook failures must never block Claude Code.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

import httpx

from bible_cc_plugin.config import load_config


def _local_client(timeout: int = 5) -> httpx.Client:
    """httpx client that bypasses proxy for 127.0.0.1."""
    return httpx.Client(trust_env=False, timeout=timeout)


def _ensure_daemon(port: int) -> None:
    """Idempotent: start daemon if not already running.

    Checks health endpoint first. If unreachable, spawns uvicorn and
    waits up to 5s for health check. Graceful degradation: failure
    prints warning to stderr but does NOT exit non-zero.
    """
    base_url = f"http://127.0.0.1:{port}"
    try:
        r = _local_client(timeout=2).get(f"{base_url}/daemon/health")
        if r.status_code == 200:
            return  # already running
    except Exception:
        pass

    # Start uvicorn in background
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
                "warning",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[bible-cc] WARNING: daemon start failed: {e}", file=sys.stderr)
        return

    # Wait for health check
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            r = _local_client(timeout=1).get(f"{base_url}/daemon/health")
            if r.status_code == 200:
                return  # started successfully
        except Exception:
            pass
        time.sleep(0.3)

    print("[bible-cc] WARNING: daemon health check timed out", file=sys.stderr)


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
