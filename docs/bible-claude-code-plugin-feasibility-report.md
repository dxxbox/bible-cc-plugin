# BiBLE Claude Code Plugin — Architecture Design

> Status: draft | Date: 2026-06-11
> ⚠️ **本文是早期可行性分析，部分细节已过时。** MCP 工具数量（6 活跃 + 2 postponed）、端点名称（`/turn/tool` 非 `/turn/assistant`）、配置字段等以 `docs/sw-design/` 下的 SW design 文档为准。

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

**Chosen: Python 3.10+ with `uv`** — `uv` provides deployment ergonomics equivalent to Bun (`uv sync` ≈ `bun install`, `uv run` ≈ `bun run`) without the overhead of a Node→Bun bridge for hook compatibility. Key advantages:

- **Zero activation**: `uv run` resolves the project's `.venv/` automatically — no `source .venv/bin/activate` needed, ever. Same friction-free experience as `bun run`.
- **Stdlib SQLite**: Python's `sqlite3` is built into the stdlib — zero native addon friction, same as `bun:sqlite`.
- **MCP SDK available**: The `mcp` Python SDK provides equivalent MCP server capabilities to the TypeScript reference implementation.
- **BiBLE API is a simple REST contract**: The HTTP client is ~100 lines of `httpx`. The API contract is the integration point — no meaningful advantage to sharing code with the Python-based Hermes plugin.
- **Hook compatibility**: Claude Code hooks are shell commands. `uv run python -m ...` is a single shell invocation — no Node→Bun bridge, no runtime bootstrapping. Every hook, script, and MCP invocation uses `uv run`.

**Previously considered: TypeScript (Bun)** — Bun + TypeScript follows claude-mem's proven stack and was the initial choice. However, `uv` eliminates the historical Python deployment pain (no venv activation, no `pip` vs `uv` confusion) while keeping the stack unified with other BiBLE plugins in the monorepo. The BiBLE Atlas API is a straightforward REST interface easily consumed with `httpx`.

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

**Chosen: Local recovery + on-demand BiBLE retrieval (pull model)** — Two distinct recall paths, triggered by different actors at different times:

**Path 1 — SessionStart local recovery (hook-driven):**
When Claude Code triggers SessionStart (cold start, `/clear`, or compaction), the daemon injects context from the **local SQLite buffer**: recent turns summary, unflushed moments from the current session, and crash-recovery moments from unclosed sessions. No BiBLE Atlas call. This is a fast, local, zero-latency operation that restores what the model just lost.

**Path 2 — Mid-session on-demand retrieval (model-driven):**
During conversation, when the user mentions a topic, idea, or question that triggers a need for broader context, the model calls `bible_memory_search` or `bible_knowledge_search` via MCP tools. BiBLE Atlas returns relevant memories, knowledge, specifications, lessons learned — accumulated across projects and over time. This is a pull model: the search is driven by the user's real-time input, not speculative pre-fetching.

**Why pull instead of push:**
- SessionStart speculative search guesses relevance from a generic session-start message; mid-session search is driven by concrete, user-supplied search intent. Relevance is orders of magnitude higher.
- BiBLE Atlas stores not just memories but knowledge bases, specifications, and lessons learned — content that has no value unless specifically needed. A speculative injection wastes tokens and API calls on content the conversation may never touch.
- The MCP tools (`bible_memory_search`, `bible_knowledge_search`) become the primary mechanism for cross-session knowledge retrieval, not a backup for failed auto-injection.

**Previously considered: Speculative auto-inject at SessionStart** — This was the initial design, inspired by claude-mem which searches its local ChromaDB at session start. But claude-mem's search is a local vector query (fast, free). BiBLE Atlas is a remote API call — the cost/benefit calculus is different. Pull-on-demand avoids speculative waste while making every search count.

### Q8: Who detects key moments?

**Chosen: Plugin-side LLM (C)** — The daemon has its own LLM call (configurable model) to classify moments. Not heuristic regex, not delegated to BiBLE server's AI pipeline. This implies the daemon needs: buffering, prompt construction, LLM API integration, and structured output parsing.

### Q9: Hook-based capture or daemon-based?

**Chosen: Worker daemon, claude-mem style (C)** — Given plugin-side LLM detection, multi-user/team scenarios, and Claude Code sessions that can end abruptly or run in parallel, a persistent daemon with durable SQLite buffer is the only architecture that won't lose moments.

### Why Not The Alternatives

| Alternative | Why Rejected |
|---|---|
| **TypeScript (Bun)** | Originally chosen for claude-mem parity and deployment simplicity. Superseded by Python + uv which provides equivalent ergonomics (`uv run` ≈ `bun run`) without the Node→Bun bridge overhead (Q3). |
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
| **Context recall** | SessionStart: local buffer recovery. Mid-session: on-demand BiBLE Atlas pull via MCP tools. |
| **Moment detection** | Plugin-side LLM call (worker daemon) |

## Architecture

```
┌───────────────────────────────────────────────────────────-──┐
│                      Claude Code                             │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐    │
│  │  Setup   │  │SessionSt │  │UserPrompt │  │   Stop    │    │
│  │  Hook    │  │   art    │  │  Submit   │  │   Hook    │    │
│  │          │  │  Hook    │  │   Hook    │  │           │    │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └─────┬─────┘    │
│       │             │              │              │          │
│       │    ┌────────┼──────────────┼──────────────┘          │
│       │    │        │              │                         │
│       │    │   ┌────▼──────────────▼──────┐                  │
│       │    │   │  MCP Server (stdio)      │                  │
│       │    │   │  bible_memory_search     │                  │
│       │    │   │  bible_memory_save       │                  │
│       │    │   │  bible_knowledge_search  │                  │
│       │    │   │  ...                     │                  │
│       │    │   └────────────┬─────────────┘                  │
│       │    │                │                                │
│  ┌────┴────┴────────────────┼────────────────────────────┐   │
│  │  Commands (user-facing)  │                            │   │
│  │  /bible-cc:status        │  Daemon health             │   │
│  │  /bible-cc:push          │  Force-push moments        │   │
│  │  /bible-cc:consult       │  Search BiBLE Atlas        │   │
│  │  /bible-cc:review        │  Manage pending moments    │   │
│  └──────────┬───────────────┘                            │   │
│  └----------|---------------┘----------------------------┘   |
└─────────────┼───────────────────────────────────────────----─┘
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
| **Daemon** (`bible-cc-daemon`) | Buffer turns, detect key moments via LLM, flush to BiBLE, serve local context injection (buffer-based) | HTTP on `localhost:9777` | Persistent (managed by hooks) |
| **MCP Server** (`bible-cc-mcp`) | 7 BiBLE tools (memory search/save/get, knowledge search/list, skill search/get) | Stdio (MCP protocol) | Per Claude Code session |
| **Hooks** | Glue — start daemon, inject context, feed turns to daemon | Shell → HTTP calls to daemon | Event-driven |
| **Commands** | User-facing slash commands for manual control — push, consult, status, review | Shell → HTTP calls to daemon | On-demand (user invoked) |

### Key Design Decisions

- Daemon port `9777` (non-standard, avoid conflicts)
- SQLite at `~/.bible-cc/daemon.db` (per-user), using Python's stdlib `sqlite3`
- BiBLE Atlas URL configured once, shared by all components
- Skill tools excluded — Claude Code manages skills natively
- **Commands operate the daemon** (push, consult, status, review). **MCP tools query BiBLE Atlas** (search, get, list across all three domains — the primary mechanism for cross-session knowledge retrieval). The only overlap is `/bible-cc:consult` — a user-initiated BiBLE V4 hybrid search, complementing the model's automatic MCP tool searches.
- MCP tools: `bible_memory_search`, `bible_memory_save`, `bible_memory_get`, `bible_knowledge_search`, `bible_knowledge_list`, `bible_skill_search`, `bible_skill_get`
- User commands: `/bible-cc:push`, `/bible-cc:consult`, `/bible-cc:status`, `/bible-cc:review`

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
| Setup | `POST /daemon/start` | First-time install (write config, install deps). Not relied on for daemon lifecycle. |
| SessionStart | `POST /daemon/start` (idempotent) → `POST /session/start` → `POST /context/inject` | Self-contained: ensure daemon running, register session + crash recovery, inject local buffer context (turns + moments). No BiBLE call. |
| UserPromptSubmit | `POST /turn/user` | Feed user message to buffer (graceful skip if daemon unreachable) |
| PostToolUse | `POST /turn/tool` | Feed tool call to buffer (graceful skip if daemon unreachable) |
| Stop | `POST /session/end` | Trigger moment detection + flush to BiBLE |

---

## Daemon Design

### Daemon Startup

On `POST /daemon/start`, the daemon initializes in this order:

```
1. Open/create SQLite DB at ~/.bible-cc/daemon.db
2. PRAGMA journal_mode=WAL;          ← concurrent writers block-wait instead of throwing SQLITE_BUSY
3. PRAGMA busy_timeout=5000;         ← wait up to 5s for write locks before giving up
4. Run schema migration              ← CREATE TABLE IF NOT EXISTS for all tables
5. Scan unclosed sessions            ← crash recovery: detect sessions with status='active', trigger retrospective flush
6. Start FastAPI server              ← uvicorn on configured port (default :9777)
                                        ← if port occupied: fail + notify (default)
                                           or port+1 retry if port_auto_fallback=true
```

**Port conflict handling**: If the configured port is occupied, the daemon fails to start. The SessionStart hook script detects the failure and outputs an error hint via stdout — the same mechanism used for key moment detection, but with error highlighting. The hint appears inline in the conversation transcript. With `inject: true`, the message also enters the system prompt so the model knows the daemon is down:
```
⎿ ❌ bible-cc daemon failed to start on port 9777 (address in use).
    Run /bible-cc:status for details.
```
By default the user must resolve the conflict manually. Optionally, `daemon.port_auto_fallback: true` makes the daemon try port+1 repeatedly until it finds a free port.

**Why WAL mode matters**: Python's stdlib `sqlite3` defaults to `journal_mode=DELETE`, which serializes all writes — a second concurrent writer immediately gets `SQLITE_BUSY` and fails. This is unavoidable in a persistent daemon serving multiple Claude Code sessions. WAL mode allows concurrent reads and serializes writes with blocking-wait (within `busy_timeout`) instead of throwing. No connection pool or write-queue needed — two PRAGMA statements solve it.

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
                           → returns "<relevant-memories>..." from local buffer only
                           → sources: recent turns summary, unflushed moments, crash-recovery moments
                           → does NOT call BiBLE Atlas (mid-session pull via MCP tools instead)

POST /daemon/consult     — {session_id, query?}
                           → if query empty: LLM summarizes conversation into query
                           → calls BiBLE V4 hybrid search (memory + knowledge + skill)
                           → returns "<relevant-memories>..." from BiBLE Atlas results

GET  /daemon/moments?session_id=X   — list pending moments
DELETE /daemon/moments/{id}          — discard a pending moment
PUT  /daemon/moments/{id}            — edit title/abstract
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
    flushed       INTEGER DEFAULT 0, -- 0=pending, 1=sent to BiBLE
    content_hash  TEXT UNIQUE NOT NULL  -- SHA-256(session_id + title + narrative) for dedup
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
              │ Content-hash │  ← SHA-256(session_id + title + narrative)
              │    dedup     │     INSERT OR IGNORE (UNIQUE constraint)
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

### Why Dedup Matters — Three Failure Scenarios

Without dedup, the same key moment can enter BiBLE Atlas multiple times, polluting future recall with duplicate memories.

**Scenario A — Phase 2 re-detects Phase 1's moment:**

```
Turn 5-7:  User decides "use Redis for session store"
           → Phase 1 detects DECISION: "Redis for session store"
           → saved to moments (flushed=0)

Turn 20:   Session ends, Phase 2 scans all 20 turns
           → LLM sees the Redis discussion at turn 5-7
           → outputs DECISION: "Chose Redis for session storage"
           → same decision, two memories in BiBLE
```

**Scenario B — Phase 2 produces a variant of the same moment:**

```
Phase 1: DECISION "Redis for session store"
         narrative: "PostgreSQL was considered but Redis chosen for low-latency session reads"

Phase 2: DECISION "Session storage: Redis over Postgres"
         narrative: "Team decided Redis for session management due to latency requirements"
```

Different title, different narrative — but semantically identical. Without content-hash dedup, both survive.

**Scenario C — Phase 1 self-duplicate from overlapping windows:**

```
Window turns 3-5: contains "Redis for session store" → detects DECISION
Window turns 4-6: same decision still in context → detects DECISION again
```

Sliding windows with 2-3 turns of overlap mean the same conversation segment is seen twice. A moment in the overlap zone gets detected in both windows.

### Dedup Strategy (Two-Layer)

Moments are deduplicated at two layers, each addressing different failure modes:

**Layer 1 — Prompt injection (Phase 2 only)**:
Phase 2's retrospective prompt includes the list of moments already detected by Phase 1. The LLM is explicitly instructed to only report NEW moments not covered below. Addresses Scenario A (re-detection) and Scenario B (variants) — if the LLM knows what's already captured, it won't re-report it.

**Layer 2 — Content-hash (both phases)**:
Before inserting any moment, compute `SHA-256(session_id + moment_type + title + narrative)`. The `content_hash` column has a UNIQUE constraint, so `INSERT OR IGNORE` silently drops duplicates. This is the safety net — it catches Scenario C (Phase 1 self-duplicates from overlapping windows) and any duplicates that slip past Layer 1.

### Phase 2 Retrospective Prompt (sketch)

```
You are reviewing a COMPLETE conversation between a user and an AI agent.
The session has ended. Provide a synthesis.

The following key moments were ALREADY detected during the session.
Do NOT re-report them. Only report NEW moments not covered below:

{already_detected_moments_list}

Now review the full session and identify:
1. Overall session assessment — what was accomplished?
2. Any ADDITIONAL key moments missed by mid-session detection
3. What should be remembered for future sessions?

Key moment types (same as mid-session):
- DECISION: the user confirms a choice, approach, or design direction
- ACCOMPLISHMENT: something was completed, verified, and accepted

Do NOT flag:
- Intermediate bug fixes or error corrections
- Exploratory discoveries (unless user explicitly confirms importance)
```

### BiBLE HTTP Client

A thin Python module (`src/bible_cc_plugin/daemon/client.py`) wrapping the BiBLE Atlas REST API
using `httpx`. Covers all three domains:

- `search_memory()`, `save_memory()`, `get_memory()`
- `search_knowledge()`, `list_knowledge()`
- `search_skill()`, `get_skill()`

The BiBLE API is a straightforward REST interface — the client is ~100 lines of
Python. No need to share code with the Hermes plugin; the API
contract is the integration point.

---

## MCP Server Design

A thin wrapper around the BiBLE HTTP client (`src/bible_cc_plugin/daemon/client.py`), using
the `mcp` Python SDK. Stdio transport, launched per Claude Code session.

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
    "bible-cc": {
      "command": "uv",
      "args": ["run", "python", "-m", "bible_cc_plugin.mcp.server"],
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

Glue between Claude Code lifecycle events and the daemon. Two Python CLI entry
points invoked via `uv run`: `bible_cc_plugin.scripts.daemon` (lifecycle) and `bible_cc_plugin.scripts.hook`
(thin wrappers that call the daemon's HTTP API).

### Hook → Daemon Mapping

```
Setup ──────────────────────────────────────────────────────────────
  → uv run python -m bible_cc_plugin.scripts.setup     (first-time install)
  → writes ~/.bible-cc/config.json, starts daemon, verifies connectivity

SessionStart ───────────────────────────────────────────────────────
  → ensures daemon is running (idempotent start if needed)
  → POST /session/start  {session_id}               (register + crash recovery)
  → POST /context/inject {session_id, user_message}  (local buffer: turns + moments)
  → returns context string → injected into Claude's system prompt
  → does NOT call BiBLE Atlas (on-demand pull via MCP tools mid-session)
  → single hook invocation, generous timeout (60s) covers cold start

UserPromptSubmit ───────────────────────────────────────────────────
  → POST /turn/user  {session_id, message}
  → graceful skip if daemon unreachable (never blocks Claude Code)

PostToolUse ────────────────────────────────────────────────────────
  → POST /turn/tool  {session_id, tool_name, arguments, result_summary}
  → graceful skip if daemon unreachable (never blocks Claude Code)

Stop ───────────────────────────────────────────────────────────────
  → POST /session/end  {session_id}
  → daemon runs moment detection → flushes key moments to BiBLE
```

### hooks/hooks.json (sketch)

```json
{
  "hooks": {
    "Setup": [{
      "command": "uv run python -m bible_cc_plugin.scripts.setup",
      "timeout": 30000
    }],
    "SessionStart": [{
      "command": "uv run python -m bible_cc_plugin.scripts.hook session-start --session-id \"$CLAUDE_SESSION_ID\"",
      "timeout": 60000,
      "inject": true
    }],
    "UserPromptSubmit": [{
      "command": "uv run python -m bible_cc_plugin.scripts.hook turn-user --session-id \"$CLAUDE_SESSION_ID\" --message \"$USER_PROMPT\"",
      "timeout": 3000
    }],
    "PostToolUse": [{
      "command": "uv run python -m bible_cc_plugin.scripts.hook turn-tool --session-id \"$CLAUDE_SESSION_ID\" --tool \"$TOOL_NAME\" --output \"$TOOL_OUTPUT\"",
      "timeout": 3000
    }],
    "Stop": [{
      "command": "uv run python -m bible_cc_plugin.scripts.hook session-end --session-id \"$CLAUDE_SESSION_ID\"",
      "timeout": 30000
    }]
  }
}
```

Note: `$TOOL_OUTPUT` (not `$TOOL_RESULT`) is the standard Claude Code environment
variable for PostToolUse hook tool output.

- `bible_memory_save` (MCP tool) writes directly to BiBLE Atlas — no daemon notification
  needed. The daemon gets tool-call context from the PostToolUse hook (`/turn/tool`).
  Same-session re-injection of manually saved memories is correct behavior: when `/clear`
  or context compact triggers SessionStart, the model has lost context and re-injecting
  the memory restores it.
- `PostToolUse` sends full tool output to daemon. The daemon stores it complete
  in the turns table. Moment detector LLM extracts a ≤250 char精华摘要 as part
  of its normal detection run (configurable via `tool_result_max_chars`, default 250).

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

- "I just made an important decision — push this session NOW, don't wait for Stop."
- "What's the daemon doing? Is capture working? Is BiBLE reachable?"
- "The model's auto-search didn't find what I need — let me try myself."
- "I want to check my pending moments before they get flushed."

### Command Surface

#### `/bible-cc:push`

Force-push the current session's moments to BiBLE Atlas RIGHT NOW. Triggers moment
detection on buffered turns and flushes key moments immediately. With `--title` and
`--abstract`, bypasses auto-detection for precise user control.

Different from the `bible_memory_save` MCP tool: this operates on the daemon's
session buffer, not arbitrary user-provided messages.

```
Implementation: curl POST /daemon/session/flush {session_id, title?, abstract?}
```

#### `/bible-cc:consult [query]`

User-initiated cross-domain search against BiBLE Atlas V4 hybrid search endpoint.
Searches across all domains (memory + knowledge + skill) in a single API call.

- **With query**: searches BiBLE Atlas for the given query string.
- **Without query (enter)**: the daemon calls an LLM to summarize the current
  conversation into a search query, then searches BiBLE Atlas.

| Actor | Trigger | Search Interface | Purpose |
|-------|---------|-----------------|---------|
| Model (MCP tools) | Automatic, mid-session | BiBLE V4 hybrid search | On-demand pull during conversation |
| User (consult) | Manual, slash command | BiBLE V4 hybrid search | User suspects auto-search missed something |

Both use the same BiBLE V4 hybrid search API. The only difference is who decides
"now is the time to search." Consult gives the user agency when the model's
auto-pull didn't surface what they want.

Output is injected into the model's context (primary purpose). Display to user
is secondary and not required for V1.

```
Implementation: curl POST /daemon/consult {session_id, query?}
  → daemon: LLM summarize if no query → BiBLE V4 hybrid search → format as context block → return
```

#### `/bible-cc:status`

Show daemon health, uptime, active sessions, buffered turn count, pending moment
count, BiBLE Atlas connectivity, SQLite integrity, and schema version.

```
Implementation: curl GET /daemon/health (extended)
```

#### `/bible-cc:review`

Browse, edit, or discard pending moments before they're flushed. The user owns
their data — review gives them control over what gets persisted to BiBLE Atlas.

```
Implementation: curl GET /daemon/moments?session_id=X
                curl DELETE /daemon/moments/{id}
                curl PUT /daemon/moments/{id}
```

### Full Command Inventory

The complete screened command inventory (37 accepted out of 98 candidates) is
maintained in `docs/command-priority-table.md`. Key additions beyond the four
above include: `check-bible`, `context`, `config`/`config-set`, `capture-pause`/`resume`,
`recover`, `token-usage`, and memory management commands (`memory-duplicates`,
`memory-merge`, `memory-tag`, `memory-timeline`, `memory-graph`, `memory-fork`).

### What about domain search/browse?

Memory search, knowledge search, skill search, listing, and retrieval are **MCP tools** —
the model invokes them **mid-session, on demand**. The user expresses intent in natural
language ("find my memories about the auth refactor", "what knowledge bases do I have?"),
and the model calls the appropriate tool. No slash commands needed.

Consult (`/bible-cc:consult`) is the only user-initiated search command — it exists
as a manual escape hatch for when the model's auto-pull doesn't surface what the user wants.
Both MCP tools and consult call the same BiBLE V4 hybrid search API.

### Command Implementation

Commands are thin shell wrappers — no new backend logic. Each is a one-liner that
either calls a Python script (for interactive flows) or curls the daemon (for status ops).

```
/bible-cc:push     → uv run python -m bible_cc_plugin.scripts.hook session-flush --session-id "$CLAUDE_SESSION_ID"
/bible-cc:consult  → curl -s -X POST http://127.0.0.1:9777/daemon/consult -d '{"session_id":"$CLAUDE_SESSION_ID","query":"..."}'
/bible-cc:status   → curl -s http://127.0.0.1:9777/daemon/health | python -m json.tool
/bible-cc:review   → curl -s http://127.0.0.1:9777/daemon/moments?session_id="$CLAUDE_SESSION_ID"
```

### commands/*.md (sketch)

```markdown
<!-- /bible-cc:push -->
# Push current session moments to BiBLE Atlas

Force-push the current session buffer's key moments to BiBLE Atlas.
Triggers moment detection on buffered turns and flushes key moments immediately.
Use --title and --abstract to provide your own summary instead of auto-detection.

Usage: /bible-cc:push [--title "Decision: use TypeScript"] [--abstract "We decided..."]
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
take precedence. JSON is chosen over YAML to avoid an extra dependency — Python's
stdlib `json` module handles parsing with zero additional packages.

### Schema

```json
{
  "bible": {
    "base_url": "http://localhost:5555",
    "token": null
  },
  "daemon": {
    "port": 9777,
    "port_auto_fallback": false,
    "db_path": "~/.bible-cc/daemon.db"
  },
  "injection": {
    "enabled": true,
    "token_budget": 1200,
    "include_turns_summary": true,
    "include_moments": true,
    "crash_recovery_moments": true
  },
  "search": {
    "default_top_k": 8,
    "default_min_score": 0.35
  },
  "capture": {
    "enabled": true,
    "mode": "key_moments",
    "commit_threshold_turns": 8,
    "commit_threshold_chars": 16000,
    "tool_result_max_chars": 250
  },
  "detection": {
    "model": "deepseek-v4-flash",
    "max_tokens": 512,
    "temperature": 0.0
  },
  "bypass": {
    "session_patterns": []
  }
}
```

**`injection`** — controls SessionStart local buffer context injection (no BiBLE call):
- `enabled`: if false, skip injection entirely (model starts cold after `/clear`/compact)
- `token_budget`: max tokens for the injected `<relevant-memories>` block
- `include_turns_summary`: include a summary of recent buffered turns
- `include_moments`: include unflushed moments from the current session
- `crash_recovery_moments`: include moments from unclosed prior sessions

**`search`** — controls default parameters for MCP tool BiBLE Atlas searches (mid-session on-demand pull):
- `default_top_k`: default number of results returned by `bible_memory_search` and `bible_knowledge_search`
- `default_min_score`: default minimum relevance score threshold

Env var overrides: `BIBLE_ATLAS_BASE_URL`, `BIBLE_ATLAS_TOKEN`, `BIBLE_CC_DAEMON_PORT`,
`BIBLE_CC_DB_PATH`.

### Setup flow

```
uv run python -m bible_cc_plugin.scripts.setup
  → prompts for BiBLE base URL
  → writes ~/.bible-cc/config.json
  → starts daemon
  → verifies connectivity to BiBLE Atlas
```

### Credentials

- **BiBLE Atlas**: `BIBLE_ATLAS_TOKEN` env var or `bible.token` in config.json
- **LLM (moment detection)**: Daemon inherits `ANTHROPIC_API_KEY` from Claude Code's
  environment. The `anthropic` Python SDK auto-detects this.

---

## Package Structure

```
bible-cc-plugin/
├── pyproject.toml                  ← uv dependencies + entry points
├── .claude-plugin/
│   └── plugin.json                 ← .claude-plugin manifest
├── .mcp.json                       ← MCP server discovery（由 setup.py 生成，不提交 git）
├── .gitignore
├── LICENSE
├── hooks/
│   └── hooks.json                  ← hook definitions
├── commands/                       ← user-facing slash commands
│   ├── status.md                   ← /bible-cc:status
│   ├── push.md                     ← /bible-cc:push
│   ├── consult.md                  ← /bible-cc:consult
│   ├── review.md                   ← /bible-cc:review
│   └── help.md                     ← /bible-cc:help
├── src/bible_cc_plugin/
│   ├── daemon/
│   │   ├── server.py               ← FastAPI HTTP server (:9777)
│   │   ├── buffer.py               ← SQLite session/turn/moment store (sqlite3)
│   │   ├── detector.py             ← LLM moment detection (anthropic SDK)
│   │   ├── injector.py             ← context injection (local buffer: turns + moments)
│   │   └── client.py               ← BiBLE HTTP client (httpx)
│   ├── mcp/
│   │   └── server.py               ← MCP stdio server (mcp Python SDK)
│   ├── config.py                   ← config loading (JSON + env var overrides)
│   └── types.py                    ← shared types (Pydantic models)
├── scripts/
│   ├── daemon.py                   ← daemon lifecycle CLI (start/stop/status)
│   ├── hook.py                     ← hook bridge (calls daemon HTTP endpoints)
│   └── setup.py                    ← interactive setup wizard
└── tests/
    ├── test_daemon.py
    ├── test_buffer.py
    ├── test_detector.py
    ├── test_mcp.py
    └── test_client.py
```

**Key dependencies:**
- `fastapi` + `uvicorn` — daemon HTTP server
- `mcp` — MCP server (Python SDK)
- `anthropic` — LLM calls for moment detection
- `httpx` — async HTTP client to BiBLE Atlas
- `pydantic` — config/types validation
- `sqlite3` — stdlib, no package needed

**Distribution:** `uv sync` — single command. User then adds the plugin directory
to Claude Code's plugin registry. Configuration via `uv run python -m bible_cc_plugin.scripts.setup` wizard.
No venv activation, no Python version management, no `pip` vs `uv` confusion.
