# 03-daemon/http-api.md — HTTP API Detailed Spec（L3）

> Daemon 全部 12 个 HTTP 端点的完整 spec：请求/响应 schema、错误码、时序约束、内部实现要点。与 `02-interfaces.md` 一致，本文补充实现细节。

---

## 1. 通用约定

### 1.1 基础信息

- **Base URL**: `http://127.0.0.1:{port}`（port 默认 9777）
- **Content-Type**: `application/json`
- **Method**: 全部 POST，除了 health（GET）
- **Timeout**: 除标注外，所有请求应在 5s 内返回

### 1.2 错误响应格式

所有端点统一使用以下错误格式：

```json
{
  "error": {
    "code": "DAEMON_NOT_RUNNING | SESSION_NOT_FOUND | MOMENT_NOT_FOUND | MOMENT_ALREADY_FLUSHED | BIBLE_UNREACHABLE | INTERNAL_ERROR",
    "message": "human-readable description"
  }
}
```

HTTP 状态码：
- `200` — 成功
- `400` — 请求参数错误（缺少 `session_id` 等）
- `404` — 资源不存在（session/moment）
- `409` — 冲突（edit flushed moment）
- `500` — 内部错误（未预期异常）
- `503` — BiBLE 不可达

### 1.3 设计约束

- 所有 `/turn/*` 端点立即返回（<100ms），不做任何阻塞操作。
- Phase 1 detection 是异步后台任务，不在 HTTP 请求线程中执行。
- `/session/end` 是唯一会阻塞等待 LLM 的端点。
- `/context/inject` 仅查本地 SQLite，禁止在此端点内调用外部 API。

---

## 2. 生命周期端点

### 2.1 `POST /daemon/start`

启动 daemon。幂等。

**Request:**
```json
{}
```
（无 body 要求，空 JSON 即可）

**Response (200):**
```json
{
  "pid": 12345,
  "port": 9777,
  "status": "running"
}
```

**内部流程:**
1. 检查 `http://127.0.0.1:{port}/daemon/health` → 200 则幂等返回。
2. 否则执行完整启动序列（见 [`startup.md`](startup.md) §1）。
3. 返回 pid（实际进程 pid）、port（可能被 auto_fallback 调整）。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| 端口被占（无 fallback） | `INTERNAL_ERROR` | 500 |
| SQLite 打开失败 | `INTERNAL_ERROR` | 500 |
| uvicorn 启动失败 | `INTERNAL_ERROR` | 500 |

**调用方**：Setup hook、SessionStart hook。

---

### 2.2 `POST /daemon/stop`

优雅关闭 daemon。flush pending writes，关闭 SQLite，退出进程。

**Request:**
```json
{}
```

**Response (200):**
```json
{
  "status": "stopped"
}
```

**内部流程:**
1. 等待当前正在处理的 flush 任务完成（最多 10s）。
2. 关闭 SQLite connection（`conn.close()`）。
3. 停止 uvicorn server。
4. 进程退出。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| Daemon 未运行 | `DAEMON_NOT_RUNNING` | 400 |

**调用方**：用户手动 `POST /daemon/stop`（通常通过 `/bible-cc:stop-daemon` 命令或直接 curl）。注意此端点不由 hook 调用——SessionEnd hook 调用的是 `/session/end`。

---

### 2.3 `GET /daemon/health`

活性检测 + 诊断探针。

**Request:** （无 body）

**Response (200):**
```json
{
  "status": "ok",
  "pid": 12345,
  "port": 9777,
  "uptime": 3600,
  "sessions": {"active": 1, "completed": 42},
  "buffer": {"total_turns": 156, "pending_moments": 3},
  "bible_connectivity": {"reachable": true, "latency_ms": 45},
  "sqlite": {"integrity": "ok", "schema_version": 1, "size_bytes": 204800}
}
```

**内部流程:**
1. `pid`、`port` 取自 daemon 进程自身（port 可能是 auto_fallback 后的实际端口）。
2. 计算 `uptime = now - daemon_start_time`。
2. 查询 SQLite：
   ```sql
   SELECT COUNT(*) FROM sessions WHERE status='active';
   SELECT COUNT(*) FROM sessions WHERE status='completed';
   SELECT COUNT(*) FROM turns;
   SELECT COUNT(*) FROM moments WHERE flushed=0;
   ```
3. BiBLE 连通性：`GET {base_url}/api/health`（或对应的 ping 端点），计时。
   如失败 → `reachable=false, latency_ms=null`。超时 3s。
4. SQLite 完整性：`PRAGMA integrity_check;` + `SELECT COUNT(*) FROM schema_version;` + 文件大小。

**错误:** 无（此端点永不失败——即使子检查失败也返回 degraded 状态）。

**调用方**：`/bible-cc:status` 命令、幂等启动检测、健康监控。

---

## 3. Session 端点

### 3.1 `POST /session/start`

创建新 session 行并执行 crash recovery 扫描。

**Request:**
```json
{
  "session_id": "abc123-def456"
}
```

**Response (200):**
```json
{
  "session_id": "abc123-def456",
  "is_new": true,
  "recovery": {
    "unclosed_sessions_found": 1,
    "moments_recovered": 2
  }
}
```

若 `recovery` 为 `null` 表示无 crash 遗留 session。

**内部流程:**
1. Validates `session_id` 非空。
2. Crash recovery（快路，同步）：
   ```sql
   SELECT * FROM sessions WHERE status = 'active' AND session_id != ?;
   -- 对每个 unclosed session:
   SELECT * FROM moments WHERE session_id = ? AND flushed IN (0, -1);
   ```
   将 recovery moments 暂存于内存，供当前 SessionStart hook 的后续 `/context/inject` 调用使用。
3. Crash recovery（慢路，异步）：
   对每个 unclosed session 创建后台任务 `asyncio.create_task(run_retrospective_and_flush(unclosed_session))`。
   后台任务不阻塞本端点返回。
4. 创建新 session：
   ```sql
   INSERT INTO sessions (session_id, status) VALUES (?, 'active');
   ```
5. 返回。

**is_new 判断:** `INSERT` 成功 → `true`；若 session_id 已存在（PRIMARY KEY 冲突）→ `INSERT OR IGNORE` → `false`。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| session_id 缺失或空 | `INTERNAL_ERROR` | 400 |

**调用方**：SessionStart hook（三步流程的第二步）。

---

### 3.2 `POST /session/end`

触发 Phase 2 retrospective + flush 所有 unflushed moments。阻塞等待 LLM。

**Request:**
```json
{
  "session_id": "abc123-def456"
}
```

**Response (200):**
```json
{
  "session_id": "abc123-def456",
  "moments_flushed": 5,
  "status": "completed"
}
```

**内部流程:**
1. 验证 `session_id` 对应的 session 存在且 `status='active'`。
2. 取出 session 所有 turns：
   ```sql
   SELECT * FROM turns WHERE session_id = ? ORDER BY seq;
   ```
3. 取出 Phase 1 已检测的 moments：
   ```sql
   SELECT * FROM moments WHERE session_id = ?;
   ```
4. 构建 Phase 2 prompt → LLM call（见 `05-capture/detection.md` §3.2）。
5. 解析 new moments → content-hash dedup → `INSERT OR IGNORE`。
6. 打包所有 unflushed moments + retrospective → flush 到 BiBLE（见 `05-capture/flush.md`）。
7. 更新 session 状态：
   ```sql
   UPDATE sessions SET status='completed', closed_at=datetime('now') WHERE session_id=?;
   ```
8. 返回 flushed count 和状态。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| session_id 缺失 | `SESSION_NOT_FOUND` | 400 |
| Session 不存在 | `SESSION_NOT_FOUND` | 404 |
| Session 已 completed | （不报错）返回 `status: "already_completed"` | 200 |
| Phase 2 LLM 失败 | （不报错）flush Phase 1 moments 后返回 | 200 |
| BiBLE flush 失败 | （不报错）moments 保持 flushed=0 | 200 |

**⚠️ 这是唯一会阻塞等待 LLM 的端点**。SessionEnd hook 的 timeout 设为 30s 以覆盖此阻塞。如果 LLM 调用超过 30s，hook 被 kill，但 daemon 端可能仍在执行——需要任务追踪机制防止孤儿任务。

**调用方**：SessionEnd hook。

---

### 3.3 `POST /daemon/session/flush`

手动 flush 当前 session 的所有 unflushed moments 到 BiBLE Atlas。**不结束 session。**

**Request:**
```json
{
  "session_id": "abc123-def456"
}
```

**Response (200):**
```json
{
  "session_id": "abc123-def456",
  "moments_flushed": 3
}
```

**内部流程:**
1. 验证 session 存在且 `status='active'`。
2. 取出所有 unflushed moments：`SELECT * FROM moments WHERE session_id=? AND flushed=0;`。
3. 序列化 → `POST /api/import/memory`（见 `05-capture/flush.md`）。
4. 更新 moments 状态：`flushed=1`（或 `-1` 如果失败）。
5. 返回 flushed count。

**不触发 Phase 2 detection。** 此端点仅 flush 已有的 Phase 1 moments，不做 LLM 调用。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| session_id 缺失 | `SESSION_NOT_FOUND` | 400 |
| Session 不存在 | `SESSION_NOT_FOUND` | 404 |
| BiBLE flush 失败 | （不报错）moments 保持 flushed=0 | 200 |

**调用方**：`/bible-cc:push` 命令。

---

## 4. Turn 端点

### 4.1 `POST /turn/user`

缓冲用户消息并触发 Phase 1 检测（异步）。

**Request:**
```json
{
  "session_id": "abc123-def456",
  "message": "Let's use PostgreSQL instead of SQLite for the auth module."
}
```

**Response (200):**
```json
{
  "turn_id": 42,
  "queued": true
}
```

**内部流程:**
1. 验证 `session_id` 存在且 `status='active'`。
2. 分配 seq（内存计数器 `session_seq[session_id] += 1`）。
3. Insert turn：
   ```sql
   INSERT INTO turns (session_id, seq, role, content) VALUES (?, ?, 'user', ?);
   ```
4. 更新 session turn_count：
   ```sql
   UPDATE sessions SET turn_count = turn_count + 1 WHERE session_id=?;
   ```
5. 检查阈值（`commit_threshold_turns` / `commit_threshold_chars`）。
   若到达阈值且 `capture.enabled=true` 且 `mid_session_detection=true`：
   → `asyncio.Queue.put(detection_task)` → 立即返回 `{queued: true}`。
   检测由后台 worker 异步执行（非阻塞）。
6. 返回 `turn_id`（即 seq）。

**返回时间:** <10ms（仅 SQLite insert）。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| session_id 缺失 | `SESSION_NOT_FOUND` | 400 |
| Session 不存在或已 completed | `SESSION_NOT_FOUND` | 400 |

**调用方**：UserPromptSubmit hook。

---

### 4.2 `POST /turn/tool`

缓冲 tool 调用信息并触发 Phase 1 检测（异步）。完整输出存入 SQLite。

**Request:**
```json
{
  "session_id": "abc123-def456",
  "tool_name": "read_file",
  "arguments": {"file_path": "/path/to/file.py"},
  "output": "<entire tool output, potentially very large>"
}
```

**Response (200):**
```json
{
  "turn_id": 43,
  "queued": true
}
```

**内部流程:**
1. 同 `/turn/user` steps 1-3，但 role='assistant'，且写入 `tool_name`、`tool_arguments`（JSON string）、`tool_output`。
2. 无机械截断——完整 `output` 存入 `tool_output` 列。LLM 在 detection worker 中按 `capture.tool_result_max_chars`（默认 250）提取精华摘要。
3. 阈值检测同 `/turn/user`。

**返回时间:** <10ms（仅 SQLite insert）。

**错误:** 同 `/turn/user`。

**调用方**：PostToolUse hook。注意 hook 使用 `$TOOL_OUTPUT` 环境变量——不是 `$TOOL_RESULT`。

---

## 5. Context 端点

### 5.1 `POST /context/inject`

从本地 buffer 构建 `<relevant-memories>` 上下文。**不调用 BiBLE API**。

**Request:**
```json
{
  "session_id": "abc123-def456",
  "user_message": "Let's continue with the auth module."
}
```

**Response (200):**
```json
{
  "context": "<relevant-memories>...local buffer content...</relevant-memories>",
  "sources": {
    "turns": 12,
    "moments": 2,
    "crash_recovery": 0
  }
}
```

**内部流程（三种场景）:**

| 场景 | 检测条件 | 注入内容 |
|------|---------|---------|
| **全新 session（无 crash）** | session 有 `is_new=true` 且 recovery 为 null | `context=""` 空字符串 |
| **`/clear` 或 compaction** | session 已存在（`is_new=false`）或有 turns | 当前 session 最近 turns 摘要 + unflushed moments |
| **Crash recovery** | session 有 `recovery.unclosed_sessions_found > 0` | Recovery moments（快路读取的 prior session 数据）+ turns 摘要 |

构建逻辑：
1. 如果 `injection.enabled = false` → 直接返回 `context=""`。
2. 如果 buffer 为空（`is_new=true` 且无 recovery）→ 按 `inject_fallback` 处理：
   - `"skip"` → `context=""`
   - `"empty"` → `<relevant-memories></relevant-memories>`
3. 否则从 SQLite 查询 turns 和 moments，组合为 `<relevant-memories>` XML block。
4. Token budget 控制：估算 token 数（字符数/3），超过 `injection.token_budget`（默认 1200）时截断并加 `[truncated]` 标记。
5. 返回 context 字符串 + sources 计数。

**禁止:** 此端点内不得调用 BiBLE API。上下文仅来自本地 SQLite。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| session_id 缺失 | `SESSION_NOT_FOUND` | 400 |

**调用方**：SessionStart hook（三步流程的第三步）。

---

### 5.2 `POST /daemon/consult`

用户发起的跨域搜索。如果 query 为空，由 daemon 调用 LLM 生成 search query。

**Request:**
```json
{
  "session_id": "abc123-def456",
  "query": "What was that decision about PostgreSQL?"
}
```

**Response (200):**
```json
{
  "context": "<relevant-memories>...combined search results...</relevant-memories>",
  "query_used": "PostgreSQL decision auth storage",
  "hits": [
    {"domain": "MEMORY", "id": "doc_1", "title": "PostgreSQL Decision", "snippet": "...", "score": 0.92},
    {"domain": "KNOWLEDGE_BASE", "id": "doc_2", "title": "PostgreSQL Auth Patterns", "snippet": "...", "score": 0.85}
  ]
}
```

**内部流程:**
1. 如果 `query` 为空/null：
   - 从 SQLite 取当前 session 最近 turns。
   - 用 conversation summary prompt 调用 LLM → 生成 search query。
   - **此 LLM 调用可能阻塞。** timeout 由 command 调用方控制。
2. 并行调用 BiBLE 三域 search（通过 `client.py`）：
   ```
   POST {base_url}/api/search/memory    {query, tag="memory", top_k=8}
   POST {base_url}/api/search/knowledge-base  {query, tag=<from_config>, top_k=8}
   POST {base_url}/api/search/skill    {query, tag="skill", top_k=8}
   ```
3. 合并结果，按 score 降序排列，取 top_k。
4. 过滤低于 `search.default_min_score`（默认 0.35）的结果。
5. 构建 `<relevant-memories>` XML block。
6. 返回。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| BiBLE 不可达 | `BIBLE_UNREACHABLE` | 503 |
| query 生成 LLM 失败 | `INTERNAL_ERROR` | 500 |
| session_id 缺失 | `SESSION_NOT_FOUND` | 400 |

**调用方**：`/bible-cc:consult` 命令。

---

## 6. Review 端点（Moments 管理）

### 6.1 `GET /daemon/moments`

列出 session 的 pending moments。

**Request:** Query param `session_id`。

**Response (200):**
```json
{
  "moments": [
    {
      "id": 1,
      "moment_type": "decision",
      "title": "PostgreSQL for auth storage",
      "narrative": "Team decided to use PostgreSQL for auth...",
      "turn_range": "5-7",
      "detected_at": "2026-06-13T10:30:00"
    }
  ]
}
```

**内部 SQL:**
```sql
SELECT id, moment_type, title, narrative, turn_range_start, turn_range_end, detected_at
FROM moments
WHERE session_id = ? AND flushed = 0
ORDER BY detected_at DESC;
```

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| session_id 缺失 | `SESSION_NOT_FOUND` | 400 |
| Session 不存在 | `SESSION_NOT_FOUND` | 404 |

**调用方**：`/bible-cc:review` 命令。

---

### 6.2 `DELETE /daemon/moments/{id}`

删除单个 pending moment（硬删除）。

**Request:** Path param `id`。

**Response (200):**
```json
{
  "deleted": true
}
```

**内部 SQL:**
```sql
DELETE FROM moments WHERE id = ? AND flushed = 0;
```

只删除 `flushed=0` 的 moment——已 flush 的 moment（flushed=1 或 2）不可删除（返回错误）。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| moment_id 不存在 | `MOMENT_NOT_FOUND` | 404 |
| Moment 已 flush | `MOMENT_ALREADY_FLUSHED` | 409 |

**调用方**：`/bible-cc:review` 命令（discard 选项）。

---

### 6.3 `PUT /daemon/moments/{id}`

编辑 pending moment 的 title 和/或 narrative。

**Request:**
```json
{
  "title": "Updated title (optional)",
  "narrative": "Updated narrative (optional)"
}
```

至少提供一个字段。两个字段都为 null/空 → 400。

**Response (200):**
```json
{
  "updated": true,
  "moment": {
    "id": 1,
    "title": "Updated title",
    "narrative": "Updated narrative"
  }
}
```

**内部流程:**
1. 查询 moment，验证存在且 `flushed=0`。
   - 若已 flush → 409 `MOMENT_ALREADY_FLUSHED`。
2. 动态构建 UPDATE：只更新提供的字段。
3. **⚠️ 内容变更后 content_hash 可能改变。** 编辑后需重新计算 content_hash 并检查 UNIQUE 约束：
   - 如果新 hash 与已有 moment 冲突 → 409 error `"Edited content duplicates an existing moment"`。
4. 返回更新后的 moment。

**错误:**
| 场景 | Code | HTTP |
|------|------|------|
| moment_id 不存在 | `MOMENT_NOT_FOUND` | 404 |
| Moment 已 flush | `MOMENT_ALREADY_FLUSHED` | 409 |
| title 和 narrative 均为空 | `INTERNAL_ERROR` | 400 |
| Content hash 冲突 | `MOMENT_ALREADY_FLUSHED` | 409 |

**调用方**：`/bible-cc:review` 命令（edit 选项）。

---

## 7. 时序约束汇总

| 端点 | 最大延迟 | 是否阻塞 | 依赖外部 |
|------|---------|---------|---------|
| `POST /daemon/start` | ~500ms | 是（startup 序列同步） | 否 |
| `POST /daemon/stop` | ~10s | 是（等 flush 完毕） | 否 |
| `GET /daemon/health` | ~3s | 是（BiBLE ping） | BiBLE（3s timeout） |
| `POST /session/start` | ~50ms | 否 | 否 |
| `POST /session/end` | ~20s | **是（LLM）** | BiBLE（flush） |
| `POST /turn/user` | <10ms | 否（检测异步） | 否 |
| `POST /turn/tool` | <10ms | 否（检测异步） | 否 |
| `POST /context/inject` | ~10ms | 否 | 否 |
| `POST /daemon/consult` | ~5s | 是（三域并行 search） | BiBLE + LLM |
| `GET /daemon/moments` | ~5ms | 否 | 否 |
| `DELETE /daemon/moments/{id}` | ~5ms | 否 | 否 |
| `PUT /daemon/moments/{id}` | ~5ms | 否 | 否 |

---

## 8. 并发与线程安全

- **FastAPI + uvicorn**：异步框架。所有端点用 `async def`。
- **SQLite 连接**：单连接——`sqlite3` 模块是线程安全的但非并发。用 WAL + busy_timeout 代替连接池。
- **异步检测队列**：`asyncio.Queue`。单 worker 消费。所有 SQLite 写入在主线程/同一连接上执行，无竞态。
- **Phase 2 + flush 后台任务**：`asyncio.create_task`。任务间不共享可变状态。

---

## 9. 参考文档

- [`../../02-interfaces.md`](../02-interfaces.md) — L1 接口定义（本文与之保持一致）
- [`startup.md`](startup.md) — 启动序列
- [`sqlite-schema.md`](sqlite-schema.md) — 表结构
- [`../05-capture/detection.md`](../05-capture/detection.md) — Phase 1/2 LLM 调用参数
- [`../05-capture/flush.md`](../05-capture/flush.md) — flush 到 BiBLE 的序列化与状态管理
- [`../08-operability/hint-system.md`](../08-operability/hint-system.md) — error hint 通知
