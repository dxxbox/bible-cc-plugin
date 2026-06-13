#!/usr/bin/env python3
"""Setup wizard — interactive first-run configuration for bible-cc-plugin.

Modes:
  uv run python -m bible_cc_plugin.scripts.setup           # interactive
  uv run python -m bible_cc_plugin.scripts.setup --reset   # delete config, then interactive
  uv run python -m bible_cc_plugin.scripts.setup --non-interactive  # defaults, no prompts
  uv run python -m bible_cc_plugin.scripts.setup --non-interactive --base-url <URL> --token <TOKEN>
  uv run python -m bible_cc_plugin.scripts.setup --debug   # verbose diagnostics

--reset requires confirmation (or --force to skip).
--non-interactive + --reset requires --force (prompts are impossible without a tty).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="bible-cc-plugin setup wizard")
    parser.add_argument("--debug", action="store_true", help="Verbose diagnostic output")
    parser.add_argument("--reset", action="store_true", help="Delete existing config before setup")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip prompts, use defaults or --base-url/--token/env vars",
    )
    parser.add_argument("--base-url", help="BiBLE Atlas URL (non-interactive mode)")
    parser.add_argument("--token", help="BiBLE Atlas token (non-interactive mode)")
    parser.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts (for --reset)"
    )
    args = parser.parse_args()
    debug = args.debug

    config_dir = Path.home() / ".bible-cc"
    config_path = config_dir / "config.json"

    # --reset: delete everything and start fresh
    if args.reset:
        _do_reset(config_dir, force=args.force, non_interactive=args.non_interactive)

    # --non-interactive: skip prompts
    if args.non_interactive:
        _do_non_interactive(config_dir, config_path, args, debug=debug)
        return

    # Interactive mode: skip if already configured
    if config_path.exists():
        print(f"Config already exists at {config_path}")
        print("To reconfigure, use --reset. To skip prompts, use --non-interactive.")
        _show_config(config_path)
        return

    print("=== bible-cc-plugin Setup ===\n")

    base_url = _prompt("BiBLE Atlas URL", default="http://localhost:5555")
    token = _prompt("Token (optional, press Enter to skip)", default="")

    _write_and_test(base_url, token, config_dir, config_path, debug=debug)


def _do_non_interactive(config_dir: Path, config_path: Path, args, *, debug: bool) -> None:
    """Non-interactive setup: resolve values from args → env → defaults."""
    base_url = args.base_url or os.getenv("BIBLE_ATLAS_BASE_URL") or "http://localhost:5555"
    token = args.token or os.getenv("BIBLE_ATLAS_TOKEN") or ""

    print("=== bible-cc-plugin Setup (non-interactive) ===")
    print(f"  base_url: {base_url}")
    print(f"  token: {'<set>' if token else '<none>'}")

    _write_and_test(base_url, token, config_dir, config_path, debug=debug)


def _do_reset(config_dir: Path, *, force: bool = False, non_interactive: bool = False) -> None:
    """Remove existing config and data directory — with confirmation."""
    if not config_dir.exists():
        return

    # ── Confirmation ─────────────────────────────────────
    if not force:
        if non_interactive:
            print(
                "Error: --reset in non-interactive mode requires --force.\n"
                "  This operation will DELETE: ~/.bible-cc/ (all config + data)\n"
                "  Re-run with --force if you are sure.",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            print(f"This will DELETE: {config_dir}/ (all config + local data)")
            answer = input("Continue? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                sys.exit(0)

    print(f"Removing {config_dir}...")
    shutil.rmtree(config_dir)
    # Also kill any running daemon on default port
    _kill_daemon_if_running()


def _kill_daemon_if_running() -> None:
    """Try to stop a running daemon via HTTP, then force-kill if needed."""
    import subprocess

    try:
        r = httpx.Client(trust_env=False, timeout=3).post("http://127.0.0.1:9777/daemon/stop")
        if r.status_code == 200:
            print("Daemon stopped gracefully.")
            return
    except Exception:
        pass

    # Fallback: find by port
    try:
        result = subprocess.run(["lsof", "-ti", ":9777"], capture_output=True, text=True)
        pids = result.stdout.strip().split("\n")
        for pid in pids:
            if pid:
                subprocess.run(["kill", "-9", pid])
                print(f"Daemon force-killed (pid={pid}).")
    except Exception:
        pass


def _write_mcp_json(base_url: str, token: str) -> None:
    """Generate .mcp.json in project root so Claude Code discovers the MCP server.

    Uses __file__ location to resolve the project root (scripts/setup.py → project root).
    """
    project_root = Path(__file__).resolve().parent.parent
    mcp = {
        "mcpServers": {
            "bible-cc": {
                "command": "uv",
                "args": ["run", "python", "-m", "bible_cc_plugin.mcp.server"],
                "env": {
                    "BIBLE_ATLAS_BASE_URL": base_url.rstrip("/"),
                    "BIBLE_ATLAS_TOKEN": token if token else "",
                },
            }
        }
    }
    mcp_path = project_root / ".mcp.json"
    mcp_path.write_text(json.dumps(mcp, indent=2) + "\n")
    print(f"  .mcp.json written to {mcp_path}")


def _write_and_test(
    base_url: str,
    token: str,
    config_dir: Path,
    config_path: Path,
    *,
    debug: bool,
) -> None:
    """Write config.json and test BiBLE connectivity."""
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

    # Generate .mcp.json in project root (for Claude Code MCP discovery)
    _write_mcp_json(base_url, token)

    # Test connectivity
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

    print("\nSetup complete.")
    print("Daemon will auto-start on next SessionStart.")
    print("Or start manually: uv run python scripts/daemon.py start")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
