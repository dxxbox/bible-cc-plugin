#!/bin/bash
# bible-cc-plugin complete uninstall — stops daemon, removes ALL local state.
# Usage: ./scripts/uninstall.sh [--force]
#
# SAFETY: This script targets ~/.bible-cc/ and standard plugin install paths
# only. It NEVER deletes the current working directory. The plugin directory is
# only removed when it matches a known install location AND contains plugin.json.
set -euo pipefail

FORCE="${1:-}"
DAEMON_FLAGS=""
if [ "$FORCE" = "--force" ]; then
    DAEMON_FLAGS="--force"
fi

echo "==> Stopping daemon..."
cd "$(dirname "$0")/.."
uv run python scripts/daemon.py stop $DAEMON_FLAGS 2>/dev/null || true

echo "==> Removing ~/.bible-cc/ (config + data)..."
rm -rf ~/.bible-cc/

# ── Plugin directory removal ──────────────────────────────
# Only remove from STANDARD install locations — NEVER use pwd.
# If the plugin was cloned elsewhere, the user must remove it manually.
STANDARD_PATHS=(
    "$HOME/.claude/plugins/bible-cc-plugin"
    "$HOME/.claude-plugins/bible-cc-plugin"
)
REMOVED=0
for PLUGIN_DIR in "${STANDARD_PATHS[@]}"; do
    if [ -d "$PLUGIN_DIR" ]; then
        # Safety check: directory must contain plugin.json to confirm it's us
        if [ -f "$PLUGIN_DIR/plugin.json" ]; then
            echo "==> Removing plugin directory: $PLUGIN_DIR"
            cd ~
            rm -rf "$PLUGIN_DIR"
            REMOVED=1
        else
            echo "!! Skipping $PLUGIN_DIR (exists but missing plugin.json — not a bible-cc install)"
        fi
    fi
done

if [ $REMOVED -eq 0 ]; then
    echo "==> No standard plugin directory found to remove."
    echo "    If installed elsewhere, remove manually."
fi

echo "==> Uninstall complete."
echo "To reinstall: git clone <repo-url> ~/.claude/plugins/bible-cc-plugin && cd ~/.claude/plugins/bible-cc-plugin && uv sync"
