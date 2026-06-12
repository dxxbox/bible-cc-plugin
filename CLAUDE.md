# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`bible-cc-plugin` is a Claude Code plugin that integrates BiBLE Atlas as a memory + knowledge broker — providing context recall, session capture, and agent-accessible tools for Claude Code.

**Status: skeleton.** The full architecture is designed and documented in `docs/bible-claude-code-plugin-feasibility-report.md`. No source code exists yet.

## Pre-Action Checklist

Before any edit/write, ask:

1. Was this explicitly asked for? (If not, stop.)
2. Am I assuming or do I know? (If assuming, ask.)
3. Is this the simplest approach? (If over 3 steps, reconsider.)
4. Will this change pass verification? (If not sure, flag it.)
5. Never, ever change standard to get `Pass`

## Build & Test

```bash
uv sync                  # create .venv, install all deps
uv run pytest            # run all tests
uv run pytest tests/unit # run a single test directory
uv run ruff check        # lint
uv run ruff format       # format
```

Runtime: **Python 3.10+ with `uv`** (no pip, no venv activation). `uv run` resolves the project's `.venv/` automatically — no `source .venv/bin/activate` needed, ever.

The design originally considered TypeScript + Bun (see feasibility report Q3) but switched to Python because `uv` provides equivalent deployment ergonomics — `uv sync` is equivalent to `bun install` in simplicity, `uv run` eliminates venv activation, and the BiBLE Atlas API is a straightforward REST contract easily consumed with `httpx`.

## Architecture

Four components, all Python:

```
bible-cc-plugin/
├── pyproject.toml            # deps, entry points, build config
├── plugin.json               # .claude-plugin manifest
├── .mcp.json                 # MCP server discovery (bible-atlas server)
├── hooks/hooks.json          # Hook → daemon HTTP mappings
├── commands/                 # User-facing slash commands
│   ├── setup.md              #   /bible-cc:setup
│   ├── status.md             #   /bible-cc:status
│   ├── save.md               #   /bible-cc:save
│   ├── recall.md             #   /bible-cc:recall
│   └── review.md             #   /bible-cc:review
├── src/bible_cc_plugin/
│   ├── daemon/
│   │   ├── server.py         #   FastAPI HTTP server on :9777
│   │   ├── buffer.py         #   SQLite session/turn/moment store (sqlite3)
│   │   ├── detector.py       #   LLM moment detection (anthropic SDK)
│   │   ├── injector.py       #   Context recall (local buffer: turns + moments)
│   │   └── client.py         #   BiBLE HTTP client (against BiBLE Atlas REST API)
│   ├── mcp/server.py         #   MCP stdio server (mcp Python SDK)
│   ├── config.py             #   Config loading (JSON + env var overrides)
│   └── types.py              #   Shared types (Pydantic models)
├── scripts/
│   ├── daemon.py             #   Daemon lifecycle CLI (start/stop/status)
│   ├── hook.py               #   Hook bridge (calls daemon HTTP endpoints)
│   └── setup.py              #   Interactive setup wizard
└── tests/
    ├── test_daemon.py
    ├── test_buffer.py
    ├── test_detector.py
    ├── test_mcp.py
    └── test_client.py
```

### Component Roles

| Component | Role | Transport | Lifetime |
|---|---|---|---|
| **Daemon** | Buffer turns, detect key moments via LLM, flush to BiBLE, serve local context injection | HTTP `localhost:9777` | Persistent |
| **MCP Server** | 7 BiBLE tools (memory search/save/get, knowledge search/list, skill search/get) | Stdio (MCP) | Per Claude Code session |
| **Hooks** | Glue — start daemon, inject context, feed turns | Shell → HTTP to daemon | Event-driven |
| **Commands** | User-facing manual control (setup, status, save, recall, review) | Shell → HTTP to daemon | On-demand |

### Key Design Decisions

- **Language**: Python 3.10+ with `uv`. Originally considered TypeScript + Bun, but `uv` provides equivalent deployment ergonomics (`uv sync` ≈ `bun install`, `uv run` ≈ `bun run`) without the overhead of a Node→Bun bridge for hook compatibility.
- **Daemon port**: `9777` (non-standard, avoid conflicts), configurable via `daemon.port`. If occupied, the SessionStart hook prints an error hint (same mechanism as moment detection hints). `daemon.port_auto_fallback` (default `false`) enables port+1 auto-retry.
- **SQLite DB**: `~/.bible-cc/daemon.db` (per-user), using stdlib `sqlite3`. Daemon startup sets `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;` — WAL mode prevents `SQLITE_BUSY` errors under concurrent writes from multiple Claude Code sessions. No connection pool needed.
- **Config**: `~/.bible-cc/config.json` with env var overrides (`BIBLE_ATLAS_BASE_URL`, `BIBLE_ATLAS_TOKEN`, `BIBLE_CC_DAEMON_PORT`, `BIBLE_CC_DB_PATH`)
- **Moment detection**: Plugin-side LLM call (configurable model). Two-phase: async mid-session detection (last 2-3 turns, with CLI hint notification) + retrospective detection on session end (full session synthesis). See "Moment Detection Design" below.
- **Capture taxonomy**: Three key moment types — session_start, decision, accomplishment. Intermediate bug fixes and unconfirmed discoveries are explicitly NOT captured.
- **Command ↔ MCP tool separation**: Commands operate the **daemon** (user-initiated). MCP tools query **BiBLE Atlas** (model-initiated). No overlap, no daemon-as-proxy.
- **Hint notification**: When a key moment is detected mid-session, a hint is printed to the CLI status line area via hook stdout. Format is configurable (see `hint_format`). The hint carries enough context that the user doesn't need to scroll back.
- **Graceful degradation (BiBLE unreachable)**: If BiBLE Atlas is down, local operations continue uninterrupted. `/session/start` and `/context/inject` are pure local SQLite — no BiBLE dependency. Moment flush is deferred (moments stay `flushed=0` until BiBLE recovers). MCP tools (`bible_memory_search` etc.) return errors — the model is informed and can retry or continue without. BiBLE status is surfaced via CLI hint and `/bible-cc:status`. No automatic retry — mark, notify, move on. If the daemon itself is unreachable during UserPromptSubmit/PostToolUse hooks, hook scripts silently skip — Claude Code is never blocked.
- **Session crash recovery**: If a session terminates abnormally (Claude Code killed, daemon crash, system restart), the Stop hook never fires. On next SessionStart, the daemon detects unclosed sessions and triggers a catch-up retrospective detection + flush. Buffered turns are never silently lost.
- **Daemon lifetime**: Runs until system shutdown or manual stop (`POST /daemon/stop`). No idle timeout. The daemon is a persistent background process, not a per-session ephemeral server.

### Hook → Daemon Flow

| Hook | Daemon Endpoint | Purpose |
|---|---|---|
| Setup | `POST /daemon/start` | First-time install (write config, install deps). Not relied on for daemon lifecycle. |
| — (manual) | `POST /daemon/stop` | Graceful shutdown |
| SessionStart | `POST /daemon/start` (idempotent) → `POST /session/start` → `POST /context/inject` | Self-contained: ensure daemon running, register session + crash recovery, inject local buffer context (turns + moments). No BiBLE call. |
| UserPromptSubmit | `POST /turn/user` | Feed user message to buffer |
| PostToolUse | `POST /turn/tool` | Feed tool call to buffer |
| Stop | `POST /session/end` | Trigger moment detection + flush to BiBLE |

### Hook Commands

All hooks use `uv run` — no venv activation needed:

```
Setup:           uv run python -m bible_cc_plugin.scripts.setup                     # first-time install wizard
SessionStart:    uv run python -m bible_cc_plugin.scripts.hook session-start ...     # self-contained: starts daemon if needed, then registers session + injects context
UserPromptSubmit: uv run python -m bible_cc_plugin.scripts.hook turn-user ...        # graceful skip if daemon unreachable
PostToolUse:     uv run python -m bible_cc_plugin.scripts.hook turn-tool ...         # graceful skip if daemon unreachable
Stop:            uv run python -m bible_cc_plugin.scripts.hook session-end ...
```

## Moment Detection Design

Two-phase detection with async mid-session hints and session-end retrospective.

### Phase 1: Mid-Session (async, after each turn)

```
UserPromptSubmit → daemon queues detection (non-blocking, returns immediately)
PostToolUse      → daemon queues detection (non-blocking, returns immediately)

Daemon worker picks up detection task:
  → builds prompt from last 2-3 turns
  → LLM call (configurable model, low max_tokens)
  → result: moment (type + title + narrative) or "none"

If moment found:
  → content-hash dedup: SHA-256(session_id + title + narrative), INSERT OR IGNORE
  → saves to SQLite moments table (flushed=0)
  → if config mid_session_upload enabled: POST to BiBLE (flushed=1)
  → prints hint to CLI status line via hook stdout
```

**The hint arrives on a subsequent turn** (not the same turn) because detection is async. The hint carries its own context so the user doesn't need to scroll back to the original turn. Format is controlled by `capture.hint_format`:

| Mode | Example |
|---|---|
| `quote_with_command` (default) | `⎿ ⏳ Captured: "Let's use PostgreSQL instead of SQLite" — Decision. /bible-cc:review to see pending moments.` |
| `quote_only` | `⎿ ⏳ Captured: "Let's use PostgreSQL instead of SQLite" — Decision.` |
| `command_only` | `⎿ ⏳ Key moment captured (turn 5). /bible-cc:review to see pending moments.` |
| `narrative` | `⎿ ⏳ Captured decision: PostgreSQL for auth storage. Postgres chosen over SQLite for concurrent writes and team expertise.` |

### Phase 2: Session End (retrospective)

```
Stop hook → POST /session/end
  → daemon runs retrospective detection:
    → prompt sees ALL turns in the session
    → prompt includes Phase 1's already-detected moments with instruction:
      "Do NOT re-report these. Only report NEW moments."
    → different LLM call, different prompt:
      "Here is a complete session. What was accomplished?
       What decisions shaped the outcome? What should be
       remembered for future sessions?"
    → output: overall session assessment + any NEW missed key moments
  → content-hash dedup on all new moments (same UNIQUE constraint)
  → bundles all unflushed moments + retrospective into one unit
  → POSTs to BiBLE Atlas as a single session import
```

The retrospective prompt is distinct from the mid-session prompt — it's a synthesis + gap-fill task, not a spot-check.

### Dedup Strategy (Two-Layer)

Moments are deduplicated at two layers to prevent duplicates from overlapping Phase 1 windows and Phase 1→Phase 2 re-detection:

| Layer | Mechanism | Scope |
|---|---|---|
| **Prompt injection** | Phase 2 prompt includes already-detected moments list; LLM instructed not to re-report | Phase 1 → Phase 2 |
| **Content-hash** | `SHA-256(session_id + title + narrative)` with UNIQUE constraint; `INSERT OR IGNORE` | Both phases (also catches Phase 1 self-duplicates from overlapping windows) |

The `moments` table has `content_hash TEXT UNIQUE NOT NULL`. Before inserting any moment, compute the hash — if it collides with an existing row, the insert is silently skipped.

### Config

```json
{
  "capture": {
    "enabled": true,
    "mode": "key_moments",
    "mid_session_detection": true,
    "mid_session_upload": false,
    "hint_format": "quote_with_command",
    "tool_result_max_chars": 250
  }
}
```

- `mid_session_detection` (default `true`): run async detection after each turn
- `mid_session_upload` (default `false`): if `true`, upload each moment to BiBLE immediately when detected. If `false`, moments accumulate as `flushed=0` and are uploaded as a group on session end
- `hint_format`: how the CLI hint is presented when a key moment is detected mid-session
- `tool_result_max_chars` (default `250`): max chars of tool output精华 extracted by moment detector LLM. Hook sends full tool output to daemon (no mechanical truncation); the LLM extracts the most relevant ≤N chars as part of its moment detection run.
- `inject_fallback` (default `skip`): behavior when BiBLE Atlas is unreachable during context injection. `skip` — return nothing, continue silently. `empty` — return an empty `<relevant-memories>` block.

## Review Command (`/bible-cc:review`)

User-facing command to browse, edit, and manage pending moments before flush.

```
/bible-cc:review
    Shows all pending moments for the current session:
      [1] Decision: "PostgreSQL for auth storage" (turn 5)
      [2] Accomplishment: "Rate limiting implemented" (turn 12)
    Options: view details, edit title/abstract, discard, force-flush
```

Daemon endpoints:
- `GET /daemon/moments?session_id=X` — list pending moments
- `DELETE /daemon/moments/{id}` — discard a pending moment
- `PUT /daemon/moments/{id}` — edit title/abstract

### Key Dependencies

- `fastapi` + `uvicorn` — daemon HTTP server
- `mcp` — MCP server (Python SDK)
- `anthropic` — LLM calls for moment detection
- `httpx` — async HTTP client to BiBLE Atlas
- `pydantic` — config/types validation
- `sqlite3` — stdlib, no package needed

## Key Rules

- **Follow the design doc**: `docs/bible-claude-code-plugin-feasibility-report.md` is the authoritative architecture reference. Read it before implementing. Note: Q3 (language choice) is superseded — the final decision is Python + uv.
- **BiBLE Atlas API is the contract**: The BiBLE HTTP client at `src/bible_cc_plugin/daemon/client.py` is written directly against the BiBLE Atlas REST API specification. It's the single client used by both daemon and MCP server — don't duplicate. The three-domain model (MEMORY, SKILL, KNOWLEDGE_BASE) and all endpoints are documented in the parent `CLAUDE.md`.
- **`uv run` everywhere**: Never `source .venv/bin/activate`. Every command uses `uv run`. Every hook, script, and MCP invocation uses `uv run`.
- **Deployment is two steps**: (1) ensure `uv` is installed, (2) `uv sync`. No activation, no venv management. The Setup hook handles step 1 if needed.
- **BiBLE HTTP client lives once**: `src/bible_cc_plugin/daemon/client.py` is the single BiBLE API client. Both daemon and MCP server use it — don't duplicate.
- **PostToolUse hook variable**: Use `$TOOL_OUTPUT` (not `$TOOL_RESULT`) — this is the standard Claude Code env var for `PostToolUse` hooks.
- **`.mcp.json` env values are literals**: MCP treats them as raw strings. Don't use shell-default syntax (`${VAR:-default}`).
- **Graceful degradation**: If BiBLE Atlas is unreachable: (1) CLI status hint notifies user, (2) `/session/start` succeeds locally, (3) `/context/inject` is skipped, (4) no automatic retry. The plugin must never block, slow down, or crash Claude Code under any BiBLE Atlas outage. Similarly, if the daemon is unreachable during UserPromptSubmit or PostToolUse hooks, the hook scripts silently skip (bypass) — Claude Code is never blocked by a daemon outage.
- **Config path**: `~/.bible-cc/config.json`. Env vars override config file values.
- **MCP server and daemon are independent**: No direct communication between them. MCP tools are pure BiBLE Atlas API wrappers. The daemon gets all tool-call context it needs from the PostToolUse hook (`/turn/tool`). The feasibility report's proposed `POST /daemon/notify` is not needed and should not be implemented. Same-session re-injection of manually saved memories is correct behavior: when `/clear` or context compact triggers SessionStart, the model has lost its context and re-injecting the memory restores it.

## Monorepo Context

This is a sub-project of the BiBLE monorepo at `/Users/x61zhang/workspace/BiBLE/`. The parent `CLAUDE.md` documents cross-cutting architecture: the three-domain model (MEMORY, SKILL, KNOWLEDGE_BASE), plugin architecture patterns, config resolution, and the test mode server.

BiBLE Atlas test server (for integration testing):
```bash
# From BiBLE-Atlas/:
uv run python -m bible.test_mode.server --port 5555
```

Other BiBLE plugins in this monorepo share the same architecture pattern (daemon + MCP + hooks + commands) but are independent consumers of the BiBLE Atlas API — none import from each other.

## Design Docs

- `docs/bible-claude-code-plugin-feasibility-report.md` — Full architecture, design journey (Q&A), component designs, config schema, rationale for every decision. **Note: Q3 (language choice) is superseded — final decision is Python + uv.**
- `docs/claude-mem-analysis-report.md` — Analysis of the claude-mem plugin (v13.4.1) that served as the architectural reference
- `docs/bible-cc-plugin-validation-2026-06-11.md` — Pre-implementation validation report (critical issues and fixes needed)
