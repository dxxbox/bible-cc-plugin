#!/bin/bash
# bible-cc-plugin complete uninstall — stops daemon, removes ALL local state.
# Usage: ./scripts/uninstall.sh [--force]
set -euo pipefail
cd "$(dirname "$0")/.."

FORCE="${1:-}"
DAEMON_FLAGS=""
if [ "$FORCE" = "--force" ]; then
    DAEMON_FLAGS="--force"
fi

echo "==> Stopping daemon..."
uv run python scripts/daemon.py stop $DAEMON_FLAGS 2>/dev/null || true

echo "==> Removing ~/.bible-cc/ (config + data)..."
rm -rf ~/.bible-cc/

echo "==> Removing plugin directory..."
PLUGIN_DIR="$(pwd)"
cd ~
rm -rf "$PLUGIN_DIR"

echo "==> Uninstall complete."
echo "To reinstall: git clone <repo-url> ~/.claude/plugins/bible-cc-plugin && cd ~/.claude/plugins/bible-cc-plugin && uv sync"
