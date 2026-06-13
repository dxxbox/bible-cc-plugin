#!/bin/bash
# bible-cc-plugin development helper
# Usage: ./scripts/dev.sh {init|test|lint|ci|reload|restart}
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-}" in
  init)
    echo "==> Installing dependencies..."
    uv sync
    echo "==> Running first-time setup..."
    uv run python scripts/setup.py
    echo "==> Init complete."
    ;;
  test) shift; uv run pytest "$@" tests/ ;;
  lint) shift; uv run ruff check "$@"; uv run ruff format --check "$@" ;;
  ci)
    echo "==> Lint..."; uv run ruff check; uv run ruff format --check
    echo "==> Unit tests..."; uv run pytest tests/unit/ -v
    echo "==> Contract tests..."; uv run pytest tests/contract/ -v
    echo "==> CI PASSED"
    ;;
  reload)
    uv run python scripts/daemon.py stop 2>/dev/null || true
    echo "Daemon will restart on next SessionStart."
    ;;
  restart)
    uv run python scripts/daemon.py stop 2>/dev/null || true
    sleep 1
    uv run python scripts/daemon.py start
    ;;
  *) echo "Usage: $0 {init|test|lint|ci|reload|restart}"; exit 1 ;;
esac
