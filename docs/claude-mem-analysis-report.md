# claude-mem Plugin — Complete Analysis Report

> Date: 2026-06-12 | Version: 13.4.1 | Author: thedotmack (Alex Newman)

## Overview

**claude-mem** is a Claude Code plugin that provides persistent memory, context compression, and knowledge management. It's the most mature plugin in the Claude Code ecosystem, serving as the architectural reference for bible-cc-plugin.

- **Language**: TypeScript/JavaScript, **Bun runtime**
- **License**: Apache-2.0
- **Status on this machine**: Installed (v13.4.1), currently **disabled** in settings

---

## 1. Directory Structure

```
claude-mem/
├── .claude-plugin/plugin.json      # Plugin manifest for Claude Code marketplace
├── .codex-plugin/plugin.json       # Plugin manifest for Codex CLI
├── .mcp.json                       # MCP server definitions (inline minified JS)
├── bun.lock                        # Bun lockfile
├── package.json                    # Runtime dependencies
├── package-lock.json
├── node_modules/                   # Installed via `bun install --production`
├── hooks/
│   ├── hooks.json                  # Claude Code hooks
│   ├── codex-hooks.json            # Codex CLI hooks
│   └── bugfixes-2026-01-10.md
├── modes/                          # 37 observation mode definitions
│   ├── code.json                   # Base "Code Development" mode
│   ├── code--ar.json … code--zh.json  # 31 language variants
│   ├── law-study.json              # "Law Study" mode
│   ├── law-study--chill.json       # Variant
│   ├── law-study-CLAUDE.md
│   ├── email-investigation.json
│   └── meme-tokens.json
├── scripts/
│   ├── bun-runner.js               # Node → Bun bridge launcher
│   ├── worker-service.cjs          # Bundled main daemon (2,388 lines)
│   ├── worker-cli.js               # Process manager (start/stop/restart/status)
│   ├── worker-wrapper.cjs          # Supervised worker wrapper
│   ├── mcp-server.cjs              # Bundled MCP server (246 lines)
│   ├── context-generator.cjs       # Bundled context generator (808 lines)
│   ├── server-beta-service.cjs     # Bundled server-beta runtime (9,844 lines)
│   ├── version-check.js            # Setup hook — install deps, verify
│   ├── statusline-counts.js        # Status line observation counters
│   └── transcript-watcher.cjs      # Transcript watcher
├── skills/                         # 20 skills (user-invocable slash commands)
│   ├── mem-search/SKILL.md         # Cross-session memory search
│   ├── knowledge-agent/SKILL.md    # Queryable knowledge corpus
│   ├── smart-explore/SKILL.md      # AST-based code exploration
│   ├── pathfinder/SKILL.md         # Codebase architecture mapping
│   ├── design-is/SKILL.md          # Dieter Rams design audit
│   ├── make-plan/SKILL.md          # Multi-phase implementation plan
│   ├── do/SKILL.md                 # Execute plans with sub-agents
│   ├── standup/
│   │   ├── SKILL.md                # Multi-agent chat coordinator
│   │   ├── agent-brief.md          # Agent behavior guide
│   │   └── standup.mjs             # Zero-dependency chat CLI (662 lines)
│   ├── babysit/SKILL.md
│   ├── how-it-works/
│   │   ├── SKILL.md
│   │   └── onboarding-explainer.md
│   ├── learn-codebase/SKILL.md
│   ├── oh-my-issues/SKILL.md
│   ├── timeline-report/SKILL.md
│   ├── version-bump/SKILL.md
│   ├── weekly-digests/SKILL.md
│   └── wowerpoint/SKILL.md
└── ui/
    ├── viewer.html                 # Browser-based memory viewer
    ├── viewer-bundle.js
    └── assets/fonts/
```

---

## 2. Dependencies

### package.json
```json
{
  "name": "claude-mem-plugin",
  "version": "13.4.1",
  "type": "module",
  "engines": { "node": ">=18.0.0", "bun": ">=1.0.0" }
}
```

**34 runtime dependencies**, dominated by **23 tree-sitter language parsers** used by the `smart-explore` skill for AST-based code analysis:
- bash, c, cpp, css, elixir, go, haskell, java, javascript, kotlin, lua, markdown, php, python, ruby, rust, scala, scss, sql, swift, toml, typescript, yaml, zig
- `shell-quote` (v1.8.3), `zod` (v4.4.3), `tree-sitter-cli` (v0.26.5)

**Key insight**: Application logic is NOT in node_modules. All business logic is bundled into self-contained `.cjs` files (worker-service, mcp-server, context-generator) with zero external dependencies.

---

## 3. How It's Installed Into Claude Code

### Installation Flow
```
User: npx @anthropic-ai/claude-code plugins install thedotmack/claude-mem
  ↓
Claude Code Marketplace:
  1. Registers in ~/.claude/plugins/installed_plugins.json
     { "claude-mem@thedotmack": { "version": "13.4.1", ... } }
  2. Clones source to ~/.claude/plugins/marketplaces/thedotmack/plugin/
  3. Extracts runtime to ~/.claude/plugins/cache/thedotmack/claude-mem/13.4.1/
  4. Plugin loaded — hooks fire on next session
```

### Setup Hook (auto-runs after install)
```
Setup hook (300s timeout):
  → runs version-check.js
  → checks .install-version marker
  → if node_modules/ missing: runs `bun install --production`
  → if version mismatch: prompts `npx claude-mem@latest install`
  → always exits 0 (never blocks)
```

### Enable/Disable
```json
// ~/.claude/settings.json
{
  "enabledPlugins": {
    "claude-mem@thedotmack": true   // set to false to disable
  }
}
```
When disabled, `bun-runner.js` returns `process.exit(0)` before doing anything.

---

## 4. Architecture: How It Works End-to-End

### The Four Surfaces

| Surface | Actor | Transport | Purpose |
|---|---|---|---|
| **Hooks** | Claude Code lifecycle | Shell → Node → Bun | Auto-capture, context injection, daemon lifecycle |
| **Worker Daemon** | Hooks + MCP Server | HTTP :37777 | SQLite storage, observation generation, context ranking |
| **MCP Server** | LLM (model-invoked) | Stdio | Search, timeline, corpus management, code exploration |
| **Skills** | User (slash commands) | SKILL.md → model | High-level workflows (mem-search, standup, etc.) |

### Hook Lifecycle

| Hook | Matcher | Action | Timeout | Purpose |
|---|---|---|---|---|
| **Setup** | `*` | `version-check.js` → auto-install deps | 300s | First-time setup |
| **SessionStart** | `startup\|clear\|compact` | 1. Start daemon 2. Inject context | 60s | Boot daemon + load memory |
| **UserPromptSubmit** | (all) | Log current prompt, init session | 60s | Session tracking |
| **PreToolUse** | `Read` | Capture file being read | 60s | Spatial context |
| **PostToolUse** | `*` | Compress tool use → observation | 120s | Core capture pipeline |
| **Stop** | (all) | Generate session summary | 120s | Session closure |

### The Node → Bun Bridge

```
Claude Code hooks (run in bash)
  ↓
bash script: locate plugin dir, resolve PATH
  ↓
node scripts/bun-runner.js scripts/worker-service.cjs <action>
  ↓
bun-runner.js: finds `bun` binary, spawns:
  ↓
bun worker-service.cjs start    ← long-running daemon (HTTP :37777)
bun worker-service.cjs hook claude-code context
bun worker-service.cjs hook claude-code observation
bun worker-service.cjs hook claude-code session-init
bun worker-service.cjs hook claude-code summarize
```

**Why the bridge?** Claude Code hooks spawn Node by default. The plugin uses Bun for its built-in SQLite and HTTP server. `bun-runner.js` (a Node script) locates the Bun binary and spawns the worker.

---

## 5. Worker Daemon (worker-service.cjs)

A 2,388-line bundled Bun application. Contains all daemon functionality in a single file.

### HTTP API (localhost:37777)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Liveness probe |
| `/api/readiness` | GET | Readiness probe (startup sync) |
| `/api/admin/shutdown` | POST | Graceful shutdown |
| `/api/search` | GET | Semantic + keyword memory search |
| `/api/timeline` | GET | Timeline context around observations |
| `/api/context` | multiple | Context injection |
| `/api/observations/batch` | POST | Batch fetch observation details |
| `/api/corpus` | multiple | Knowledge corpus management |
| `/api/corpus/:name/prime` | POST | Prime corpus for querying |
| `/api/corpus/:name/query` | POST | Query primed corpus |

### Port Calculation
```
port = env CLAUDE_MEM_WORKER_PORT
       || settings.CLAUDE_MEM_WORKER_PORT
       || 37700 + (uid % 100)
```
Default: 37777 (for uid 77, typically the first user). Multi-user systems get different ports.

### Two Runtime Modes

| Mode | Storage | When |
|---|---|---|
| **worker** (default) | Local SQLite + ChromaDB vector | Running locally |
| **server-beta** | Remote REST API | `CLAUDE_MEM_SERVER_BETA_URL` is set |

---

## 6. MCP Server (mcp-server.cjs)

246-line bundled MCP server, launched via `.mcp.json` as a stdio process.

### Discovery (`.mcp.json`)

```json
{
  "mcpServers": {
    "mcp-search": {
      "command": "node",
      "args": ["-e", "<minified auto-discovery JS>"],
      "env": { "CLAUDE_MEM_WORKER_PORT": "37777" }
    }
  }
}
```

The inline JS auto-discovers the plugin directory by checking:
1. `CLAUDE_PLUGIN_ROOT` env var
2. `./plugin/` in working directory
3. Version-sorted scan of `~/.claude/plugins/cache/thedotmack/claude-mem/`
4. Fallback to `~/.claude/plugins/marketplaces/thedotmack/plugin/`

Then spawns: `node <path>/scripts/mcp-server.cjs`

### MCP Tools

**Core Memory Tools:**
| Tool | Purpose |
|---|---|
| `search` | Semantic + keyword search across all memories |
| `timeline` | Get conversation context around an observation |
| `get_observations` | Fetch full observation details by IDs |

**Server-Beta Tools** (only when server-beta is configured):
| Tool | Purpose |
|---|---|
| `observation_add`, `observation_search`, `observation_context` | Remote memory CRUD |
| `observation_record_event` | Record agent events |

**Corpus Management:**
| Tool | Purpose |
|---|---|
| `build_corpus` | Build a knowledge corpus from observations |
| `list_corpora` | List all corpora and their status |
| `prime_corpus` | Load a corpus into AI session |
| `query_corpus` | Ask questions against a primed corpus |

**Code Exploration** (Tree-sitter AST — requires node_modules):
| Tool | Purpose |
|---|---|
| `smart_search` | Find code symbols across directories |
| `smart_outline` | Get structured outline of a file |
| `smart_unfold` | Unfold implementation of a symbol |

---

## 7. SQLite Schema

Database: `~/.claude-mem/claude-mem.db`

### Core Tables

**sdk_sessions** — one per Claude Code session:
- `id` (PK), `content_session_id` (UNIQUE), `memory_session_id` (UNIQUE)
- `project` (TEXT), `platform_source` (TEXT, default 'claude')
- `user_prompt`, `custom_title`
- `started_at`, `started_at_epoch`, `completed_at`, `completed_at_epoch`
- `status` ('active'|'completed'|'failed')
- `worker_port`, `prompt_counter`

**observations** — each tool use compressed into one structured record:
- `id` (PK), `memory_session_id` (FK), `project` (TEXT)
- `text` (TEXT, nullable), `type` (TEXT: bugfix/feature/discovery/decision/etc.)
- `title`, `subtitle`, `facts`, `narrative`, `concepts`
- `files_read`, `files_modified`
- `prompt_number`, `discovery_tokens`
- `content_hash` (TEXT) — for UPSERT dedup
- `generated_by_model`, `relevance_count`
- `agent_type`, `agent_id`, `metadata` (TEXT)
- `merged_into_project`
- `created_at`, `created_at_epoch`

**session_summaries** — per-prompt checkpoint summaries:
- `id` (PK), `memory_session_id` (FK), `project` (TEXT)
- `request`, `investigated`, `learned`, `completed`, `next_steps`
- `files_read`, `files_edited`, `notes`
- `prompt_number`, `discovery_tokens`

**user_prompts** — with FTS5 full-text search:
- `id` (PK), `content_session_id` (FK)
- `prompt_number`, `prompt_text`
- FTS5 virtual table: `user_prompts_fts`

**pending_messages** — async hook event queue:
- `id` (PK), `session_db_id` (FK), `content_session_id` (TEXT)
- `message_type` ('observation'|'summarize')
- `tool_name`, `tool_input`, `tool_response`, `cwd`
- `last_user_message`, `last_assistant_message`
- `prompt_number`, `tool_use_id`
- `status` ('pending'|'processing')
- `agent_type`, `agent_id`

**schema_versions** — migration tracking (32 versions applied incrementally).

---

## 8. Observation Pipeline (How Capture Works)

```
1. PostToolUse hook fires
   → bash runs: node bun-runner.js worker-service.cjs hook claude-code observation
   → payload: tool_name, tool_input, tool_response (truncated), cwd

2. Worker receives payload
   → inserts row into pending_messages (status: pending)
   → async worker picks it up

3. Observation Generation
   → builds LLM prompt with:
     - Current tool use (name, input, truncated output)
     - Recent user/assistant messages for context
     - Active mode configuration (code.json etc.)
   → calls Claude API (model: CLAUDE_MEM_MODEL, default claude-sonnet-4-5)
   → parses structured output: type, title, subtitle, facts, narrative, concepts

4. Dedup check
   → content_hash = SHA-256(memory_session_id + title + narrative)
   → INSERT OR IGNORE — same observation never stored twice

5. Saved to observations table
   → ready for future context injection
```

### What gets captured vs ignored:
- **Captured**: bugfix, feature, refactor, change, discovery, decision, security_alert
- **Ignored**: tools listed in `CLAUDE_MEM_SKIP_TOOLS` (default: `ListMcpResourcesTool,SlashCommand,Skill,TodoWrite,AskUserQuestion`)
- **Configurable** via `CLAUDE_MEM_CONTEXT_OBSERVATION_TYPES`

---

## 9. Context Injection (How Recall Works)

```
SessionStart hook fires:
  → POST /api/context
  → context-generator.cjs runs:
    1. Determines project name (handles worktree detection)
    2. Queries observations table for this project
    3. Relevance-scores observations (recency + usage count + concept match)
    4. Selects top-N (default 50 observations)
    5. Formats as structured markdown with sections:
       - Recently investigated, learned, completed, next steps
       - Relevant past observations with titles, narratives, concepts
    6. Truncates to token budget
    7. Returns formatted string → injected into Claude Code session
```

Configured via:
- `CLAUDE_MEM_CONTEXT_OBSERVATIONS` (default: 50)
- `CLAUDE_MEM_CONTEXT_FULL_COUNT` (default: 5)
- `CLAUDE_MEM_CONTEXT_SESSION_COUNT` (default: 10)

---

## 10. Configuration

### Config File
`~/.claude-mem/settings.json`

### Key Settings
| Setting | Default | Purpose |
|---|---|---|
| `CLAUDE_MEM_MODEL` | `claude-sonnet-4-5` | Model for observation generation |
| `CLAUDE_MEM_CONTEXT_OBSERVATIONS` | `50` | Max observations in context |
| `CLAUDE_MEM_WORKER_PORT` | `37777` | Daemon HTTP port |
| `CLAUDE_MEM_WORKER_HOST` | `127.0.0.1` | Daemon bind address |
| `CLAUDE_MEM_SKIP_TOOLS` | `ListMcpResourcesTool,SlashCommand,...` | Tools to ignore |
| `CLAUDE_MEM_PROVIDER` | `claude` | Compression provider |
| `CLAUDE_MEM_DATA_DIR` | `~/.claude-mem` | Data root |
| `CLAUDE_MEM_MODE` | `code` | Active observation mode |
| `CLAUDE_MEM_CONTEXT_OBSERVATION_TYPES` | `bugfix,feature,...` | Types to include |
| `CLAUDE_MEM_CONTEXT_FULL_COUNT` | `5` | Observations with full details |

### Data Directory Layout (`~/.claude-mem/`)
```
claude-mem.db          ← Main SQLite database
settings.json          ← Config
worker.pid             ← Daemon PID
logs/                  ← Daily logs (worker + plugin)
chroma/                ← ChromaDB vector index
corpora/               ← Knowledge corpus data
archives/, trash/, backups/
```

---

## 11. Skills System

Skills are markdown files with YAML frontmatter. When the user types `/skill-name`, Claude Code loads the SKILL.md and the model follows its instructions.

### Key Skills

**mem-search** — 3-tier search workflow:
1. `search` → find candidate observations
2. `timeline` → get context around each
3. `get_observations` → fetch full details for filtered hits

**smart-explore** — AST-based code exploration:
- Uses tree-sitter MCP tools (`smart_search`, `smart_outline`, `smart_unfold`)
- "Index first, fetch on demand" → 4-18× token reduction vs reading files

**knowledge-agent** — Build queryable corpuses from observations:
- `build_corpus` → `prime_corpus` → `query_corpus`
- Each corpus is an independent AI session

**standup** — Multi-agent chat via file-based rooms:
- `standup.mjs`: 662-line Node CLI with lock-based chat files
- `agent-brief.md`: behavior guide for each agent in the room

---

## 12. Key Design Decisions (relevant for bible-cc-plugin)

| Decision | Rationale |
|---|---|
| **Bun runtime** | Built-in SQLite, HTTP server, fast startup. Node bridge (`bun-runner.js`) handles hook compatibility. |
| **Single-file bundles** | `worker-service.cjs` (2,388 lines), `mcp-server.cjs` (246 lines). All logic self-contained, no module resolution at runtime. |
| **SQLite + ChromaDB** | SQLite for structured data, ChromaDB for vector search, FTS5 for full-text fallback. |
| **Observation compression** | Don't store raw tool output. LLM compresses each use into structured observation (type, title, facts, narrative, concepts). |
| **Content-hash dedup** | SHA-256 of session_id + title + narrative = idempotent. Re-running same tool doesn't create duplicate memories. |
| **Async processing** | Tool observations go into `pending_messages` queue. Worker processes them async — never blocks Claude Code's response time. |
| **Mode system** | Observation types and concepts change per domain (code vs law vs email). Modes define the compression schema. |
| **Multi-platform** | Two hook files: `hooks.json` (Claude Code) + `codex-hooks.json` (Codex CLI). Tracks `platform_source` per session. |
| **Skills as workflows** | Skills instruct the model, not execute shell commands. Model uses MCP tools to fulfill the skill's workflow. |
| **Port calculation** | `37700 + (uid % 100)` — avoids conflicts in multi-user systems. |

---

## 13. What bible-cc-plugin Can Learn

### Adopt from claude-mem:
1. **Bun runtime + single-file bundles** — eliminates venv, pip, Python version issues
2. **Node → Bun bridge pattern** — `bun-runner.js` is a clean solution for hook compatibility
3. **Daemon as central hub** — hooks, MCP, commands all talk to one HTTP daemon
4. **Skills as model instructions** — not shell scripts; the model uses MCP tools
5. **Async observation pipeline** — queue tool results, process offline, never block
6. **Content-hash dedup** — idempotent saves prevent memory pollution
7. **Port calculation by UID** — multi-user safety by default

### Don't adopt from claude-mem:
1. **34 tree-sitter dependencies** — bible-cc doesn't need code exploration; BiBLE handles search
2. **ChromaDB vector store** — BiBLE Atlas already has OpenSearch with vector search
3. **Self-built LLM compression pipeline** — BiBLE's server-side AI pipeline can handle this
4. **Server-beta mode** — unnecessary complexity for a single-backend plugin
5. **23 language parsers** — the plugin is a bridge to BiBLE, not a code analysis tool
