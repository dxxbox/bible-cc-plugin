# Phase 0: 1st Call — Install & Run

> **状态：✅ 完成 (2026-06-14)**

Phase 0 walking skeleton 已完成。安装、配置、daemon 启停、health check、卸载全链路跑通。
超出原计划的额外产出：
- `bible-cc` 一键 CLI（install/start/stop/restart/status/logs/verify/reinstall/uninstall）
- Hook bridge（SessionStart 自动启动 daemon，turn-user/turn-tool/session-end 骨架）
- Marketplace 安装模式（`bible-cc-local`，source: "./"）
- Daemon 日志持久化（`~/.bible-cc/daemon.log`）+ `./bible-cc logs`
- 结构化日志（`logging_config.py`，JSON-line 格式）

> **For agentic workers:** Phase 0 是 walking skeleton——安装、配置、启动 daemon、health check、停止、卸载。这一条链路跑通后，所有后续 Phase 都是在此基础上增量添加能力。

**Goal:** 用户从"零"到"plugin 安装完成 → daemon 运行中 → health check green → 可停止/重启/卸载"。这是整个项目的"1st call"。

**Architecture:** Setup wizard（交互式 CLI）→ 写 config.json → 启动 FastAPI daemon → `GET /daemon/health` 返回真实进程状态。Phase 0 无 SQLite，无 session。

**Tech Stack:** Python 3.10+, uv, Pydantic, FastAPI, uvicorn

**预估: 2-3 天 | 实际: Phase 0 核心 ~1 天 + marketplace 迁移 + 日志可观测性**

**1st Call 用户旅程:**
```
# 1. 安装
cd <workspace>/bible-cc-plugin
./bible-cc install
  → rsync → ~/.claude/plugins/marketplaces/bible-cc-local/
  → uv sync
  → 注册 marketplace + claude plugin install

# 2. 首次配置（或 Setup hook 自动触发）
./bible-cc setup
  → BiBLE Atlas URL? [http://localhost:5555]
  → Token? (optional) []
  → Testing connectivity... OK (15ms)
  → Config written to ~/.bible-cc/config.json
  → Setup complete.

# 3. 启动 daemon（SessionStart hook 自动触发，或手动）
./bible-cc start
  → [daemon] Starting on 127.0.0.1:9777... OK (pid=12345)

# 4. 验证
./bible-cc status
  → Daemon: running (pid=12345, port=9777, uptime=5s)

# 5. 停止
./bible-cc stop
  → Daemon stopped.

# 6. 重启
./bible-cc restart
  → Stopping... OK → Starting... OK (pid=12346)

# 7. 查看日志
./bible-cc logs
  → {"ts":"...", "level":"INFO", "msg":"daemon starting on port 9777"}
  → INFO: Uvicorn running on http://127.0.0.1:9777
```

---

## Feature 逐个讨论

### F0.1 — pyproject.toml + 项目元配置

| 属性 | 说明 |
|------|------|
| **理由** | 所有 Python 依赖管理、入口点、构建配置的单一事实来源。`uv sync` 是安装的第一步，`uv run` 是 CLAUDE.md 硬性约束。没有 pyproject.toml，无法安装依赖，无法运行任何代码。 |
| **优先级** | P0 — 安装基础 |
| **依赖** | 无 |

### F0.2 — plugin.json + .mcp.json（生成） + hooks/hooks.json

| 属性 | 说明 |
|------|------|
| **理由** | Claude Code 识别 plugin 的清单文件。`plugin.json` 和 `hooks/hooks.json` 是静态提交的；`.mcp.json` 由 `setup.py` 在 install 时动态生成（不提交 git），内容使用用户配置的 base_url/token。 |
| **优先级** | P0 — Claude Code 集成基础 |
| **依赖** | 无 |

### F0.3 — src/bible_cc_plugin/types.py（Phase 0 最小集）

| 属性 | 说明 |
|------|------|
| **理由** | Phase 0 只需要与 install/health/lifecycle 相关的类型。后续 Phase 扩展此文件。 |
| **优先级** | P0 |
| **依赖** | pyproject.toml（pydantic 依赖） |

Phase 0 定义：
- `Config`: bible（base_url, token）, daemon（port, port_auto_fallback）
- `HealthStatus`: status, pid, port, uptime, daemon_version
- `DaemonStartResponse`, `DaemonStopResponse`, `ErrorResponse`

Phase 1+ 扩展：Session, Turn, Moment, 完整 Config 域。

### F0.4 — src/bible_cc_plugin/config.py（配置系统）

| 属性 | 说明 |
|------|------|
| **理由** | Setup wizard 写 config，daemon 读 config——这些都在 Phase 0 发生。三阶加载（default → config.json → env var）是 CLAUDE.md 硬性约束。 |
| **优先级** | P0 — 配置基础设施 |
| **依赖** | types.py（Config model） |

### F0.5 — Setup Wizard（scripts/setup.py）

| 属性 | 说明 |
|------|------|
| **理由** | **这是 1st call 的核心**——用户安装 plugin 后触发的第一个交互。Setup wizard 让用户从零到"config 就绪"不需要知道 config.json 格式或 env var 名称。Idempotent——重复运行不会覆盖已有 config（仅提示已存在）。BiBLE 连通性测试让用户立即知道配置是否正确。**从 Phase 6 提前到 Phase 0**，因为安装是第一步，不是最后一步。 |
| **优先级** | P0 — 1st call 核心 |
| **依赖** | config.py（write config）、client.py 尚未存在（用裸 httpx check_health） |

流程：
```
1. 检测 ~/.bible-cc/config.json → 存在则提示"已配置"，跳过询问
2. 提示 BiBLE Atlas base_url [默认: http://localhost:5555]
3. 提示 token [可选]
4. 写 config.json
5. 测试 BiBLE 连通性（GET /health，超时 5s）
6. 输出结果：✓ Connected (15ms) 或 ⚠ Unreachable (will retry later)
7. 提示："Daemon 将在下次 SessionStart 自动启动。
   手动启动: uv run python -m bible_cc_plugin.scripts.daemon start"
```

Setup hook 注册在 `hooks.json` 中，指向 `uv run python -m bible_cc_plugin.scripts.setup`。

### F0.6 — Daemon Lifecycle CLI（scripts/daemon.py）

| 属性 | 说明 |
|------|------|
| **理由** | **1st call 的第二个核心**——start/stop/status/restart 是用户日常操作 daemon 的四个基本动作。不是 skeleton——Phase 0 就必须完整可用。SessionStart hook 依赖 `daemon start` 的幂等性（daemon 已运行时直接返回当前状态）。 |
| **优先级** | P0 — daemon 生命周期 |
| **依赖** | config.py（port）、F0.7（daemon server with health endpoint） |

四个命令：
- `start [--debug]`: 后台启动 uvicorn → 等待 health check 返回 200（最多等 5s）→ 输出 pid + port。Idempotent：已运行时输出 "already running (pid=X)"。
- `stop`: `POST /daemon/stop` → 等待进程退出（最多等 5s）→ 输出 "stopped"。
- `status`: `GET /daemon/health` → 格式化输出（running/not running + pid + port + uptime）。
- `restart`: stop → start（保留 --debug flag）。

### F0.7 — Daemon Server 最小实现（server.py）

| 属性 | 说明 |
|------|------|
| **理由** | **1st call 的第三个核心**——daemon 进程必须能启动并响应 health check。Phase 0 只实现两个端点：`POST /daemon/start`（idempotent）、`POST /daemon/stop`、`GET /daemon/health`（返回真实 pid/port/uptime）。无 SQLite，无 session。 |
| **优先级** | P0 — daemon 进程基础 |
| **依赖** | config.py（port）、types.py（HealthStatus） |

FastAPI app 最小集：health endpoint 用 `os.getpid()` 和 `time.time() - start_time` 计算真实值。Stop endpoint 触发 `sys.exit(0)`。Start endpoint 返回 idempotent 响应。

### F0.8 — CI Pipeline + 测试

| 属性 | 说明 |
|------|------|
| **理由** | CD 原则——CI 从第一天存在。`./scripts/dev.sh ci` 覆盖 lint + unit test（config）+ contract test（health schema）。Phase 0 结束 = CI green = 1st call 已验证。 |
| **优先级** | P0 — CD 基础 |
| **依赖** | pyproject.toml、config.py、daemon server |

- `scripts/dev.sh`: init, test, lint, ci
- `tests/unit/test_config.py`: 三阶加载、env override、非法值回退（10+ cases）
- `tests/contract/test_daemon_health.py`: 启动 daemon → GET /health → 验证 schema

### F0.9 — Debuggability 基础设施

| 属性 | 说明 |
|------|------|
| **理由** | 1st call 出问题时不能靠猜。Config 加载来源追踪 + daemon start --debug + structured logging 从第一天就内建。 |
| **优先级** | P0 |
| **依赖** | config.py、daemon.py |

- `load_config(debug=True)` → stderr 输出来源追踪
- `daemon start --debug` → uvicorn log_level=debug，每个 request 输出 stderr
- `src/bible_cc_plugin/logging_config.py` → 统一 structured logging

### F0.10 — Hook Bridge（bible_cc_plugin.scripts.hook）

| 属性 | 说明 |
|------|------|
| **理由** | SessionStart 时自动启动 daemon 是用户体验的基础——用户不应该每次开 Claude Code 都手动 `./bible-cc start`。turn-user/turn-tool/session-end 骨架为 Phase 1 做好准备。 |
| **优先级** | P0 |
| **依赖** | config.py、daemon server |

实现：
- `session-start`: 幂等启动 daemon（先 check health，未运行则 spawn uvicorn），失败不阻断 Claude Code
- `turn-user` / `turn-tool` / `session-end`: Phase 0 静默 pass-through

### F0.11 — Daemon 日志可观测性

| 属性 | 说明 |
|------|------|
| **理由** | 原始实现将 daemon stdout/stderr 丢弃到 /dev/null，启动失败时无法诊断。 |
| **优先级** | P1（Phase 0 末期追加） |
| **依赖** | hook.py、daemon.py、server.py |

实现：
- daemon 输出写入 `~/.bible-cc/daemon.log`（append 模式）
- 健康检查超时时打印最后 20 行日志
- `./bible-cc logs` 命令查看最近 50 行
- `server.py` 启用结构化日志（startup + shutdown 事件）
- `httpx.ConnectError` → DEBUG（启动中，正常）；其他异常 → WARNING（daemon 已运行但异常）

---

## Phase 0 验收标准

- [x] `uv sync` 成功，无错误
- [x] `./scripts/dev.sh ci` 通过（lint → unit test → contract test，exit code 0）
- [x] `uv run python -m bible_cc_plugin.scripts.setup` 交互式完成首次配置
- [x] Setup 重复运行不覆盖已有 config（idempotent）
- [x] `~/.bible-cc/config.json` 格式正确
- [x] `uv run python -m bible_cc_plugin.scripts.daemon start` 启动 daemon
- [x] `GET /daemon/health` 返回真实 pid + port + uptime（非硬编码）
- [x] `daemon status` 显示 running/not running + 详细信息
- [x] `daemon stop` 优雅停止 daemon 进程
- [x] `daemon restart` stop → start 完整流程
- [x] `daemon start` 重复执行不报错（idempotent）
- [x] `daemon start --debug` 启动后 stderr 可见 request 日志
- [x] `load_config(debug=True)` stderr 输出每项来源
- [x] plugin.json、hooks/hooks.json 格式正确；.mcp.json 由 setup.py 正确生成
- [x] `tests/contract/test_daemon_health.py` 通过
- [x] `./bible-cc logs` 查看 daemon 日志
- [x] 30 tests passed (22 unit + 8 contract)

---

## Phase 0 产出文件

```
bible-cc-plugin/
├── bible-cc                    ← 一键 CLI（install/start/stop/status/logs/verify/reinstall/uninstall）
├── .claude-plugin/
│   ├── plugin.json             ← F0.2 (静态提交)
│   └── marketplace.json        ← marketplace 定义 (source: "./")
├── .mcp.json                   ← F0.2 (由 setup 生成，不提交 git)
├── hooks/hooks.json            ← F0.2 (Setup/SessionStart/UserPromptSubmit/PostToolUse/Stop hooks)
├── commands/
│   └── status.md               ← 占位（Phase 5 正式实现）
├── pyproject.toml              ← F0.1
├── src/bible_cc_plugin/
│   ├── __init__.py
│   ├── types.py                ← F0.3 (Phase 0 最小集)
│   ├── config.py               ← F0.4
│   ├── logging_config.py       ← F0.9
│   ├── scripts/
│   │   ├── __init__.py
│   │   ├── hook.py             ← F0.10 (hook bridge, session-start 自动启动 daemon)
│   │   ├── setup.py            ← F0.5 (setup wizard)
│   │   └── daemon.py           ← F0.6 (start/stop/status/restart CLI)
│   └── daemon/
│       ├── __init__.py
│       └── server.py           ← F0.7 (health + start/stop 端点)
├── scripts/
│   ├── dev.sh                  ← F0.8 (CI 骨架)
│   ├── uninstall.sh            ← 完整卸载
│   └── verify-install.sh       ← 安装后验证
└── tests/
    ├── __init__.py
    ├── unit/
    │   ├── __init__.py
    │   └── test_config.py      ← F0.8
    ├── contract/
    │   ├── __init__.py
    │   └── test_daemon_health.py ← F0.8
    ├── integration/
    │   └── __init__.py          ← Phase 1+
    └── e2e/
        └── __init__.py          ← Phase 5+
```

---

## 与原始计划的主要偏差

| 计划 | 实际 | 原因 |
|------|------|------|
| `scripts/setup.py`, `scripts/daemon.py` 为独立脚本 | 移入 `src/bible_cc_plugin/scripts/`，使用 `-m` 模块路径调用 | `uv run python -m bible_cc_plugin.scripts.setup` 范式 |
| 安装到 `~/.claude/plugins/bible-cc-plugin/` + `skills-dir` 注册 | 安装到 `~/.claude/plugins/marketplaces/bible-cc-local/` + `known_marketplaces.json` + `extraKnownMarketplaces` | Claude Code marketplace 模式 |
| 不包含 marketplace.json | 新增 `.claude-plugin/marketplace.json` (source: "./") | Plugin 即 marketplace 目录 |
| F0.10 hook bridge 未在原始计划中 | 已实现 session-start 自动启动 daemon | SessionStart 自动启动是用户体验基础 |
| F0.11 日志可观测性未在原始计划中 | daemon 日志写入文件 + tail 诊断 + `./bible-cc logs` | /dev/null 丢弃所有输出导致启动失败无法诊断 |
