# 01 — Architecture Overview

> L1 | 全景 | 本文是 SW design 的根文档。定义了组件边界、数据流向、设计原则和硬性约束。所有 L2/L3 文件不得与本文冲突。

---

## 1. 四组件模型

bible-cc-plugin 由四个独立的、通过明确协议通信的组件构成。

```
┌──────────────────────────────────────────────────────────────┐
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
│  ┌────┴────┴────────────────┼────────────────────────────────┤
│  │  Commands (user-facing)  │                                │
│  │  /bible-cc:status        │  Daemon health                 │
│  │  /bible-cc:push          │  Force-push moments            │
│  │  /bible-cc:consult       │  Search BiBLE Atlas            │
│  │  /bible-cc:review        │  Manage pending moments        │
│  │  /bible-cc:help           │  List all commands             │
│  └──────────┬───────────────┘                                │
└─────────────┼───────────────────────────────────────────────-┘
              │
              ▼
┌──────────────────────────────────────────────┐
│         BiBLE CC Daemon (HTTP :9777)          │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌─────────────┐ │
│  │  Session │  │  Moment  │  │   Context   │ │
│  │  Buffer  │  │ Detector │  │   Injector  │ │
│  │ (SQLite) │  │  (LLM)   │  │             │ │
│  └────┬─────┘  └────┬─────┘  └──────┬──────┘ │
│       │             │               │        │
└───────┼─────────────┼───────────────┼────────┘
        │             │               │
        ▼             ▼               ▼
┌──────────────────────────────────────────────┐
│            BiBLE Atlas Server                │
│  (memory / knowledge search, save, import)   │
└──────────────────────────────────────────────┘
```

| Component | Role | Transport | Lifetime |
|-----------|------|-----------|----------|
| **Daemon** | Buffer turns, detect key moments via LLM (Phase 1/2), flush to BiBLE Atlas, serve local context injection | HTTP `localhost:9777` | Persistent（跨 session） |
| **MCP Server** | Expose 6 BiBLE tools to the model (memory search/save/get, knowledge search, skill search/get) + 2 postponed | Stdio (MCP) | Per Claude Code session |
| **Hooks** | Bridge Claude Code lifecycle events → daemon HTTP API. Start daemon, inject context, feed turns. | Shell → HTTP to daemon | Event-driven |
| **Commands** | User-initiated slash commands for daemon control（push, consult, status, review）and plugin management | Shell → HTTP to daemon | On-demand |

---

## 2. Context Recall：Pull Model

Context recall 分为两条路径，由不同的 actor 在不同的时机触发。

```
SessionStart (hook-driven)              Mid-session (model-driven)
  │                                        │
  ▼                                        ▼
POST /context/inject                  MCP tools invoke
  → local SQLite ONLY                   → BiBLE V4 domain search endpoints
  → turns summary                       → memory + knowledge + skill
  → unflushed moments                  → cross-session knowledge
  → crash-recovery moments             → on-demand, intent-driven
  → zero BiBLE call
```

**核心原则**：`/context/inject` 永远只看本地 buffer。跨 session 知识发现由模型在对话中通过 MCP 工具主动 pull。唯一的例外是 `/bible-cc:consult`——用户觉得自动 pull 不够时手动触发，由 client 并行调用三域 search 端点后合并结果。

**三种 SessionStart 场景**：

| 场景 | 本地 Buffer 状态 | `/context/inject` 注入内容 |
|------|-----------------|--------------------------|
| 全新 session，无 crash 遗留 | 空 | 空 `<relevant-memories>` |
| `/clear` 或 compaction（同 session） | 有 turns + unflushed moments | turns 摘要 + unflushed moments |
| 全新 session，有 crash 遗留 | 空，但 prior session 有未 flush 数据 | crash recovery moments（快路 SQLite）+ turns 摘要 |

---

## 3. Data Flow（全链路）

### 3.1 采集链路：hook → buffer → detect → flush

```
UserPromptSubmit hook
  → POST /turn/user {session_id, message}
    → daemon inserts turn into SQLite turns table
    → if accum(turns, chars) >= threshold → queue Phase 1 detection task
      → daemon worker: build prompt from last 2-3 turns → LLM call → parse moment
      → content-hash dedup (SHA-256(session_id + title + narrative), INSERT OR IGNORE)
      → save to moments table (flushed=0)
      → if mid_session_upload: POST to BiBLE (flushed=1)
      → print hint to transcript via hook stdout
    → daemon returns immediately（non-blocking）

PostToolUse hook → same flow via POST /turn/tool
  → full tool output stored in turns table（no mechanical truncation）
  → LLM extracts ≤250 char精华摘要 as part of detection run
```

### 3.2 结束链路：retrospective + flush

```
Stop hook
  → POST /session/end {session_id}
    → Phase 2 retrospective detection:
      → prompt sees ALL turns + Phase 1's already-detected moments（instructed NOT to re-report）
      → LLM call → overall assessment + NEW missed key moments
    → content-hash dedup on all new moments
    → bundle all unflushed moments + retrospective → POST to BiBLE Atlas as single import
    → mark session closed
```

### 3.3 SessionStart 链路：recovery + injection

```
SessionStart hook
  → ensure daemon running (idempotent POST /daemon/start)
  → POST /session/start {session_id}
    → scan for unclosed sessions (crash recovery):
      fast path: read existing unflushed moments from SQLite（毫秒级）
      slow path: queue Phase 2 retrospective + flush for unclosed sessions（异步，完成后 hint）
    → create new session row
  → POST /context/inject {session_id, user_message}
    → query local buffer → returns <relevant-memories> string
    → inject into system prompt (hook stdout, inject: true)
```

---

## 4. 设计原则

| # | 原则 | 含义 |
|---|------|------|
| 1 | **本地优先** | 本地 SQLite buffer 是 primary source。BiBLE Atlas 是跨 session 补充。 |
| 2 | **Pull 优于 Push** | 不 speculative 搜索 BiBLE。由对话意图驱动，模型通过 MCP 工具主动 pull。 |
| 3 | **永不阻塞 Claude Code** | Daemon 或 BiBLE 不可用时，hooks 静默跳过。插件故障不得影响 Claude Code 的正常运行。 |
| 4 | **用户主权** | Pending moments 可 review/edit/discard。已 flush 到 BiBLE 的记忆不可修改（只能 delete）。 |
| 5 | **数据采集不占命令** | Token、perf 等指标由 daemon 在后台采集，随 push 发送到 BiBLE。Server dashboard 展示。不占用用户的命令表。 |
| 6 | **去重是 must-have** | 所有 moment insert 前计算 content-hash，INSERT OR IGNORE。Phase 2 prompt 注入已知 moments 列表。两层去重。 |

---

## 5. 硬性约束

以下约束对所有 L2/L3 文件具有强制力：

1. **SQLite WAL**：daemon 启动时必须执行 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`。无例外。
2. **Content-hash dedup**：moments 表 `content_hash TEXT UNIQUE NOT NULL`。所有 insert 前计算 `SHA-256(session_id + title + narrative)`。
3. **Hook timeout**：UserPromptSubmit/PostToolUse ≤ 3000ms（纯 HTTP + 排队，检测异步）。SessionStart ≤ 60000ms（覆盖冷启动）。Stop ≤ 30000ms。
4. **`uv run` 统一入口**：所有 hooks、scripts、MCP 调用使用 `uv run python -m ...`。禁止 `source .venv/bin/activate`。
5. **Graceful degradation**：BiBLE 不可达时本地操作不受影响。Daemon 不可达时 UserPromptSubmit/PostToolUse hooks 静默跳过。
6. **`$TOOL_OUTPUT` 非 `$TOOL_RESULT`**：PostToolUse hook 环境变量。
7. **`.mcp.json` 字面量**：env 值不得使用 shell-default 语法。

---

## 6. 模块索引

| 模块 | L2 文件 | L3 子文件 |
|------|---------|----------|
| Daemon | [`03-daemon.md`](03-daemon.md) | startup, sqlite-schema, port-conflict, http-api |
| Config | [`04-config.md`](04-config.md) | schema |
| Capture Pipeline | [`05-capture-pipeline.md`](05-capture-pipeline.md) | hook-flow, detection, flush |
| Recall Pipeline | [`06-recall-pipeline.md`](06-recall-pipeline.md) | local-injection, consult, mcp-tools |
| Commands | [`07-commands.md`](07-commands.md) | specs |
| Operability | [`08-operability.md`](08-operability.md) | hint-system, status, failure-paths |
| Monitoring | [`09-monitoring.md`](09-monitoring.md) | data-collection |
| Deployment | [`10-deployment.md`](10-deployment.md) | upgrade |
| Testing | [`11-testing.md`](11-testing.md) | unit, integration, e2e |

---

## 7. 参考文档

- [`docs/bible-claude-code-plugin-feasibility-report.md`](../bible-claude-code-plugin-feasibility-report.md) — 架构设计、决策历程、配置 schema
- [`CLAUDE.md`](../../CLAUDE.md) — 项目约束、Key Rules、SW Design 编写规则
- [`docs/command-priority-table.md`](../command-priority-table.md) — 完整命令清单（含优先级、MVP 范围、server/plugin 边界）
- [`docs/design-review-2026-06-12.md`](../design-review-2026-06-12.md) — 10 findings 的讨论结论和修复记录
