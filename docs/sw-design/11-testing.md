# 11 — Testing

> L2 | 领域总览 | 定义 bible-cc-plugin 的测试策略、测试分层、场景选择原则和全局验收标准。L3 `unit.md`、`integration.md`、`e2e.md` 分别细化单元、集成和端到端测试计划。

---

## 1. 定位

bible-cc-plugin 的测试目标不是证明每个实现细节存在，而是证明四组件架构在用户真实工作流中可靠：

```
Claude Code hooks / commands / MCP
  → bible-cc daemon
  → local SQLite buffer
  → optional moment detection LLM
  → BiBLE Atlas V4 API
```

测试计划围绕用户场景展开，每个场景都必须包含 sunny path 和 rainy path。sunny path 证明核心价值链路跑通；rainy path 证明插件满足 graceful degradation：daemon、BiBLE、LLM、SQLite、hook 任一环节出问题时，Claude Code 不被阻塞，用户能看到足够诊断信息。

---

## 2. 全局约束

以下约束对所有测试文件、测试夹具和 CI 配置具有强制力。

1. **`uv run` only**: 所有测试命令使用 `uv run`，禁止依赖 venv activation。
2. **测试环境隔离**: 每个测试用例使用临时 `HOME` 或显式 `BIBLE_CC_DB_PATH`，不得读写真实 `~/.bible-cc/daemon.db`。
3. **端口隔离**: 集成/E2E 测试使用动态空闲端口，只有文档示例使用默认 daemon 端口 `9777`。
4. **BiBLE 依赖使用真实 server**: 集成/E2E 测试默认连接可用的 BiBLE server 测试实例。测试数据必须使用独立 `kb_index`、tag、session id 前缀，避免污染真实用户数据。
5. **LLM 默认 stub**: moment detection、consult 空 query 归纳等 LLM 调用在自动化测试中使用 deterministic stub。只在人工 smoke 或显式慢测中允许真实 Anthropic API。
6. **结构断言优先**: LLM 相关输出不做精确文本断言，只断言 JSON schema、moment type、必要字段、dedup 行为、hint 格式和错误处理。
7. **Hook 不阻塞是验收条件**: `UserPromptSubmit`、`PostToolUse` hook 的 rainy path 必须验证 daemon 不可达时静默跳过，Claude Code turn 不失败。
8. **SessionStart 不调用 BiBLE**: 任何自动化测试都不得把 `/context/inject` 的成功建立在 BiBLE 可达上。它只能读取本地 SQLite buffer。
9. **Command/MCP 边界要测**: commands 操作 daemon；MCP tools 直接操作 BiBLE Atlas。测试必须覆盖二者互不依赖。
10. **并发写必须测**: SQLite WAL + `busy_timeout=5000` 是硬性约束，集成测试必须覆盖多 session 并发写入。
11. **敏感信息不进日志**: 测试日志可记录字段名、长度、hash、状态码，不记录 token、完整用户 prompt、大体积 tool output 或上传文件正文。
12. **失败可诊断**: 每个 rainy path 都要断言用户可见 hint、structured error、health/status 字段或命令输出中至少有一种诊断线索。

---

## 3. 测试分层

| 层级 | 目标 | 依赖 | 典型用例 |
|------|------|------|----------|
| Unit | 验证确定性逻辑 | 无 daemon、无网络、stub LLM | config merge、SQLite schema、content-hash、prompt builder、client payload |
| Integration | 验证组件协议 | daemon + temp SQLite + real BiBLE server + stub LLM | HTTP API、hook bridge、MCP tools、commands、flush、concurrency |
| E2E | 验证用户工作流 | plugin package + hook scripts + daemon + real BiBLE server | install/status、fresh session、clear recovery、review/push、crash recovery |

测试投资优先级：

1. Unit 覆盖所有确定性代码和历史 design review finding。
2. Integration 覆盖跨组件协议、rainy path、并发和 BiBLE V4 契约。
3. E2E 只覆盖关键用户旅程，数量少但必须稳定、可复现。

---

## 4. 测试目录规划

```
tests/
├── unit/
│   ├── test_config.py
│   ├── test_buffer.py
│   ├── test_detector.py
│   ├── test_injector.py
│   ├── test_client.py
│   └── test_mcp.py
├── integration/
│   ├── test_daemon_http.py
│   ├── test_hook_bridge.py
│   ├── test_capture_flush.py
│   ├── test_recall_consult.py
│   ├── test_commands.py
│   ├── test_mcp_server.py
│   └── test_concurrency.py
├── e2e/
│   ├── test_install_status_flow.py
│   ├── test_session_capture_flow.py
│   ├── test_clear_recovery_flow.py
│   ├── test_crash_recovery_flow.py
│   ├── test_operations_lifecycle.py
│   └── test_bible_unreachable_flow.py
└── fixtures/
    ├── bible_seed/
    │   ├── memory-message.json
    │   ├── knowledge-note.md
    │   └── skill-standard/
    ├── hook_payloads/
    └── conversations/
```

CI commands:

```bash
uv sync
uv run ruff check
uv run ruff format --check
uv run pytest tests/unit
uv run pytest tests/integration
uv run pytest tests/e2e
```

---

## 5. Scenario Coverage Matrix

| User scenario | Unit | Integration | E2E |
|---------------|------|-------------|-----|
| Install and first setup | config defaults, env overrides | setup/status command, health endpoint | install → setup → status |
| Daemon restart and plugin reload | migration helpers, pid handling | stop/start endpoints, stale PID, SessionStart auto-start | restart/reload preserves local data |
| Upgrade | config/schema migration helpers | idempotent migration, dependency sync assumptions | upgrade preserves config and pending data |
| Uninstall | path resolution, cleanup plan | stop daemon + scoped local data cleanup + plugin registry/cache guidance | uninstall removes local state and leaves server data untouched |
| Fresh session starts | context formatter, empty fallback | SessionStart API, no BiBLE call | hook returns empty/skip injection |
| `/clear` or compaction recovery | injector selection rules | same-session `/context/inject` | buffer survives context loss |
| Crash recovery | session state transitions | unclosed session scan + async recovery | previous session context restored |
| Capture key moments | detector prompt/parser, dedup hash | turn endpoints + background detection | user sees pending moment hint |
| Review/edit/discard moments | moment update rules | review endpoints and command output | user edits before flush |
| Stop/push flush | client import payload | real BiBLE memory import + retry | session end persists moments |
| Consult cross-domain search | result merge/sort | three-domain parallel search | user-triggered recall works |
| MCP model tools | tool schemas, client calls | stdio server against real BiBLE server | model-facing tools stay daemon-independent |
| BiBLE unreachable | error mapping | flush deferred, MCP errors | Claude Code continues offline |
| Daemon unavailable | hook error handling | hook bridge silent skip | turn continues without plugin |
| Port conflict | port selection logic | startup error + hint | SessionStart shows actionable hint |
| Concurrent sessions | sequence/dedup rules | WAL write contention | two sessions do not lose turns |
| Capture pause/bypass | regex/config rules | hook skips buffer writes | private session leaves no moments |
| Operation failure paths | error mapping | daemon stop failure, locked DB, missing config | actionable operation diagnostics |

---

## 6. Acceptance Gates

Before a feature is considered ready:

1. Relevant unit tests pass for deterministic logic.
2. At least one integration test covers each touched daemon endpoint, command, hook, or MCP tool.
3. Any changed user workflow has one E2E sunny path and at least one rainy path.
4. `uv run ruff check`, `uv run ruff format --check`, and `uv run pytest` pass.
5. Test fixtures do not require real user config, real `~/.bible-cc`, or real Anthropic API. Tests that touch BiBLE use a configured test server namespace.
6. Known design review risks remain covered: Python + `uv`, SessionStart self-contained startup, WAL concurrency, content-hash dedup, local-only injection, `.mcp.json` literals, hook timeouts, full tool output storage, review endpoints, and port conflict hints.
7. Real-server async operations use bounded polling: import/download task checks must define max attempts, interval, and failure diagnostics before any E2E can pass.

---

## 7. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `11-testing/unit.md` | 单元测试计划：确定性逻辑、stubs、fixtures、module matrix | ✅ 完成 |
| `11-testing/integration.md` | 集成测试计划：daemon + SQLite + real BiBLE server + hook/command/MCP 协议 | ✅ 完成 |
| `11-testing/e2e.md` | 端到端测试计划：用户场景、sunny/rainy path、自动化边界 | ✅ 完成 |

---

## 8. 参考文档

- [`01-architecture-overview.md`](01-architecture-overview.md) — 四组件模型、pull model、硬性约束
- [`02-interfaces.md`](02-interfaces.md) — daemon HTTP API、BiBLE V4 API、MCP tool schema、hook 约定
- [`../bible-claude-code-plugin-feasibility-report.md`](../bible-claude-code-plugin-feasibility-report.md) — 架构决策和 moment detection 设计
- [`../design-review-2026-06-12.md`](../design-review-2026-06-12.md) — 已解决 findings 和必须防回归的风险
- [`../command-priority-table.md`](../command-priority-table.md) — 命令优先级、MVP 范围、server/plugin 边界
