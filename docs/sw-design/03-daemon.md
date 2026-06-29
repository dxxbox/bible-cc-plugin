# 03 — Daemon

> L2 | 领域总览 | 定义了 daemon 的启动序列、SQLite 约束、端口冲突处理、HTTP API 的全局设计约束。各 L3 文件不得与本文约束冲突。

---

## 1. 定位

Daemon 是 bible-cc-plugin 的核心持久进程。它是一个基于 FastAPI + uvicorn 的 HTTP server，监听 `localhost:9777`（端口可配），使用 SQLite 作为本地存储。所有 hook 和 command 通过 HTTP API 与之交互。

对外职责：
- **缓冲**：接收 hook 投递的 turn 数据，存入 SQLite
- **检测**：异步跑 Phase 1（mid-session）和 Phase 2（retrospective）moment detection LLM 调用
- **注入**：SessionStart 时从本地 buffer 构建 `<relevant-memories>` 注入上下文
- **flush**：将 moments 推送到 BiBLE Atlas
- **query**：通过 `/daemon/consult` 代理 BiBLE V4 搜索

对内约束：
- 所有持久化数据存入 `~/.bible-cc/daemon.db`
- 所有 HTTP 端点返回 JSON，错误统一格式（见 `02-interfaces`）
- 永不 crash：内部异常必须 catch，返回 structured error，不抛 500

---

## 2. 全局约束

以下约束对所有 daemon L3 文件及实现代码具有强制力。

### 2.1 启动序列

详见 `03-daemon/startup.md`。

```
1. Open/create SQLite DB at ~/.bible-cc/daemon.db
2. PRAGMA journal_mode=WAL;          ← concurrent writes: block-wait instead of SQLITE_BUSY
3. PRAGMA busy_timeout=5000;         ← wait ≤5s for write locks
4. Run schema migration              ← CREATE TABLE IF NOT EXISTS for all tables
5. Scan unclosed sessions            ← crash recovery (fast: SQLite read; slow: async Phase 2 + flush)
6. Start FastAPI server              ← uvicorn on configured port (default :9777)
```

硬性要求：
- **WAL 模式无例外**：第 2 步和第 3 步必须在任何读写操作之前执行。
- **schema migration 幂等**：使用 `CREATE TABLE IF NOT EXISTS`，重复执行无害。
- **crash recovery 不阻塞启动**：第 5 步的快路（读 SQLite）同步完成，慢路（Phase 2 LLM + flush）异步队列跑。

### 2.2 SQLite Schema

详见 `03-daemon/sqlite-schema.md`。

核心表：

| 表 | 用途 | 关键约束 |
|----|------|---------|
| `sessions` | 活跃 session 记录 | `session_id TEXT PRIMARY KEY` |
| `turns` | 缓冲的对话 turn | `session_id REFERENCES sessions`, `seq` per-session auto-increment（非全局自增） |
| `moments` | 检测到的 key moments | `content_hash TEXT UNIQUE NOT NULL`, `flushed` 默认 0, `import_task_id`, `flushed_at`, `retry_count`（见 `05-capture/flush.md`） |

强制规则：
- **content-hash dedup**：所有 moment insert 前计算 `SHA-256(session_id + title + narrative)`。UNIQUE 约束 + `INSERT OR IGNORE`。杜绝重复。
- **WAL + busy_timeout**：无连接池，靠两行 PRAGMA 解决并发。
- **无 FK cascade**：delete/update 由应用层显式控制。

### 2.3 HTTP API

详见 `03-daemon/http-api.md`。

端点分组：

| 组 | 端点 | 用途 |
|----|------|------|
| 生命周期 | `/daemon/start`, `/daemon/stop`, `/daemon/health` | 进程管理 + 健康检查 |
| Session | `/session/start`, `/session/end` | 会话边界 + crash recovery |
| Turn | `/turn/user`, `/turn/assistant`, `/turn/tool` | 数据缓冲 + Phase 1 检测触发 |
| Context | `/context/inject`, `/daemon/consult` | 本地注入 / BiBLE 搜索 |
| Review | `/daemon/moments`, `/daemon/moments/{id}` | pending moment 管理 |

强制规则：
- 所有 `/turn/*` 端点立即返回（non-blocking）。Phase 1 detection 是异步队列任务。
- **Phase 1 检测阈值**：默认 `capture.commit_threshold_turns=4`, `capture.commit_threshold_chars=2000`。以先到达者为准触发检测。这些值在 `04-config/schema.md` 中定义，daemon 实现从 config 读取。
- `/session/end` 会阻塞等待 LLM（Phase 2 retrospective）完成。timeout 由 Stop hook 的 30s 控制。
- `/daemon/consult` 在 query 为空时也会调 LLM（conversation summarization → search query synthesis），该 LLM 调用同样可能阻塞。timeout 由调用方（command）控制。
- `/context/inject` 只能查本地 SQLite。禁止在此端点内调用 BiBLE API。

### 2.4 端口冲突

详见 `03-daemon/port-conflict.md`。

- 默认端口 `9777`，通过 config `daemon.port` 可配。
- 默认行为：端口被占 → daemon 启动失败 → SessionStart hook 检测 → stdout error hint（transcript + system prompt）。
- 可选行为：`daemon.port_auto_fallback: true` → port+1 重试直到找到可用端口。

### 2.5 错误处理

| 场景 | 行为 |
|------|------|
| 端口被占 | 返回 error + hint。不静默。 |
| SQLite I/O 错误 | 返回 `"sqlite_integrity": "error"` 在 health check 中。不 crash。 |
| Phase 1 LLM 调用失败 | Log 错误，跳过本轮。不影响 buffer。 |
| Phase 2 LLM 调用失败 | Log 错误，仅 flush Phase 1 已有的 moments。不阻塞 session close。 |
| BiBLE flush 失败 | Moments 保持 `flushed=0`。下次 push 或 retry-push 重试。 |
| Daemon 中途 crash | SQLite WAL 保护数据完整性。恢复后 SessionStart 自动 crash recovery。 |

---

## 3. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `03-daemon/startup.md` | 启动序列：WAL PRAGMA → schema migration → crash recovery scan → uvicorn | ✅ 已完成 |
| `03-daemon/sqlite-schema.md` | 表结构、索引、PRAGMA、content_hash UNIQUE、migration 策略 | ✅ 已完成 |
| `03-daemon/port-conflict.md` | 端口冲突检测、错误通知、auto_fallback 逻辑 | ✅ 已完成 |
| `03-daemon/http-api.md` | 每个端点的请求/响应 spec、时序约束、错误码 | ✅ 已完成 |

---

## 4. 参考文档

- [`01-architecture-overview.md`](01-architecture-overview.md) — 四组件模型、pull model、硬性约束
- [`02-interfaces.md`](02-interfaces.md) — Daemon HTTP API 完整端点定义、错误响应格式
- [`../bible-claude-code-plugin-feasibility-report.md`](../bible-claude-code-plugin-feasibility-report.md) — Daemon Design 章节
- [`../../CLAUDE.md`](../../CLAUDE.md) — Key Design Decisions、Moment Detection Design
