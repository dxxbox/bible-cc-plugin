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


def _local_client(timeout: int = 5) -> httpx.Client:
    """httpx client that bypasses proxy for 127.0.0.1 (trust_env=False)."""
    return httpx.Client(trust_env=False, timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="bible-cc daemon lifecycle")
    parser.add_argument("action", choices=["start", "stop", "status", "restart"])
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--force", action="store_true", help="Force-kill daemon if graceful stop fails"
    )
    args = parser.parse_args()

    config = load_config(debug=args.debug)
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
            r = _local_client(timeout=1).get(f"{base_url}/daemon/health")
            if r.status_code == 200:
                data = r.json()
                print(f"OK (pid={data['pid']}, port={port})")
                return
        except Exception:
            pass
        time.sleep(0.3)
    print("FAILED (health check timed out)")
    sys.exit(1)


def _do_stop(base_url: str, *, force: bool = False) -> None:
    try:
        r = _local_client(timeout=5).post(f"{base_url}/daemon/stop")
        if r.status_code == 200:
            print("Daemon stopped.")
            return
    except Exception as e:
        if force:
            pass  # graceful stop failed — falling through to force-kill
        else:
            print(f"Daemon is not running (stop failed: {e})")
            return

    if force:
        import subprocess

        port = int(base_url.rsplit(":", 1)[1]) if ":" in base_url else 9777
        try:
            result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
            for pid in result.stdout.strip().split("\n"):
                if pid:
                    subprocess.run(["kill", "-9", pid])
                    print(f"Daemon force-killed (pid={pid}, port={port}).")
                    return
        except Exception:
            pass

    print("Daemon is not running.")


def _do_status(base_url: str) -> None:
    try:
        r = _local_client(timeout=2).get(f"{base_url}/daemon/health")
        if r.status_code == 200:
            data = r.json()
            uptime_m = data["uptime"] // 60
            uptime_s = data["uptime"] % 60
            print("Daemon: running")
            print(f"  PID:    {data['pid']}")
            print(f"  Port:   {data['port']}")
            print(f"  Uptime: {uptime_m}m {uptime_s}s")
            return
    except Exception as e:
        print(f"Daemon: not running ({e})")
        return
    print(f"Daemon: not running (HTTP {r.status_code})")


if __name__ == "__main__":
    main()
