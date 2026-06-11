# bible-cc-plugin Validation Report

**Date**: 2026-06-11
**Overall**: PASS with issues

---

## Summary

The plugin has a solid architecture and clean implementation. All 18 tests pass. Three-component design (daemon + MCP server + hooks) is well separated. Core functionality works correctly. 2 critical, 2 major, 3 minor issues found — primarily in configuration files and deploy script, not in application logic.

---

## Critical Issues

### 1. MCP env variable uses shell-default syntax
**File**: `.mcp.json`

The `env` block uses `${BIBLE_ATLAS_BASE_URL:-http://localhost:5555}` which is not standard MCP configuration format. The MCP server process receives the literal string rather than resolving to `http://localhost:5555`. MCP server silently fails unless `BIBLE_ATLAS_BASE_URL` is set in the parent environment.

**Fix**: Replace with `"BIBLE_ATLAS_BASE_URL": "http://localhost:5555"` or rely on daemon-level config resolution.

### 2. deploy.sh --setup calls wrong entry point
**File**: `deploy.sh` (line 138)

`--setup` calls `"$PYTHON_BIN" -m bible_cc_plugin.cli setup` which invokes `daemon_main()`, not `setup_main()`. The setup wizard never runs.

**Fix**: Use `bible-cc setup` entry point or invoke `setup_main()` directly.

---

## Major Issues

### 3. MCP permissions in wrong location
**File**: `.claude/settings.local.json`

File lives in plugin directory but contains `enableAllProjectMcpServers` and MCP tool permissions that Claude Code expects at project root. MCP tools will prompt for approval on every session.

**Fix**: Merge permissions into project root `.claude/settings.local.json` or add via deploy script.

### 4. Wrong env var name in hooks
**File**: `hooks/hooks.json` (line 16)

`$TOOL_RESULT` should be `$TOOL_OUTPUT` — the standard Claude Code env var for `PostToolUse` hooks. Tool results will always be empty.

**Fix**: Change `$TOOL_RESULT` to `$TOOL_OUTPUT`.

---

## Minor Issues

### 5. Wrong plugin name in docstrings
**Files**: `bypass.py` (line 1), `injection.py` (line 1)

Module docstrings say "BiBLE Hermes Plugin" instead of "BiBLE CC Plugin".

**Fix**: Updated docstrings to "BiBLE CC Plugin". ✅ Done

### 6. No .gitignore
**File**: (missing)

Python artifacts (`.venv/`, `.pytest_cache/`, `uv.lock`, etc.) not excluded from version control.

**Fix**: Added `.gitignore` with Python, venv, build, test, and IDE patterns. ✅ Done

### 7. No LICENSE file
**File**: (missing)

`plugin.json` declares `"license": "MIT"` but no LICENSE file present.

**Fix**: Copied LICENSE from project root. ✅ Done

---

## Component Summary

| Component | Status | Details |
|---|---|---|
| Manifest (`plugin.json`) | ✅ Valid | All fields correct |
| Hooks (`hooks/hooks.json`) | ⚠️ Issue | `$TOOL_RESULT` → `$TOOL_OUTPUT` |
| MCP Servers (`.mcp.json`) | ⚠️ Issue | Shell-default env syntax |
| Commands | ❌ None | No `commands/` directory |
| Agents | ❌ None | No `agents/` directory |
| Skills | ❌ None | No `skills/` directory |
| Tests | ✅ 18/18 pass | Buffer, config, detector, injector, MCP |

---

## Positive Findings

- Three-component architecture (daemon, MCP server, hooks) is well designed and logically separated
- Daemon uses local SQLite buffer with proper schema, migrations, and thread safety
- All 18 tests pass cleanly
- Configuration resolution with proper env var overrides, range validation, and fallback defaults
- Hook lifecycle clearly documented in README.md
- MCP tool input schemas are well-defined with proper types and descriptions
- Logging is structured with redaction for secrets
- Graceful degradation when BiBLE Atlas is unreachable

---

## Resolved (this session)

| # | Issue | Status |
|---|-------|--------|
| 5 | Docstrings say "Hermes" instead of "CC" | ✅ Fixed |
| 6 | No `.gitignore` | ✅ Added |
| 7 | No `LICENSE` file | ✅ Copied |

## Remaining

| # | Issue | Priority |
|---|-------|----------|
| 1 | MCP env shell-default syntax | Critical |
| 2 | deploy.sh --setup wrong entry point | Critical |
| 3 | MCP permissions in wrong location | Major |
| 4 | `$TOOL_RESULT` → `$TOOL_OUTPUT` | Major |
