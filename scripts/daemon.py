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

import httpx

from bible_cc_plugin.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="bible-cc daemon lifecycle")
    parser.add_argument("action", choices=["start", "stop", "status", "restart"])
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    config = load_config(debug=args.debug)
    port = config.daemon.port
    base_url = f"http://127.0.0.1:{port}"

    if args.action == "start":
        _do_start(port, debug=args.debug)
    elif args.action == "stop":
        _do_stop(base_url)
    elif args.action == "status":
        _do_status(base_url)
    elif args.action == "restart":
        _do_stop(base_url)
        time.sleep(1)
        _do_start(port, debug=args.debug)


def _do_start(port: int, *, debug: bool) -> None:
    base_url = f"http://127.0.0.1:{port}"
    try:
        r = httpx.get(f"{base_url}/daemon/health", timeout=2)
        if r.status_code == 200:
            data = r.json()
            print(f"Daemon already running (pid={data['pid']}, port={port})")
            return
    except Exception:
        pass

    log_level = "debug" if debug else "warning"
    cmd = [
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
    ]
    print(f"[daemon] Starting on 127.0.0.1:{port}...", end=" ", flush=True)
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=None if debug else subprocess.DEVNULL,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/daemon/health", timeout=1)
            if r.status_code == 200:
                data = r.json()
                print(f"OK (pid={data['pid']}, port={port})")
                return
        except Exception:
            pass
        time.sleep(0.3)
    print("FAILED (health check timed out)")
    sys.exit(1)


def _do_stop(base_url: str) -> None:
    try:
        r = httpx.post(f"{base_url}/daemon/stop", timeout=5)
        if r.status_code == 200:
            print("Daemon stopped.")
            return
    except Exception:
        pass
    print("Daemon is not running.")


def _do_status(base_url: str) -> None:
    try:
        r = httpx.get(f"{base_url}/daemon/health", timeout=2)
        if r.status_code == 200:
            data = r.json()
            uptime_m = data["uptime"] // 60
            uptime_s = data["uptime"] % 60
            print("Daemon: running")
            print(f"  PID:    {data['pid']}")
            print(f"  Port:   {data['port']}")
            print(f"  Uptime: {uptime_m}m {uptime_s}s")
            return
    except Exception:
        pass
    print("Daemon: not running")


if __name__ == "__main__":
    main()
