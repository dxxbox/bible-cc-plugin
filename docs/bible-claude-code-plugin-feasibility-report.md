# BiBLE Claude Code Plugin — Architecture Design

> Status: draft | Date: 2026-06-11

## Overview

A Claude Code plugin that integrates BiBLE Atlas as a memory + knowledge broker.
Four components: a persistent daemon, an MCP server, lifecycle hooks, and user-facing commands.

## Design Journey

This section captures the Q&A flow that shaped the architecture — why each decision was made and what alternatives were considered.

### Q1: Primary motivation?

**Chosen: Full integration (C)** — Context recall/injection + session capture + agent tools. Not just tools-only or memory-only. The plugin should mirror what bible-hermes-plugin does for Hermes Agent.

### Q2: What can we learn from claude-mem?

Before choosing an approach, we analyzed the `claude-mem` plugin (v13.4.1, by thedotmack) which is already running in this session. Key findings:

- **Architecture**: Hook system (Setup, SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop) → Worker daemon (HTTP :37777, SQLite) → MCP server (stdio) → Skills (slash commands)
- **claude-mem built everything from scratch** because no external memory server existed: own SQLite, own ChromaDB vector store, own LLM observation pipeline, own schema migrations
- **BiBLE Atlas already provides all of that**: OpenSearch for search, Celery for async tasks, full HTTP API, file storage. The plugin is a *bridge*, not a replacement.

### Q3: Language?

**Chosen: TypeScript (Bun runtime)** — Follows claude-mem's proven stack. Key advantages:

- **No venv headaches**: Bun is a single binary; `bun install` just works. No Python version management, no virtual environment activation, no Pylance import resolution issues.
- **Native SQLite**: `bun:sqlite` is built into the runtime — zero native addon friction vs Python's `sqlite3` (which is also stdlib, but the broader deployment story favors Bun).
- **MCP SDK first-class**: The `@modelcontextprotocol/sdk` TypeScript package is the reference MCP implementation.
- **Single binary deployment**: Users install Bun, run `bun install`, and everything works. No `pip`, `uv`, `venv`, or Python version conflicts.
- **claude-mem parity**: The plugin we studied (claude-mem v13.4.1) uses this exact stack — hooks → worker daemon → MCP server → commands. Following it reduces architectural risk.

**Previously considered: Python** — Python was chosen initially to reuse bible-hermes-plugin's HTTP client, recall pipeline, ranking, and config modules. However, the deployment complexity (venv management, Pylance resolution, `uv` vs `pip` confusion, system Python vs project Python) outweighed the code reuse benefit. The BiBLE HTTP API is a simple REST interface — reimplementing the client in TypeScript is ~100 lines, not worth the deployment tax.

### Q4: Who are the users?

**Chosen: Both individual + team (C)** — Developers, but also writers, reporters, teachers. Not code-only. BiBLE serves as a general knowledge/memory broker, not a code-specific tool.

### Q5: Where does BiBLE Atlas live?

**Chosen: Doesn't matter (D)** — The plugin just needs a base URL. Local, team server, cloud — all opaque to the plugin.

### Q6: What gets captured as memory?

**Chosen: Key moments, configurable (C)** — Not full transcripts, not summary-only. Three moment types matter:
- **Session start** — defines topic scope
- **Decision moment** — user confirms a choice/approach (MUST have)
- **Accomplishment moment** — something verified and accepted; session focus shifts

Explicitly NOT captured: intermediate bug fixes (model/user mistakes), unconfirmed discoveries (side notes).

### Q7: How does context recall work?

**Chosen: Auto-inject + explicit tools (D)** — BiBLE plays the role of a *broker/summoner* of knowledge and memory. Claude Code already manages skills well natively. So the tool surface excludes skill tools — only memory + knowledge tools remain.

### Q8: Who detects key moments?

**Chosen: Plugin-side LLM (C)** — The daemon has its own LLM call (configurable model) to classify moments. Not heuristic regex, not delegated to BiBLE server's AI pipeline. This implies the daemon needs: buffering, prompt construction, LLM API integration, and structured output parsing.

### Q9: Hook-based capture or daemon-based?

**Chosen: Worker daemon, claude-mem style (C)** — Given plugin-side LLM detection, multi-user/team scenarios, and Claude Code sessions that can end abruptly or run in parallel, a persistent daemon with durable SQLite buffer is the only architecture that won't lose moments.

### Why Not The Alternatives

| Alternative | Why Rejected |
|---|---|
| **Python (uv/pip)** | venv management, Pylance resolution, and deployment complexity outweigh code reuse with hermes plugin. Bun + TypeScript eliminates all of this (Q3). |
| **Thin scripts + server-side LLM** | User wants plugin-side moment detection (Q8) |
| **In-memory MCP server only** | Buffer lost on crash/restart; can't survive session boundaries |
| **Full transcript capture** | Too noisy; user explicitly wants key moments only (Q6) |
| **Include skill tools** | Claude Code manages skills natively; BiBLE focuses on memory + knowledge (Q7) |

---

## Scenario Summary

| Dimension | Decision |
|---|---|
| **Users** | Individuals + teams; developers, writers, reporters, teachers |
| **BiBLE location** | Opaque to plugin — just a base URL |
| **Primary domains** | Memory + Knowledge brokerage (skills are Claude Code's domain) |
| **Capture content** | Key moments: session-start topics, decisions, accomplishments |
| **Capture mode** | Configurable; key-moments by default |
| **Context recall** | Auto-inject + explicit tools both available |
| **Moment detection** | Plugin-side LLM call (worker daemon) |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Claude Code                             │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐  │
│  │  Setup   │  │SessionSt │  │UserPrompt │  │   Stop    │  │
│  │  Hook    │  │   art    │  │  Submit   │  │   Hook    │  │
│  │          │  │  Hook    │  │   Hook    │  │           │  │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └─────┬─────┘  │
│       │             │              │               │        │
│       │    ┌────────┼──────────────┼───────────────┘        │
│       │    │        │              │                         │
│       │    │   ┌────▼──────────────▼──────┐                  │
│       │    │   │  MCP Server (stdio)      │                  │
│       │    │   │  bible_memory_search     │                  │
│       │    │   │  bible_memory_save       │                  │
│       │    │   │  bible_knowledge_search  │                  │
│       │    │   │  ...                     │                  │
│       │    │   └────────────┬─────────────┘                  │
│       │    │                │                                │
│  ┌────┴────┴────────────────┼────────────────────────────┐  │
│  │  Commands (user-facing)  │                            │  │
│  │  /bible-cc:setup         │  Plugin bootstrap          │  │
│  │  /bible-cc:status        │  Daemon health             │  │
│  │  /bible-cc:save          │  Force-save session        │  │
│  │  /bible-cc:recall        │  Force context refresh     │  │
│  └──────────┬───────────────┘                            │  │
│             │                                            │  │
└─────────────┼────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────┐
│         Bible CC Daemon (HTTP :9777)          │
│                                               │
│  ┌──────────┐  ┌──────────┐  ┌─────────────┐ │
│  │  Session │  │  Moment  │  │   Context    │ │
│  │  Buffer  │  │ Detector │  │   Injector   │ │
│  │ (SQLite) │  │  (LLM)   │  │              │ │
│  └────┬─────┘  └────┬─────┘  └──────┬──────┘ │
│       │             │               │        │
└───────┼─────────────┼───────────────┼────────┘
        │             │               │
        ▼             ▼               ▼
┌──────────────────────────────────────────────┐
│            BiBLE Atlas Server                 │
│  (memory / knowledge search, save, import)    │
└──────────────────────────────────────────────┘
```

### Four Components

| Component | Role | Transport | Lifetime |
|---|---|---|---|
| **Daemon** (`bible-cc-daemon`) | Buffer turns, detect key moments via LLM, flush to BiBLE, serve context for injection | HTTP on `localhost:9777` | Persistent (managed by hooks) |
| **MCP Server** (`bible-cc-mcp`) | 6 BiBLE tools (memory search/save/get, knowledge search/list) + daemon status tool | Stdio (MCP protocol) | Per Claude Code session |
| **Hooks** | Glue — start daemon, inject context, feed turns to daemon | Shell → HTTP calls to daemon | Event-driven |
| **Commands** | User-facing slash commands for manual control — recall, list, save, status and sometime delete | Shell → HTTP calls to daemon | On-demand (user invoked) |

### Key Design Decisions

- Daemon port `9777` (non-standard, avoid conflicts)
- SQLite at `~/.bible-cc/daemon.db` (per-user), using Bun's built-in `bun:sqlite`
- BiBLE Atlas URL configured once, shared by all components
- Skill tools excluded — Claude Code manages skills natively
- **Commands operate the daemon** (setup, status, save, recall). **MCP tools query BiBLE** (search, get, list across all three domains). No overlap, no daemon-as-proxy.
- MCP tools: `bible_memory_search`, `bible_memory_save`, `bible_memory_get`, `bible_knowledge_search`, `bible_knowledge_list`, `bible_skill_search`, `bible_skill_get`
- User commands: `/bible-cc:setup`, `/bible-cc:status`, `/bible-cc:save`, `/bible-cc:recall`

### Capture Taxonomy (Key Moments)

- **Session start** — defines topic scope
- **Decision moment** — user confirms a choice or approach
- **Accomplishment moment** — something verified and accepted by user; session focus shifts

Non-key (not captured):
- Intermediate bug fixes (model/user mistakes)
- Discoveries (side notes, unless user confirms as significant)

### Hook → Daemon Flow

| Hook | Daemon Endpoint | Purpose |
|---|---|---|
| Setup | `POST /daemon/start` | Start daemon if not running |
| SessionStart | `POST /session/start` + `POST /context/inject` | Register session, get context injection string |
| UserPromptSubmit | `POST /turn/user` | Feed user message to buffer |
| PostToolUse | `POST /turn/tool` | Feed tool call to buffer |
| Stop | `POST /session/end` | Trigger moment detection + flush to BiBLE |

---

## Daemon Design

### HTTP API

```
POST /daemon/start       — idempotent, returns {pid, port, status}
POST /daemon/stop        — graceful shutdown
GET  /daemon/health      — {status: "ok", uptime: 1234, sessions: 3}

POST /session/start      — {session_id} → creates session row
POST /session/end        — {session_id} → triggers flush + moment detection

POST /turn/user          — {session_id, message} → buffer turn
POST /turn/assistant     — {session_id, message, tool_calls[]} → buffer turn

POST /context/inject     — {session_id, user_message}
                           → returns "<relevant-memories>..." string
```

### SQLite Schema

```sql
-- Active sessions
CREATE TABLE sessions (
    session_id     TEXT PRIMARY KEY,
    started_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',
    topic_scope    TEXT,
    turn_count     INTEGER DEFAULT 0,
    buffered_chars INTEGER DEFAULT 0
);

-- Buffered turns (raw conversation)
CREATE TABLE turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    seq           INTEGER NOT NULL,
    role          TEXT NOT NULL,      -- user | assistant
    content       TEXT NOT NULL,
    tool_calls    TEXT,               -- JSON array of {name, arguments}
    timestamp     TEXT NOT NULL
);

-- Detected key moments
CREATE TABLE moments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    moment_type   TEXT NOT NULL,      -- session_start | decision | accomplishment
    title         TEXT NOT NULL,
    narrative     TEXT NOT NULL,
    turn_range    TEXT,               -- e.g. "3-7"
    detected_at   TEXT NOT NULL,
    flushed       INTEGER DEFAULT 0  -- 0=pending, 1=sent to BiBLE
);
```

### Moment Detection Flow

```
UserPromptSubmit ─→ buffer turn ─→ check threshold
                                      │
                    ┌─────────────────┘ (every N turns or N chars)
                    ▼
              ┌──────────────┐
              │ Build prompt │  ← last K turns + session context
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │   LLM Call   │  ← Claude API (configurable model)
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Parse result │  → structured moments or "none"
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Save to      │
              │ moments table│  ← pending flush
              └──────────────┘
```

### Moment Detection Prompt (sketch)

```
You are analyzing a conversation between a user and an AI agent.
Identify if any KEY MOMENTS occurred in these recent turns.

Key moment types:
- SESSION_START: the user defines the topic/scope of work
- DECISION: the user confirms a choice, approach, or design direction
- ACCOMPLISHMENT: something was completed, verified, and accepted

Do NOT flag:
- Intermediate bug fixes or error corrections
- Exploratory discoveries (unless user explicitly confirms importance)

For each key moment found, provide:
- type: one of the above
- title: one-line summary
- narrative: 2-4 sentences describing what happened and why it matters
```

### BiBLE HTTP Client

A thin TypeScript module (`src/daemon/client.ts`) wrapping the BiBLE Atlas REST API
using Bun's native `fetch`. Covers all three domains:

- `searchMemory()`, `saveMemory()`, `getMemory()`
- `searchKnowledge()`, `listKnowledge()`
- `searchSkill()`, `getSkill()`

The BiBLE API is a straightforward REST interface — the client is ~100 lines of
TypeScript. No need to share code with the Python-based Hermes plugin; the API
contract is the integration point.

---

## MCP Server Design

A thin wrapper around the BiBLE HTTP client (`src/daemon/client.ts`), using
`@modelcontextprotocol/sdk`. Stdio transport, launched per Claude Code session.

### Tools

| Tool | Parameters | Returns |
|---|---|---|
| `bible_memory_search` | query, top_k, min_score | hits[] |
| `bible_memory_save` | messages[], title, abstract | {memory_id, task_id} |
| `bible_memory_get` | memory_id | {title, overview, ...} |
| `bible_knowledge_search` | query, tag, top_k | hits[] |
| `bible_knowledge_list` | tag | [{name, description}] |

### Discovery (`.mcp.json`)

```json
{
  "mcpServers": {
    "bible-atlas": {
      "command": "bun",
      "args": ["run", "src/mcp/server.ts"],
      "env": {
        "BIBLE_ATLAS_BASE_URL": "http://localhost:5555"
      }
    }
  }
}
```

No shell-default syntax in `env` — MCP treats values as literals. The daemon
resolves defaults at startup; the MCP server reads `BIBLE_ATLAS_BASE_URL` as-is.

## Hooks Design

Glue between Claude Code lifecycle events and the daemon. Two TypeScript CLI entry
points compiled via `bun build`: `bible-cc-daemon` (lifecycle) and `bible-cc-hook`
(thin wrappers that call the daemon's HTTP API).

### Hook → Daemon Mapping

```
Setup ──────────────────────────────────────────────────────────────
  → bible-cc-daemon --start          (idempotent, no-op if running)
  → ensures config exists (prompt setup if missing)

SessionStart ───────────────────────────────────────────────────────
  → POST /session/start  {session_id}
  → POST /context/inject {session_id, user_message}
  → returns context string → injected into Claude's system prompt

UserPromptSubmit ───────────────────────────────────────────────────
  → POST /turn/user  {session_id, message}

PostToolUse ────────────────────────────────────────────────────────
  → POST /turn/tool  {session_id, tool_name, arguments, result_summary}

Stop ───────────────────────────────────────────────────────────────
  → POST /session/end  {session_id}
  → daemon runs moment detection → flushes key moments to BiBLE
```

### hooks/hooks.json (sketch)

```json
{
  "hooks": {
    "Setup": [{
      "command": "bun run scripts/daemon.ts --start",
      "timeout": 10000
    }],
    "SessionStart": [{
      "command": "bun run scripts/hook.ts session-start --session-id \"$CLAUDE_SESSION_ID\"",
      "timeout": 15000,
      "inject": true
    }],
    "UserPromptSubmit": [{
      "command": "bun run scripts/hook.ts turn-user --session-id \"$CLAUDE_SESSION_ID\" --message \"$USER_PROMPT\"",
      "timeout": 5000
    }],
    "PostToolUse": [{
      "command": "bun run scripts/hook.ts turn-tool --session-id \"$CLAUDE_SESSION_ID\" --tool \"$TOOL_NAME\" --output \"$TOOL_OUTPUT\"",
      "timeout": 5000
    }],
    "Stop": [{
      "command": "bun run scripts/hook.ts session-end --session-id \"$CLAUDE_SESSION_ID\"",
      "timeout": 30000
    }]
  }
}
```

Note: `$TOOL_OUTPUT` (not `$TOOL_RESULT`) is the standard Claude Code environment
variable for PostToolUse hook tool output.

- `bible_memory_save` (MCP tool) also calls `POST /daemon/notify` so the daemon
  knows a manual memory was saved and can skip re-injecting it as context.
- `PostToolUse` truncates tool result content to 250 chars by default
  (configurable via `tool_result_max_chars`) — enough for moment detection,
  not full file contents.

---

## Commands Design

Commands and MCP tools serve different actors. The design principle, following claude-mem:

- **Commands** = user-initiated, operate on the **plugin/daemon itself** (setup, status, force-save, force-recall)
- **MCP tools** = model-initiated, query **BiBLE Atlas** (search, get, list across all three domains)

The user should not need a slash command for `memory-search` — they say "find my
memories about X" and the model invokes `bible_memory_search`. Commands are for
actions only the user can trigger on the daemon.

### Why commands matter

Without commands, users rely entirely on automatic hooks and model-driven MCP tools.
But some actions are inherently user-initiated:

- "I just made an important decision — save this session NOW, don't wait for Stop."
- "What's the daemon doing? Is capture working?"
- "Refresh my context — I've changed direction and the old recall is stale."
- "Set up BiBLE for the first time — where do I point it?"

### Command Surface (4 commands)

```
/bible-cc:setup
    Interactive first-time setup wizard.
    Prompts for BiBLE Atlas base URL, tests connectivity, writes ~/.bible-cc/config.json,
    starts the daemon. Idempotent — re-run to change config.
    Implemented as: bun run scripts/setup.ts

/bible-cc:status
    Show daemon health, uptime, active sessions, buffered turn count, pending moment
    count, and BiBLE Atlas connectivity status.
    Implemented as: curl GET /daemon/health (extended)

/bible-cc:save [--title T] [--abstract A]
    Force-save the current buffered session as a memory RIGHT NOW.
    Triggers moment detection on buffered turns and flushes to BiBLE Atlas.
    With --title/--abstract, bypasses auto-detection for precise user control.
    Different from the bible_memory_save MCP tool: this operates on the daemon's
    session buffer, not arbitrary user-provided messages.
    Implemented as: curl POST /daemon/session/flush {session_id, title?, abstract?}

/bible-cc:recall
    Manually trigger a full context recall mid-session.
    Queries all enabled BiBLE domains through the daemon's context injector,
    returning a fresh <relevant-memories> block.
    Useful when the user changes topic and wants fresh context immediately.
    Implemented as: curl POST /daemon/context/inject {session_id, user_message}
```

### What about domain search/browse?

Memory search, knowledge search, skill search, listing, and retrieval are **MCP tools** —
the model invokes them. The user expresses intent in natural language ("find my
memories about the auth refactor", "what knowledge bases do I have?"), and the model
calls the appropriate tool. No slash commands needed.

This keeps the command surface small (4 commands) and the MCP tool surface
comprehensive (8 tools across 3 domains).

### Command Implementation

Commands are thin shell wrappers — no new backend logic. Each is a one-liner that
either calls a Bun script (for interactive flows) or curls the daemon (for status ops).

```
/bible-cc:setup   → bun run scripts/setup.ts
/bible-cc:status  → curl -s http://127.0.0.1:9777/daemon/health | bun -e "JSON.parse(await stdin.text())"
/bible-cc:save    → bun run scripts/hook.ts session-flush --session-id "$CLAUDE_SESSION_ID"
/bible-cc:recall  → bun run scripts/hook.ts context-inject --session-id "$CLAUDE_SESSION_ID"
```

### commands/*.md (sketch)

```markdown
<!-- /bible-cc:save -->
# Save current session as memory

Force-save the current conversation buffer as a memory in BiBLE Atlas.
Triggers moment detection on buffered turns and flushes key moments immediately.
Use --title and --abstract to provide your own summary instead of auto-detection.

Usage: /bible-cc:save [--title "Decision: use TypeScript"] [--abstract "We decided..."]
```

### Design rationale

The initial design had 11+ commands mirroring every BiBLE API endpoint. This was
over-engineered for two reasons:

1. **Redundancy with MCP tools**: The model can already call `bible_memory_search`,
   `bible_knowledge_search`, `bible_skill_search`, etc. Adding user-facing commands
   for the same operations creates two ways to do the same thing with no benefit.

2. **Daemon as API gateway**: Having the daemon proxy every BiBLE API call
   (`/daemon/api/memory/search`, `/daemon/api/knowledge/list`, ...) bloats the daemon
   API surface. The daemon's job is session buffering + moment detection + context
   injection — not proxying REST calls it adds no value to.

The revised design follows claude-mem's separation: the daemon does session management;
MCP tools do domain queries; commands bridge the user to the daemon for the few
actions that are inherently user-initiated.

---

## Config System

Single JSON file at `~/.bible-cc/config.json`. Environment variable overrides
take precedence. JSON is chosen over YAML to avoid an extra dependency — Bun's
native `Bun.file().json()` handles parsing with zero additional packages.

### Schema

```json
{
  "bible": {
    "base_url": "http://localhost:5555",
    "token": null
  },
  "daemon": {
    "port": 9777,
    "db_path": "~/.bible-cc/daemon.db"
  },
  "recall": {
    "enable_memory": true,
    "enable_knowledge": false,
    "knowledge_tags": [],
    "top_k": 8,
    "min_score": 0.35,
    "injection_token_budget": 1200,
    "force_injection": false
  },
  "capture": {
    "enabled": true,
    "mode": "key_moments",
    "commit_threshold_turns": 8,
    "commit_threshold_chars": 16000,
    "tool_result_max_chars": 250
  },
  "detection": {
    "model": "claude-sonnet-4-5",
    "max_tokens": 512,
    "temperature": 0.0
  },
  "bypass": {
    "session_patterns": []
  }
}
```

Env var overrides: `BIBLE_ATLAS_BASE_URL`, `BIBLE_ATLAS_TOKEN`, `BIBLE_CC_DAEMON_PORT`,
`BIBLE_CC_DB_PATH`.

### Setup flow

```
bun run scripts/setup.ts
  → prompts for BiBLE base URL
  → writes ~/.bible-cc/config.json
  → starts daemon
  → verifies connectivity to BiBLE Atlas
```

### Credentials

- **BiBLE Atlas**: `BIBLE_ATLAS_TOKEN` env var or `bible.token` in config.json
- **LLM (moment detection)**: Daemon inherits `ANTHROPIC_API_KEY` from Claude Code's
  environment. The `@anthropic-ai/sdk` TypeScript package auto-detects this.

---

## Package Structure

```
bible-cc-plugin/
├── package.json                    ← Bun dependencies + scripts
├── tsconfig.json                   ← TypeScript config
├── plugin.json                     ← .claude-plugin manifest
├── .mcp.json                       ← MCP server discovery
├── .gitignore
├── LICENSE
├── hooks/
│   └── hooks.json                  ← hook definitions
├── commands/                       ← user-facing slash commands
│   ├── setup.md                    ← /bible-cc:setup
│   ├── status.md                   ← /bible-cc:status
│   ├── save.md                     ← /bible-cc:save
│   └── recall.md                   ← /bible-cc:recall
├── src/
│   ├── daemon/
│   │   ├── server.ts               ← HTTP server (:9777) — Bun.serve()
│   │   ├── buffer.ts               ← SQLite session/turn/moment store (bun:sqlite)
│   │   ├── detector.ts             ← LLM moment detection (@anthropic-ai/sdk)
│   │   ├── injector.ts             ← context injection (calls BiBLE recall API)
│   │   └── client.ts               ← BiBLE HTTP client (native fetch)
│   ├── mcp/
│   │   └── server.ts               ← MCP stdio server (@modelcontextprotocol/sdk)
│   ├── config.ts                   ← config loading (JSON + env var overrides)
│   └── types.ts                    ← shared TypeScript types
├── scripts/
│   ├── daemon.ts                   ← daemon lifecycle CLI (start/stop/status)
│   ├── hook.ts                     ← hook bridge (calls daemon HTTP endpoints)
│   └── setup.ts                    ← interactive setup wizard
└── tests/
    ├── daemon.test.ts
    ├── buffer.test.ts
    ├── detector.test.ts
    ├── mcp.test.ts
    └── client.test.ts
```

**Key dependencies:**
- `@modelcontextprotocol/sdk` — MCP server framework
- `@anthropic-ai/sdk` — LLM calls for moment detection
- `bun:sqlite` — built-in SQLite (no package needed)
- Native `fetch` — HTTP client to BiBLE Atlas (no package needed)

**Distribution:** `bun install` — single command. User then adds the plugin directory
to Claude Code's plugin registry. Configuration via `bun run scripts/setup.ts` wizard.
No virtual environment, no Python version management, no `pip` vs `uv` confusion.
