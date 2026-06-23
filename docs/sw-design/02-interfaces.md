# 02 — Interfaces

> L1 | 边界 | 本文定义了所有跨组件接口协议——daemon HTTP API、BiBLE Atlas V4 API 契约、MCP tool schema、hook ↔ daemon 约定。所有组件实现必须以此为准。

---

## 1. Daemon HTTP API

Daemon 监听 `localhost:9777`（端口可配），所有端点返回 JSON。除非标注，均为同步返回。

### 1.1 生命周期端点

```
POST /daemon/start
  → idempotent. If daemon is already running, return current {pid, port, status}.
  → If not running: execute startup sequence (WAL → migration → crash recovery scan → uvicorn).
  → Request:  {} (no body required)
  → Response: {pid: int, port: int, status: "running" | "starting"}

POST /daemon/stop
  → Graceful shutdown. Flushes pending writes, closes SQLite, exits.
  → Request:  {}
  → Response: {status: "stopped"}

GET /daemon/health
  → Liveness + diagnostic probe. Used by /bible-cc:status and /bible-cc:check-bible.
  → Response: {
      status: "ok",
      pid: int,
      port: int,
      uptime: int (seconds),
      sessions: {active: int, completed: int},
      buffer: {total_turns: int, pending_moments: int},
      bible_connectivity: {reachable: bool, latency_ms: int},
      sqlite: {integrity: "ok" | "error", schema_version: int, size_bytes: int}
    }
```

### 1.2 Session 端点

```
POST /session/start
  → Creates a new session row. Scans for unclosed sessions (crash recovery):
      fast path: read existing unflushed moments/turns from SQLite — does NOT block.
      slow path: queue Phase 2 retrospective + flush task (async background worker).
  → Request:  {session_id: string}
  → Response: {session_id: string, is_new: bool, recovery: {unclosed_sessions_found: int, moments_recovered: int} | null}

POST /session/end
  → Triggers Phase 2 retrospective moment detection on ALL buffered turns.
  → Bundles all unflushed moments + retrospective result → POST to BiBLE Atlas as single import.
  → Marks session status = 'completed'.
  → Request:  {session_id: string}
  → Response: {session_id: string, moments_flushed: int, status: "completed"}

POST /daemon/session/flush
  → Flushes all unflushed moments for a session WITHOUT ending the session.
  → Used by /bible-cc:push for manual mid-session flush.
  → Request:  {session_id: string}
  → Response: {session_id: string, moments_flushed: int}
```

### 1.3 Turn 端点

```
POST /turn/user
  → Buffers the user's message. Queues Phase 1 detection task (non-blocking).
  → Request:  {session_id: string, message: string}
  → Response: {turn_id: int, queued: bool} — returns immediately, detection runs async.

POST /turn/tool
  → Buffers a tool invocation (name, arguments, full output). Queues Phase 1 detection task.
  → Daemon stores FULL tool output in turns table (no mechanical truncation).
  → LLM extracts ≤tool_result_max_chars精华摘要 as part of detection.
  → Request:  {session_id: string, tool_name: string, arguments: object, output: string}
  → Response: {turn_id: int, queued: bool} — returns immediately.
```

### 1.4 Context 端点

```
POST /context/inject
  → Returns <relevant-memories> string from LOCAL BUFFER ONLY.
  → Sources: recent turns summary, unflushed moments, crash-recovery moments.
  → Does NOT call BiBLE Atlas. Three branches based on buffer state (see 01-architecture §2).
  → Request:  {session_id: string, user_message: string}
  → Response: {context: string, sources: {turns: int, moments: int, crash_recovery: int}}

POST /daemon/consult
  → User-initiated cross-domain search. Client makes parallel calls to three domain search endpoints and merges results (see §2.2).
  → If query is empty/null: daemon calls LLM to summarize conversation into a query.
  → Request:  {session_id: string, query?: string}
  → Response: {context: string, query_used: string, hits: [{domain, id, title, snippet, score}]}
```

### 1.5 Moments 端点（review 命令用）

```
GET  /daemon/moments?session_id={session_id}
  → Lists all pending（unflushed）moments for a session.
  → Response: {moments: [{id, moment_type, title, narrative, turn_range, detected_at}]}

DELETE /daemon/moments/{id}
  → Discards a single pending moment (hard delete from SQLite).
  → Response: {deleted: true}

PUT  /daemon/moments/{id}
  → Edits title and/or narrative of a pending moment. flushed=1 moments cannot be edited.
  → Request:  {title?: string, narrative?: string}
  → Response: {updated: true, moment: {id, title, narrative}}
```

### 1.6 错误响应格式

所有错误返回统一结构：

```json
{
  "error": {
    "code": "DAEMON_NOT_RUNNING" | "SESSION_NOT_FOUND" | "MOMENT_NOT_FOUND" | "MOMENT_ALREADY_FLUSHED" | "BIBLE_UNREACHABLE" | "INTERNAL_ERROR",
    "message": "human-readable description"
  }
}
```

HTTP status codes: 200 (success), 400 (bad request), 404 (not found), 409 (conflict, e.g. edit flushed moment), 500 (internal), 503 (BiBLE unreachable).

---

## 2. BiBLE Atlas V4 API 契约

插件通过 `client.py` 调用 BiBLE Atlas REST API。V4 按域独立路由：`/api/search/memory`、`/api/search/knowledge-base`、`/api/search/skill`。**不存在统一的跨域搜索端点**——跨域查询由 client 并行调用三域 search API 后合并结果。

> 参考：`BiBLE-Atlas/docs/designs/server_part/v4/02_API接口文档.md`

### 2.1 Search API

三域 Search 请求体字段一致，路由独立。请求编码 `application/json`。

```
POST /api/search/memory
POST /api/search/knowledge-base
POST /api/search/skill

Request:
{
  "query": string,            // required
  "tag": string,              // required. MEMORY→"memory", SKILL→"skill", KNOWLEDGE_BASE→自定义(如 "design/flow/alg")
  "kb_index": string,         // optional, 精确指定知识库索引
  "search_type": "keyword" | "title" | "text" | "vector" | "hybrid",  // optional
  "top_k": int,               // optional
  "vector_model": string,     // optional (与索引绑定一致)
  "vector_weight": float      // optional, 混合检索向量权重
}

Response:
{
  "success": true,
  "domain": "MEMORY" | "KNOWLEDGE_BASE" | "SKILL",
  "kb_index": "kb_xxx",
  "tag": "memory",
  "total": int,
  "results": {
    // key is domain snake_case: "memory" | "knowledge_base" | "skill"
    // ⚠️ 与 domain 字段不同: domain="MEMORY", key="memory"
    "memory": [
      {
        "doc_id": string,
        "section_id": string,
        "section_title": string,
        "score": float,
        "content": string
      }
    ]
  }
}
```

### 2.2 跨域查询（Plugin 侧实现）

`/bible-cc:consult` 和 MCP 工具需要跨域搜索时，由 `client.py` 并行调用三个 search 端点，合并结果按 score 降序排列后返回。属 plugin 侧逻辑，非 BiBLE 服务端功能。

### 2.3 Import API

三域 Import 均为异步。请求编码 `multipart/form-data`。返回 `202 + task_id + status=queued`。

```
POST /api/import/memory        → {files[], kb_index, tag="memory", parser_script?, vector_model?, parser_context?, ...}
POST /api/import/knowledge-base → {files[], kb_index, tag, parser_script?, vector_model?, parser_context?, ...}
POST /api/import/skill          → {files[], kb_index, tag="skill", parser_script?, vector_model?, parser_context?, ...}

Response: {success: true, task_id: string, domain: string, kb_index: string, tag: string, status: "queued"}
```

任务状态查询：`GET /api/control/admin/tasks/{task_id}`。状态流转：`queued → running → completed / failed / cancelled`。

**⚠️ Flush 序列化设计待定**：daemon 的 flush 操作需要将 moments（结构化 JSON 对象，含 title/narrative/moment_type 等字段）通过 `multipart/form-data` 的 `files[]` 上传到 `/api/import/memory`。具体方案：
- 将 moments 序列化为 JSON 文件后作为 `files[]` 上传（一个文件含多条 moments，或一个文件一条 moment）
- `kb_index` 从 config 读取（待定义 `bible.kb_index` 或 `capture.kb_index`）
- `tag` 固定为 `"memory"`
此设计需在 `05-capture/flush.md`（L3）中落实。

### 2.4 Download API

SKILL/MEMORY 下载走异步任务，不支持简单的 `GET /api/download/{id}`。

```
POST /api/download/memory/file    → {tag, storage_path} → 202 + task_id
POST /api/download/memory/batch   → {tag, storage_paths[]} → 202 + task_id
GET  /api/download/memory/artifact/{artifact_id} → 二进制文件流

POST /api/download/skill/file     → {tag, storage_path} → 202 + task_id
POST /api/download/skill/batch    → {tag, storage_paths[]} → 202 + task_id
GET  /api/download/skill/artifact/{artifact_id} → 二进制文件流
```

### 2.5 客户端约定

- 所有请求带 `Authorization: Bearer {token}`（token 来自 `BIBLE_ATLAS_TOKEN` env var 或 `bible.token` config）。
- Timeout: 10s connect, 30s read. 超时视为 BiBLE unreachable。
- BiBLE 不可达时，client 抛出 `BibleUnreachableError`，调用方决定 fallback 策略（graceful degradation）。

---

## 3. MCP Tool Schema

MCP Server（`src/bible_cc_plugin/mcp/server.py`）通过 stdio transport 暴露 tool。Model 在对话中自动调用。共 **6 个活跃 tool + 2 个 postponed**（待服务端确认）。详见 `06-recall/mcp-tools.md`。

| Tool | Parameters | Returns | BiBLE Endpoint |
|------|-----------|---------|----------------|
| `bible_memory_search` | `query`, `tag?` (default "memory"), `top_k?`, `search_type?` | `results[]` | `POST /api/search/memory` |
| `bible_memory_save` | `messages[]`, `title?`, `abstract?` | `{task_id, status: "queued"}` | `POST /api/import/memory` — title/abstract serialized into file content via multipart |
| `bible_memory_get` | `storage_path` | Downloads via async task → artifact | `POST /api/download/memory/file` → poll task → `GET /api/download/memory/artifact/{id}` |
| `bible_memory_delete` | `memory_id` | `{deleted: true}` | ❌ postponed — V4 API 未提供，待服务端确认后实现 |
| `bible_knowledge_search` | `query`, `tag`, `top_k?`, `search_type?` | `results[]` | `POST /api/search/knowledge-base` |
| `bible_knowledge_list` | `tag?` | `[{name, description, tag}]` | ❌ postponed — V4 API 未提供，待服务端确认后实现 |
| `bible_skill_search` | `query`, `tag` (fixed "skill"), `top_k?`, `search_type?` | `results[]` | `POST /api/search/skill` |
| `bible_skill_get` | `storage_path` | Downloads via async task → artifact | `POST /api/download/skill/file` → poll task → `GET /api/download/skill/artifact/{id}` |

### 3.1 MCP 工具设计原则

1. **纯 BiBLE API 封装**：MCP Server 不直接访问 daemon SQLite。不调用 daemon HTTP API。
2. **无状态**：每个 MCP tool 调用是独立的，不依赖之前的状态。必需的配置（base_url、token）通过环境变量读入。
3. **错误即返回**：BiBLE 不可达时，返回结构化错误给 model。Model 被告知后可重试或跳过。不 crash MCP server。
4. **postponed tools**：`bible_memory_delete` 和 `bible_knowledge_list` 标记为 postponed——V4 API 尚未提供对应端点。在 MCP server 中注册占位 tool（直接返回 "not yet available"），等服务端确认后再实现。Pending moments 的删除通过 daemon 的 review 端点。

### 3.2 MCP Discovery（`.mcp.json`）

`.mcp.json` 由 setup wizard（`bible_cc_plugin.scripts.setup`）在 install 时动态生成于 plugin 目录根，**不提交 git**（在 `.gitignore` 中）。Claude Code 在 plugin 目录中自动发现该文件并注册 MCP server。

```json
{
  "mcpServers": {
    "bible-cc": {
      "command": "uv",
      "args": ["run", "python", "-m", "bible_cc_plugin.mcp.server"],
      "env": {
        "BIBLE_ATLAS_BASE_URL": "http://localhost:5555",
        "BIBLE_ATLAS_TOKEN": ""
      }
    }
  }
}
```

- 示例中的值为默认值。实际值由 `setup.py` 根据用户配置写入（base_url、token）。
- `env` 值为字面量，不得使用 `${VAR:-default}` 语法（MCP 不解析 shell 默认值）。
- Daemon 启动时读取 `BIBLE_ATLAS_BASE_URL` env var 或 config 文件；MCP server 读取 `.mcp.json` 中的值或继承父进程环境。
- `bible-cc` 为 MCP server name，标识这是 bible-cc-plugin 提供的 MCP server。
- 生命周期：`install` 时生成 → 持久保留 → `uninstall` 时随 plugin 目录或 `rm -f` 删除。CI 结束时显式 `rm -f` 清理。

---

## 4. Hook ↔ Daemon 约定

### 4.1 Hook → Daemon 映射

| Hook | Daemon Endpoint(s) | Timeout | 失败行为 |
|------|-------------------|---------|---------|
| **Setup** | `POST /daemon/start` | 30s | 首次安装，报错接受。 |
| **SessionStart** | `POST /daemon/start` → `POST /session/start` → `POST /context/inject` | 60s | daemon 起不来 → error hint（stdout, inject:true）。后续 hooks 静默跳过。 |
| **UserPromptSubmit** | `POST /turn/user` | 3s | daemon 不可达 → 静默跳过。不阻塞 Claude Code。 |
| **PostToolUse** | `POST /turn/tool` | 3s | daemon 不可达 → 静默跳过。 |
| **Stop** | （无 daemon 调用，no-op 占位，Phase 2b 预留 mid-session detection） | 3s | per-turn 事件，当前仅 log。 |
| **SessionEnd** | `POST /session/end` | 30s | daemon 不可达 → 静默跳过。数据留在 SQLite，下次 SessionStart recovery。 |

### 4.2 Hook 响应约定

Hook 脚本通过 stdout JSON 向 Claude Code 返回结构化结果：

```json
{
  "continue": true,
  "suppressOutput": false,
  "hookSpecificOutput": {
    "additionalContext": "..."
  }
}
```

- `session-start` hook 设置 `inject: true`，使输出同时进入 system prompt。
- `suppressOutput: false`（默认）→ stdout 文本出现在 conversation transcript 中。
- 所有 hook 使用 `uv run python -m bible_cc_plugin.scripts.*` 调用。

### 4.3 hooks.json 结构

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
      "command": "uv run python -m bible_cc_plugin.scripts.hook turn-stop --session-id \"$CLAUDE_SESSION_ID\"",
      "timeout": 3000
    }],
    "SessionEnd": [{
      "command": "uv run python -m bible_cc_plugin.scripts.hook session-end --session-id \"$CLAUDE_SESSION_ID\"",
      "timeout": 30000
    }]
  }
}
```

### 4.4 环境变量约定

| 变量 | 用途 | 来源 |
|------|------|------|
| `CLAUDE_SESSION_ID` | 当前 Claude Code session ID | Claude Code 提供 |
| `USER_PROMPT` | 用户输入文本 | Claude Code 提供（UserPromptSubmit） |
| `TOOL_NAME` | 工具名称 | Claude Code 提供（PostToolUse） |
| `TOOL_OUTPUT` | 工具完整输出 | Claude Code 提供（PostToolUse）。**注意：是 `$TOOL_OUTPUT`，不是 `$TOOL_RESULT`** |
| `BIBLE_ATLAS_BASE_URL` | BiBLE Atlas 地址 | 用户配置（env var 或 config.json） |
| `BIBLE_ATLAS_TOKEN` | BiBLE Atlas 认证 token | 用户配置（env var 或 config.json） |
| `BIBLE_CC_DAEMON_PORT` | daemon 监听端口覆盖 | config.json `daemon.port` 的 env override |
| `BIBLE_CC_DB_PATH` | SQLite 数据库路径覆盖 | config.json `daemon.db_path` 的 env override |
| `ANTHROPIC_API_KEY` | Moment detection LLM 的 API key | 继承自 Claude Code 进程环境 |

---

## 5. 错误处理策略

| 场景 | 行为 |
|------|------|
| Daemon 端口被占 | SessionStart hook 检测 → stdout error hint（transcript + system prompt）。用户看到 `❌` 标记错误。 |
| BiBLE Atlas 不可达 | Flush 延迟（moments 留 SQLite）。MCP tools 返回结构化错误给 model。CLI hint 通知用户。`/bible-cc:check-bible` 返回当前状态。 |
| Daemon 在 session 中途 crash | UserPromptSubmit/PostToolUse hooks 尝试调 daemon 失败 → 首次输出 hint "daemon unreachable"，同 session 后续静默跳过（cooldown）。SessionEnd hook 失败 → 数据留 SQLite。下次 SessionStart 自动 recovery。 |
| Phase 1 LLM 调用失败 | Log 错误，跳过本轮检测。不影响 buffer。下轮 threshold 到达时重试。 |
| Phase 2 LLM 调用失败 | Log 错误，仅 flush Phase 1 已有的 moments。不阻塞 session close。 |
| BiBLE import（flush）失败 | Moments 保持 `flushed=0`。用户可通过 `/bible-cc:retry-push` 手动重试，或等下次 push。 |
| Content-hash 碰撞 | INSERT OR IGNORE 静默跳过。不报错。不丢数据。 |

---

## 6. 参考文档

- [`01-architecture-overview.md`](01-architecture-overview.md) — 组件模型、pull model、数据流、硬性约束
- [`../bible-claude-code-plugin-feasibility-report.md`](../bible-claude-code-plugin-feasibility-report.md) — 架构设计决策、Daemon HTTP API 端点定义
- [`../../CLAUDE.md`](../../CLAUDE.md) — Key Rules、Hook → Daemon Flow、Hint 机制
