#!/bin/bash
# bible-cc-plugin install verification — confirms everything works after install+setup.
# Usage: ./scripts/verify-install.sh
# Exit 0 = all checks passed. Exit 1 = one or more checks failed.
set -euo pipefail
cd "$(dirname "$0")/.."

FAILED=0
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

check() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo -e "  [${GREEN}PASS${NC}] $label"
    else
        echo -e "  [${RED}FAIL${NC}] $label"
        FAILED=$((FAILED + 1))
    fi
}

echo "=== bible-cc-plugin Install Verification ==="
echo ""

echo "--- Basic environment ---"
check "uv is installed" uv --version
check "Python >= 3.10" python3 -c "import sys; assert sys.version_info >= (3,10)"
check "pyproject.toml exists" test -f pyproject.toml
check ".venv exists" test -d .venv

echo ""
echo "--- Config ---"
# Auto-setup if config doesn't exist (supports fresh install testing)
if [ ! -f ~/.bible-cc/config.json ]; then
    echo "  (config.json not found — running setup --non-interactive)"
    uv run python scripts/setup.py --non-interactive 2>&1 | sed 's/^/  | /'
fi
check "config.json exists" test -f ~/.bible-cc/config.json
check "config.json is valid JSON" python3 -c "import json; json.load(open('$HOME/.bible-cc/config.json'))"

echo ""
echo "--- Daemon lifecycle ---"
uv run python scripts/daemon.py stop --force 2>/dev/null || true
sleep 1

check "daemon start" uv run python scripts/daemon.py start
check "daemon status shows running" bash -c "uv run python scripts/daemon.py status | grep -q running"
check "health endpoint responds" curl -sf http://127.0.0.1:9777/daemon/health >/dev/null
check "health PID > 0" test "$(curl -s http://127.0.0.1:9777/daemon/health | python3 -c 'import sys,json; print(json.load(sys.stdin)["pid"])')" -gt 0
check "daemon stop" uv run python scripts/daemon.py stop
sleep 1  # daemon does async shutdown (0.5s delay)
check "daemon not running after stop" bash -c "! curl -sf http://127.0.0.1:9777/daemon/health"

echo ""
echo "--- Code quality ---"
check "ruff lint passes" uv run ruff check
check "ruff format passes" uv run ruff format --check

echo ""
echo "--- Tests ---"
check "unit tests pass" uv run pytest tests/unit/ -q
check "contract tests pass" uv run pytest tests/contract/ -q

echo ""
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}=== All checks passed ===${NC}"
else
    echo -e "${RED}=== $FAILED check(s) failed ===${NC}"
fi
exit $FAILED
