#!/usr/bin/env python3
"""Daemon lifecycle CLI — start, stop, status, restart.

Usage:
  uv run python -m bible_cc_plugin.scripts.daemon start [--debug]
  uv run python -m bible_cc_plugin.scripts.daemon stop
  uv run python -m bible_cc_plugin.scripts.daemon status
  uv run python -m bible_cc_plugin.scripts.daemon restart [--debug]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import httpx

from bible_cc_plugin.config import load_config
from bible_cc_plugin.logging_config import configure_logging, get_logger

_logger = get_logger("daemon")

_DAEMON_LOG = Path.home() / ".bible-cc" / "daemon.log"


def _local_client(timeout: int = 5) -> httpx.Client:
    """httpx client that bypasses proxy for 127.0.0.1 (trust_env=False)."""
    return httpx.Client(trust_env=False, timeout=timeout)


def _tail_log(path: Path, lines: int = 20) -> str:
    """Return the last N lines of a file, or empty string if file missing."""
    try:
        content = path.read_text()
        all_lines = content.splitlines()
        return "\n".join(all_lines[-lines:])
    except (OSError, FileNotFoundError):
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="bible-cc daemon lifecycle")
    parser.add_argument("action", choices=["start", "stop", "status", "restart"])
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--force", action="store_true", help="Force-kill daemon if graceful stop fails"
    )
    args = parser.parse_args()

    config = load_config(debug=args.debug)
    configure_logging(**config.logging.model_dump())
    port = config.daemon.port
    base_url = f"http://127.0.0.1:{port}"

    if args.action == "start":
        _do_start(port, debug=args.debug)
    elif args.action == "stop":
        _do_stop(base_url, force=args.force)
    elif args.action == "status":
        _do_status(base_url)
    elif args.action == "restart":
        _do_stop(base_url)
        time.sleep(1)
        _do_start(port, debug=args.debug)


def _do_start(port: int, *, debug: bool) -> None:
    base_url = f"http://127.0.0.1:{port}"
    try:
        r = _local_client(timeout=2).get(f"{base_url}/daemon/health")
        if r.status_code == 200:
            data = r.json()
            _logger.info("Daemon already running (pid=%s, port=%d)", data["pid"], port)
            return
    except Exception:
        pass

    _DAEMON_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(str(_DAEMON_LOG), "a")

    log_level = "debug" if debug else "info"
    _logger.info("Starting on 127.0.0.1:%d...", port)
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
            log_level,
        ],
        stdout=log_fh,
        stderr=log_fh,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        ok = False
        try:
            r = _local_client(timeout=1).get(f"{base_url}/daemon/health")
            ok = r.status_code == 200
        except httpx.ConnectError:
            _logger.debug("daemon not ready (connection refused)")
        except Exception:
            _logger.warning("health check error: %s", sys.exc_info()[1])
        if ok:
            log_fh.close()
            data = r.json()
            _logger.info("OK (pid=%s, port=%d)", data["pid"], port)
            return
        time.sleep(0.3)

    log_fh.close()
    tail = _tail_log(_DAEMON_LOG)
    _logger.error("FAILED (health check timed out)")
    if tail:
        _logger.error("Last 20 lines of %s:\n%s", _DAEMON_LOG, tail)
    sys.exit(1)


def _do_stop(base_url: str, *, force: bool = False) -> None:
    try:
        r = _local_client(timeout=5).post(f"{base_url}/daemon/stop")
        if r.status_code == 200:
            _logger.info("Daemon stopped.")
            return
    except Exception as e:
        if force:
            pass  # graceful stop failed — falling through to force-kill
        else:
            _logger.warning("Daemon is not running (stop failed: %s)", e)
            return

    if force:
        import subprocess

        port = int(base_url.rsplit(":", 1)[1]) if ":" in base_url else 9777
        try:
            result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
            for pid in result.stdout.strip().split("\n"):
                if pid:
                    subprocess.run(["kill", "-9", pid])
                    _logger.warning("Daemon force-killed (pid=%s, port=%d).", pid, port)
                    return
        except Exception:
            pass

    _logger.info("Daemon is not running.")


def _do_status(base_url: str) -> None:
    try:
        r = _local_client(timeout=2).get(f"{base_url}/daemon/health")
        if r.status_code == 200:
            data = r.json()
            uptime_m = data["uptime"] // 60
            uptime_s = data["uptime"] % 60
            _logger.info(
                "Daemon: running (PID=%s, Port=%s, Uptime=%dm%ds)",
                data["pid"],
                data["port"],
                uptime_m,
                uptime_s,
            )
            return
    except Exception as e:
        _logger.info("Daemon: not running (%s)", e)
        return
    _logger.info("Daemon: not running (HTTP %d)", r.status_code)


if __name__ == "__main__":
    main()
