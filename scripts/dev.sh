#!/bin/bash
# bible-cc-plugin development helper
# Usage: ./scripts/dev.sh {init|test|lint|ci|reload|restart}
set -euo pipefail
cd "$(dirname "$0")/.."

# ── Helpers ──────────────────────────────────────────────
_generate_mcp_json() {
    local config="$HOME/.bible-cc/config.json"
    if [ -f "$config" ]; then
        uv run python -c "
import json, os
c = json.load(open(os.path.expanduser('~/.bible-cc/config.json')))
b = c['bible']
mcp = {
    'mcpServers': {
        'bible-cc': {
            'command': 'uv',
            'args': ['run', 'python', '-m', 'bible_cc_plugin.mcp.server'],
            'env': {
                'BIBLE_ATLAS_BASE_URL': b['base_url'],
                'BIBLE_ATLAS_TOKEN': b.get('token') or '',
            }
        }
    }
}
open('.mcp.json', 'w').write(json.dumps(mcp, indent=2) + '\n')
"
        echo "  .mcp.json generated"
    else
        echo "  WARNING: ~/.bible-cc/config.json not found, skipping .mcp.json"
    fi
}

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
    _generate_mcp_json
    echo "==> Lint..."; uv run ruff check; uv run ruff format --check
    echo "==> Unit tests..."; uv run pytest tests/unit/ -v
    echo "==> Contract tests..."; uv run pytest tests/contract/ -v
    echo "==> CI PASSED"
    rm -f .mcp.json
    echo "  .mcp.json cleaned up"
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
