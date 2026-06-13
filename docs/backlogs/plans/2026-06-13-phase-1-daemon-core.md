# Phase 1: Daemon Core + Local Buffer

> **For agentic workers:** Phase 1 在 Phase 0 的"daemon 可启动、可 health check"基础上，添加数据持久化能力——SQLite、session/turn/moment schema、完整 HTTP API。Phase 0 的 daemon lifecycle（start/stop/status）和 setup wizard 已就绪，本 Phase 只扩展 server.py 的端点 + 新增 buffer.py。

**Goal:** Daemon 完整启动序列、SQLite schema（3 表）、HTTP API 全部端点可用（15 个端点）、session/turn 生命周期、本地 context injection。

**Phase 0 已有（不重复实现）:** setup wizard、daemon lifecycle CLI（start/stop/status/restart）、health endpoint、config 系统、CI 骨架。

**本 Phase 新增:** SQLite WAL schema、buffer.py、session/turn/moment CRUD、/context/inject、扩展 server.py 端点。

> **/orchestrate 提示**: 本 Phase 使用 SQLite（stdlib sqlite3），非 PostgreSQL。如 chain 中包含 `database-reviewer`，请在 task description 中标注 `(SQLite, not PostgreSQL)` 以避免不适用建议。

**Architecture:** FastAPI HTTP server + SQLite (WAL mode) + 无 LLM 调用 + 无 BiBLE 通信。

**Tech Stack:** FastAPI, uvicorn, sqlite3 (stdlib), Pydantic

**预估: 5-7 天**

---

## Feature 逐个讨论

### F1.1 — SQLite Schema + buffer.py

| 属性 | 说明 |
|------|------|
| **理由** | 所有持久化数据的核心存储。CLAUDE.md 硬性约束：WAL mode 必须第一个 PRAGMA 执行，`busy_timeout=5000` 紧随其后。这两个 PRAGMA 是并发写不产生 SQLITE_BUSY 的唯一保证。content-hash UNIQUE 约束是 dedup 的第二层防线（第一层是 Phase 2 prompt injection）。 |
| **优先级** | P0 — 存储基础，所有数据读写都经过它 |
| **依赖** | types.py（Session, Turn, Moment Pydantic models） |

三张表：
- **sessions**: `session_id TEXT PRIMARY KEY`, `status TEXT`（active/completed/crashed）, `created_at`, `completed_at`
- **turns**: `id INTEGER PRIMARY KEY AUTOINCREMENT`, `session_id TEXT REFERENCES sessions`, `seq INTEGER` (per-session auto-increment), `role TEXT`（user/tool）, `message TEXT`, `tool_name TEXT`, `tool_args TEXT`, `tool_output TEXT`, `created_at`
- **moments**: `id INTEGER PRIMARY KEY AUTOINCREMENT`, `session_id TEXT REFERENCES sessions`, `moment_type TEXT NOT NULL`, `title TEXT NOT NULL`, `narrative TEXT NOT NULL`, `content_hash TEXT UNIQUE NOT NULL`, `flushed INTEGER DEFAULT 0`, `import_task_id TEXT`, `flushed_at TEXT`, `retry_count INTEGER DEFAULT 0`, `created_at`

`schema_version` 用 `PRAGMA user_version` tracking。

### F1.2 — Daemon 启动序列

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 明确定义的 6 步启动序列（open DB → WAL → busy_timeout → migration → crash recovery scan → uvicorn），这是硬性约束，不得改变顺序。开 DB 之后的任何错误都不得 crash daemon（catch + structured error）。Crash recovery 快路同步不阻塞启动。 |
| **优先级** | P0 — daemon 生命周期 |
| **依赖** | buffer.py（SQLite schema）、config.py（port, db_path） |

启动序列保证：WAL 在任何读写之前；schema migration 幂等（CREATE TABLE IF NOT EXISTS）；crash recovery 快路同步完成（读 unclosed sessions），慢路异步队列（Phase 2 LLM + flush，到 Phase 2 再接入）；端口被占可检测并产生 error hint；启动成功则返回 pid + port。

### F1.3 — HTTP API — 生命周期端点（3 个）

| 属性 | 说明 |
|------|------|
| **理由** | `/daemon/start`（idempotent）、`/daemon/stop`（graceful）、`/daemon/health`（liveness + diagnostic probe）是 daemon 运行的基础。`start` 的幂等性是 SessionStart hook 的前提——hook 每次都调 start，如果 daemon 已运行就直接返回当前状态，不需要额外逻辑判断。 |
| **优先级** | P0 |
| **依赖** | 启动序列、config.py |

- `POST /daemon/start`: 空 body → running/starting status。Idempotent——已运行时返回当前 pid/port/status。
- `POST /daemon/stop`: 空 body → flush pending writes → close SQLite → exit。
- `GET /daemon/health`: 返回完整诊断 JSON（pid, port, uptime, sessions {active, completed}, buffer {total_turns, pending_moments}, bible_connectivity {reachable, latency_ms}, sqlite {integrity, schema_version, size_bytes}）。**Phase 1 时 bible_connectivity.reachable 固定为 null**（BiBLE client 尚未实现）。

### F1.4 — HTTP API — Session 端点（2 个）

| 属性 | 说明 |
|------|------|
| **理由** | session 是数据组织的基本单元。`/session/start` 的 crash recovery scan 是整个 plugin 数据可靠性的关键——如果 daemon 或 Claude Code 异常终止，Stop hook 不会触发，session 保持 active。下次 SessionStart 必须发现并恢复这些 unclosed sessions。 |
| **优先级** | P0 |
| **依赖** | buffer.py（sessions/turns 表） |

- `POST /session/start`: `{session_id}` → 创建新 session row → 扫描 unclosed sessions → 快路返回 moments_recovered 数量。Phase 1 不跑 Phase 2 retrospective（LLM 未接入），仅标记。
- `POST /session/end`: `{session_id}` → 标记 session status='completed'。Phase 1 不跑 flush（BiBLE client 未接入）。

### F1.5 — HTTP API — Turn 端点（2 个）

| 属性 | 说明 |
|------|------|
| **理由** | `/turn/user` 和 `/turn/tool` 是数据流入链路的第一站。CLAUDE.md 硬性约束：必须立即返回（non-blocking），Phase 1 detection 是异步队列任务。PostToolUse hook 传入的 tool output 完整存入 turns 表——不做机械截断，LLM 在 detection 阶段自己提取精华。 |
| **优先级** | P0 |
| **依赖** | buffer.py（turns 表）、types.py |

- `POST /turn/user`: `{session_id, message}` → 写入 turns 表 → 返回 turn_id + queued。Phase 1 无 detection queue。
- `POST /turn/tool`: `{session_id, tool_name, arguments, output}` → 完整存储 output 字段 → 返回 turn_id + queued。

### F1.6 — Context Injection（/context/inject）

| 属性 | 说明 |
|------|------|
| **理由** | Pull model 的第一条路径（SessionStart 本地注入）。CLAUDE.md 硬性约束：纯本地 SQLite 操作，禁止在此端点内调用 BiBLE API。三种场景分支是硬性要求：新 session（空 buffer）、/clear 或 compact（当前 session turns + moments）、crash recovery（prior unclosed session data）。 |
| **优先级** | P0 — recall pipeline 基础 |
| **依赖** | buffer.py（turns + moments 读） |

三种场景的输出：
1. **新 session，无 crash**: 空 buffer → 返回空 `<relevant-memories>`（或 `inject_fallback=empty` 时返回空 block）
2. **/clear 或 compact**: 当前 session turns summary + unflushed moments → 注入
3. **新 session，crash recovery**: prior unclosed session 的 turns + moments → 注入

### F1.7 — 端口冲突处理

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束——端口被占不能静默，必须产生用户可见 error hint。默认行为（`port_auto_fallback=false`）是 fail-fast + notify，因为静默切换端口会导致 hook 脚本指向错误端口。`port_auto_fallback=true` 是 opt-in 的便利选项。 |
| **优先级** | P1 — 运维基础 |
| **依赖** | daemon 启动序列、config.py（port, port_auto_fallback） |

- 默认行为：端口被占 → daemon 启动失败 → SessionStart hook 检测并输出 error hint
- Opt-in 行为：`port_auto_fallback=true` → port+1 重试最多 10 次 → 仍未找到则报错

### F1.8 — Unit Tests

| 属性 | 说明 |
|------|------|
| **理由** | 覆盖所有确定性逻辑。SW design review 中发现的 findings 形成了一份事实上的边界条件清单——测试必须覆盖这些 case。 |
| **优先级** | P0 — TDD |
| **依赖** | Phase 0 测试骨架 |

必写测试：
- `test_buffer.py`: schema 创建幂等（重复 CREATE TABLE 不报错）、WAL PRAGMA 实际执行（`PRAGMA journal_mode` 返回 "wal"）、content-hash 计算正确性（相同输入 → 相同 hash）、INSERT OR IGNORE 行为（UNIQUE 冲突时不报错也不覆盖）、多 session 并发写入（WAL + busy_timeout 验证，无 SQLITE_BUSY）、crash recovery scan 发现 unclosed sessions
- `test_config.py`（Phase 0 延续）: config merge 优先级、db_path `~` 展开

### F1.9 — CI Pipeline 扩展：Unit Test 全覆盖

| 属性 | 说明 |
|------|------|
| **理由** | Phase 1 新增 buffer.py 和 server.py——确定性逻辑的核心。CI 必须覆盖这些新增的单元测试。`dev.sh ci` 现在跑 lint + unit test（config + buffer + types + health contract），每次 commit 验证。 |
| **优先级** | P0 — CD 持续集成 |
| **依赖** | Phase 0 CI 骨架、F1.8（test_buffer.py） |

实现：扩展 `scripts/dev.sh` 的 `ci` 命令包含 `uv run pytest tests/unit/ tests/contract/`。CI 失败 = commit 不能合入。

### F1.10 — Contract Tests：Daemon HTTP API 接口契约

| 属性 | 说明 |
|------|------|
| **理由** | Phase 1 实现了 15 个 HTTP 端点。每个端点的 request/response schema 在 `02-interfaces.md` 中定义。契约测试验证每个端点返回的 JSON 结构与 spec 一致——status code、必需字段、字段类型。这些测试不依赖业务逻辑正确性（那是单元测试的职责），只验证接口协议。 |
| **优先级** | P0 — 接口契约 |
| **依赖** | F1.3-F1.6（HTTP API 端点就绪）、02-interfaces.md |

实现：
- `tests/contract/test_daemon_api.py`：每个端点 1 个 contract case
  - `POST /daemon/start` → 验证 response 含 pid, port, status
  - `POST /session/start` → 验证 response 含 session_id, is_new, recovery
  - `POST /turn/user` → 验证 response 含 turn_id, queued
  - `POST /context/inject` → 验证 response 含 context, sources
  - `GET /daemon/health` → 验证所有 health 字段存在
  - 错误 case：非法 body → 验证 422 + error code
- 使用 `jsonschema` 库验证 response 结构

### F1.11 — Debuggability：启动诊断 + SQLite 内省 + 请求追踪

| 属性 | 说明 |
|------|------|
| **理由** | Phase 1 是数据基础设施层——SQLite schema、session/turn 生命周期、context injection。任何环节出问题都需要立即定位。启动序列 6 步中每一步都可能失败（端口冲突、WAL 失败、schema 损坏、crash recovery 异常），每步都需要输出诊断信息。SQLite 是黑盒——没有内省能力就不知道表里有什么。 |
| **优先级** | P0 — 调试基础 |
| **依赖** | buffer.py、server.py、Phase 0 logging_config.py |

实现：

**启动诊断日志**（启动序列每步输出到 stderr）：
```
[daemon] Step 1/6: Opening SQLite at ~/.bible-cc/daemon.db... OK (2ms)
[daemon] Step 2/6: PRAGMA journal_mode=WAL... OK → "wal"
[daemon] Step 3/6: PRAGMA busy_timeout=5000... OK
[daemon] Step 4/6: Schema migration... OK (3 tables, schema_version=1)
[daemon] Step 5/6: Crash recovery scan... OK (0 unclosed sessions)
[daemon] Step 6/6: Starting uvicorn on 127.0.0.1:9777... OK
```

**Health check verbose mode**: `GET /daemon/health?verbose=true` 返回额外调试字段：
- `startup_timings`: 每步耗时（ms）
- `sqlite.detailed`: table row counts, index list, WAL 文件大小, page count
- `config_sources`: 每个配置项的来源

**SQLite 内省 debug 端点**（仅在 `--debug` 模式或 `log_level=DEBUG` 时注册，避免生产暴露）：
- `GET /daemon/debug/schema` → 返回所有表的 DDL
- `GET /daemon/debug/tables/{name}?limit=20` → 表前 20 行 + row count
- `GET /daemon/debug/turns?session_id=X&limit=N` → 指定 session 的 turns

**请求追踪**：每个 HTTP 请求生成 `X-Request-ID` header（UUID4），所有该请求的日志带 request_id。响应返回 `X-Request-ID` header。FastAPI middleware 记录 `{method} {path} → {status} ({duration_ms}ms)` 到 stderr。

**Config debug 落地**: `config.py` 的 `load_config(debug=True)` 集成 daemon 启动流程——启动时打印配置摘要（隐藏 token）。

---

## Phase 1 验收标准

- [ ] `./scripts/dev.sh ci` 通过（lint + unit test + contract test）
- [ ] Daemon 启动序列完整执行（WAL → migration → crash recovery → uvicorn），顺序不可变
- [ ] 启动时每步输出诊断日志到 stderr（6 steps + timing），格式正确
- [ ] `GET /daemon/health?verbose=true` 返回 startup_timings + sqlite.detailed + config_sources
- [ ] `GET /daemon/debug/schema` 返回三表 DDL（debug 模式）
- [ ] `GET /daemon/debug/turns?session_id=X` 返回 turns 列表（debug 模式）
- [ ] 每个 HTTP response 包含 `X-Request-ID` header，stderr 日志带 request_id
- [ ] 所有 HTTP API 端点可用，返回格式符合 `02-interfaces.md` 的 JSON schema
- [ ] `tests/contract/test_daemon_api.py` 通过：每个端点 1 个 contract case + 错误 case
- [ ] SQLite 三表创建幂等（重复执行无害）
- [ ] WAL mode 确认生效（`PRAGMA journal_mode` 返回 "wal"）
- [ ] 并发写入不产生 SQLITE_BUSY（至少 3 个并发 session 写入验证）
- [ ] `/context/inject` 三个场景分支行为正确
- [ ] 端口冲突时 daemon 启动失败 + stderr 可见 "port X occupied by PID Y"
- [ ] session crash recovery scan 正确发现 unclosed sessions，stderr 可见 "recovered N moments from M sessions"
- [ ] 单元测试全部通过（test_buffer.py 至少 10+ cases）

---

## Phase 1 产出文件

```
src/bible_cc_plugin/
├── daemon/
│   ├── __init__.py
│   ├── server.py              ← F1.2, F1.3-F1.6, F1.11 (FastAPI app, 所有端点, debug middleware)
│   └── buffer.py              ← F1.1, F1.11 (SQLite schema + CRUD + 内省方法)
tests/unit/
├── test_buffer.py             ← F1.8
├── test_config.py             ← (Phase 0 延续)
└── test_types.py              ← F0.3 验证
tests/contract/
├── test_daemon_health.py      ← F0.8 (延续)
└── test_daemon_api.py         ← F1.10 (HTTP API 契约)
├── test_config.py             ← (Phase 0 延续)
└── test_types.py              ← F0.3 验证
```
