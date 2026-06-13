#!/usr/bin/env python3
"""Setup wizard — interactive first-run configuration for bible-cc-plugin.

Triggered automatically by Claude Code's Setup hook, or run manually:
  uv run python -m bible_cc_plugin.scripts.setup
  uv run python -m bible_cc_plugin.scripts.setup --debug
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="bible-cc-plugin setup wizard")
    parser.add_argument("--debug", action="store_true", help="Verbose diagnostic output")
    args = parser.parse_args()
    debug = args.debug

    config_dir = Path.home() / ".bible-cc"
    config_path = config_dir / "config.json"

    if config_path.exists():
        print(f"Config already exists at {config_path}")
        print("To reconfigure, delete this file and re-run setup.")
        _show_config(config_path)
        return

    print("=== bible-cc-plugin Setup ===\n")

    base_url = _prompt("BiBLE Atlas URL", default="http://localhost:5555")
    token = _prompt("Token (optional, press Enter to skip)", default="")

    config = {
        "bible": {
            "base_url": base_url.rstrip("/"),
            "token": token if token else None,
            "kb_index": "bible-cc",
        },
        "daemon": {"port": 9777, "port_auto_fallback": False},
    }

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"\nConfig written to {config_path}")

    print(f"\nTesting BiBLE connectivity ({base_url})...", end=" ", flush=True)
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=5)
        latency = r.elapsed.total_seconds() * 1000
        if r.status_code == 200:
            print(f"OK ({latency:.0f}ms)")
        else:
            print(f"WARNING: HTTP {r.status_code}")
    except Exception as e:
        print(f"UNREACHABLE ({e})")
        if debug:
            import traceback

            traceback.print_exc()
        print("  Daemon will start but BiBLE features will be unavailable.")
        print("  Run setup again after fixing connectivity.")

    print("\nSetup complete.")
    print("Daemon will auto-start on next SessionStart.")
    print("Or start manually: uv run python -m bible_cc_plugin.scripts.daemon start")


def _prompt(text: str, default: str) -> str:
    if default:
        value = input(f"{text} [{default}]: ").strip()
        return value if value else default
    return input(f"{text}: ").strip()


def _show_config(path: Path) -> None:
    try:
        data = json.loads(path.read_text())
        bible = data.get("bible", {})
        print(f"  base_url: {bible.get('base_url', 'N/A')}")
        print(f"  token: {'<set>' if bible.get('token') else '<none>'}")
        daemon = data.get("daemon", {})
        print(f"  port: {daemon.get('port', 9777)}")
    except Exception:
        print("  (unable to read config)")


if __name__ == "__main__":
    main()
