"""Daemon launcher — shared subprocess spawning logic.

Used by both hook.py (SessionStart) and daemon.py (CLI start/restart).
Extracted per Phase 0 lesson #2: don't duplicate subprocess spawning.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx


def _local_client(timeout: int = 2) -> httpx.Client:
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


def ensure_daemon_started(
    port: int,
    log_path: Path,
    poll_timeout: float = 5.0,
) -> bool:
    """Idempotent: ensure daemon is running on the given port.

    1. GET /daemon/health → 200? → return True（already running）
    2. Spawn uvicorn（stdout/stderr → log_path）
    3. Poll health check（up to poll_timeout seconds）
    4. Success → return True
    5. Timeout → tail last 20 log lines → print to stderr → return False

    Returns:
        True if daemon is running (was already, or started successfully).
        False if daemon could not be started.
    """
    base_url = f"http://127.0.0.1:{port}"

    # Already running?
    try:
        r = _local_client(timeout=2).get(f"{base_url}/daemon/health")
        if r.status_code == 200:
            return True
    except Exception:
        pass

    # Ensure log directory exists
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Spawn uvicorn
    log_fh = open(str(log_path), "a")
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
        return False

    # Poll health check
    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        ok = False
        try:
            r = _local_client(timeout=1).get(f"{base_url}/daemon/health")
            ok = r.status_code == 200
        except httpx.ConnectError:
            pass  # daemon not ready yet — expected
        except Exception:
            print(f"[bible-cc] WARNING: health check error: {sys.exc_info()[1]}", file=sys.stderr)
        if ok:
            log_fh.close()
            return True
        time.sleep(0.3)

    # Timeout — show log tail for diagnosis
    log_fh.close()
    tail = _tail_log(log_path)
    print("[bible-cc] WARNING: daemon health check timed out", file=sys.stderr)
    if tail:
        print(f"[bible-cc] Last 20 lines of {log_path}:", file=sys.stderr)
        print(tail, file=sys.stderr)
    else:
        print(f"[bible-cc] (no log output at {log_path})", file=sys.stderr)

    return False
