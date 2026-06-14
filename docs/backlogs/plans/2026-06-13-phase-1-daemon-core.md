# Phase 1: Daemon Core + Local Buffer

> **For agentic workers:** Phase 1 在 Phase 0 的"daemon 可启动、可 health check"基础上，添加数据持久化能力——SQLite、session/turn/moment schema、完整 HTTP API。

**Goal:** Daemon 完整启动序列、SQLite schema、HTTP API 全部端点可用、session/turn 生命周期、本地 context injection。

**Phase 0 已有（不重复实现）:** setup wizard、daemon lifecycle CLI（`./bible-cc start/stop/restart`）、health endpoint（`GET /daemon/health`）、config 系统、CI 骨架、hook bridge。**Phase 0 无可用 slash command**——`commands/status.md` 为空占位，Phase 1 交付首批 4 个 command。

**Architecture:** FastAPI HTTP server + SQLite (WAL mode) + 无 LLM 调用 + 无 BiBLE 通信。

**Tech Stack:** FastAPI, uvicorn, sqlite3 (stdlib), Pydantic

**预估: 5-7 天**

---

## Sub-Phase 总览 + 依赖关系

```
Phase 0 ──► 1a ──► 1b ──► 1c ──► 1d
              │      │      │      │
              ▼      ▼      ▼      ▼
           status  sessions context diagnose
```

| 子 Phase | 文件 | 内容 | 交付 Command | 依赖 | 预估 |
|----------|------|------|-------------|------|------|
| **1a** | [`2026-06-14-phase-1a-sqlite-schema.md`](../Phase-1/2026-06-14-phase-1a-sqlite-schema.md) | SQLite schema + buffer.py + migration + content-hash + config 集成 | `/bible-cc:status` | Phase 0 | 2d |
| **1b** | [`2026-06-14-phase-1b-session-turn.md`](../Phase-1/2026-06-14-phase-1b-session-turn.md) | Session/turn 端点 + seq 分配 + `GET /daemon/sessions` + 契约测试 | `/bible-cc:sessions` | **1a**（SQLite schema + CRUD） | 1.5d |
| **1c** | [`2026-06-14-phase-1c-context-injection.md`](../Phase-1/2026-06-14-phase-1c-context-injection.md) | Context injection 三场景 + crash recovery 快路 + seq 恢复 | `/bible-cc:context` | **1b**（session/turn 端点 + recovery 数据） | 1.5d |
| **1d** | [`2026-06-14-phase-1d-operability.md`](../Phase-1/2026-06-14-phase-1d-operability.md) | 端口冲突 + debug 端点 + verbose health + request-id | `/bible-cc:diagnose` | **1c**（完整 daemon HTTP API） | 1d |

### 关键依赖链

| 依赖 | 为什么 |
|------|--------|
| 1a → 1b | 1b 的 session/turn 端点依赖 buffer.py 的 CRUD 函数和 SQLite schema |
| 1b → 1c | 1c 的 `/context/inject` 依赖 `/session/start` 提供的 recovery 数据和 turn 缓冲 |
| 1c → 1d | 1d 的 debug 端点、verbose health 需要完整 HTTP API 和 SQLite 数据存在 |

### 测试环境标注约定（全文适用）

| 标注 | 含义 | 编写时机 | 需要 |
|------|------|---------|------|
| `[Unit]` | 纯函数，`tmp_path` SQLite / pytest mock / stub | **Pre-impl**（先于实现，Red-Green） | 无外部进程 |
| `[Integration]` | 需要 daemon 进程、真实 SQLite WAL 并发、`subprocess` | **Post-impl**（实现后验证） | `uv run` daemon 实例 |
| `[Contract]` | 需要 daemon HTTP 进程运行，验证响应 JSON schema | **Post-impl**（实现后验证） | `uv run` daemon 实例 |

**各 Sub-Phase 默认标注**：

| Sub-Phase | 默认标注 | 例外 |
|-----------|---------|------|
| 1a（buffer/schema/migration/hash/config） | `[Unit] [Pre]` | 无 |
| 1b.1-1b.3（session/turn 端点逻辑） | `[Unit] [Pre]` | route handler → `[Integration]` |
| 1b.4（契约测试） | `[Contract] [Post]` | 无 |
| 1c（注入/恢复/seq） | `[Unit] [Pre]` | crash recovery 慢路 → `[Integration]` |
| 1d（operability） | 混合，逐条标注 | hint 构建→`[Unit] [Pre]`，端点→`[Integration]`，schema→`[Contract]` |

---

## Phase 1 验收总览

- [ ] `./scripts/dev.sh ci` 通过（lint + unit test + contract test）
- [ ] Daemon 启动序列完整执行（open→WAL→busy_timeout→migration→crash recovery→uvicorn），顺序不可变
- [ ] 启动时 6 步诊断日志输出到 `daemon.log`（带 timing）
- [ ] `GET /daemon/health?verbose=true` 返回 startup_timings + sqlite.detailed + config_sources
- [ ] `GET /daemon/debug/schema` 返回三表 DDL（debug 模式）
- [ ] `GET /daemon/debug/turns?session_id=X` 返回 turns 列表（debug 模式）
- [ ] 每个 HTTP response 包含 `X-Request-ID` header
- [ ] 所有 HTTP API 端点可用，返回格式符合 `02-interfaces.md` 的 JSON schema
- [ ] `tests/contract/test_daemon_api.py` 通过：每端点 1 contract case + 错误 case
- [ ] `tests/unit/test_buffer.py` 通过：所有 CRUD 函数 100% 覆盖
- [ ] SQLite 四表创建幂等（重复执行无害）
- [ ] WAL mode 确认生效（`PRAGMA journal_mode` 返回 "wal"）
- [ ] 并发写入不产生 SQLITE_BUSY（≥3 并发 session）
- [ ] `/context/inject` 三场景分支行为正确
- [ ] Crash recovery 快路正确发现 unclosed sessions 并恢复数据
- [ ] 端口冲突时 daemon 启动失败 + error hint
- [ ] **每个 feature 的意图测试全部通过**（≥ 15 个意图测试）
- [ ] **4 个 slash command 全部可用**：`/bible-cc:status`、`/bible-cc:sessions`、`/bible-cc:context`、`/bible-cc:diagnose`

---

## 产出文件

```
commands/
├── status.md              ← 1a：从空占位落地
├── sessions.md            ← 1b：新增
├── context.md             ← 1c：新增
└── diagnose.md            ← 1d：新增（骨架）

src/bible_cc_plugin/daemon/
├── server.py              ← 所有 FastAPI 端点（15 个）+ middleware
├── buffer.py              ← SQLite schema + CRUD + migration + content-hash + seq 恢复
└── injector.py            ← 三场景分支逻辑

tests/
├── unit/test_buffer.py    ← 1a + 1c.2-1c.3
├── unit/test_injector.py  ← 1c.1
├── unit/test_config.py    ← 1a.5（Phase 0 延续）
└── contract/test_daemon_api.py ← 1b.4
```

---

## 设计依据

- `docs/sw-design/` 下 27 个 L1/L2/L3 文件
- `docs/bible-claude-code-plugin-feasibility-report.md`
- `docs/command-priority-table.md`
