# 10 — Deployment

> L2 | 领域总览 | 定义了 bible-cc-plugin 的安装、升级、卸载、重载流程。L3 `upgrade.md` 描述升级的完整生命周期。

---

## 1. 前置条件

| 条件 | 说明 |
|------|------|
| Python 3.10+ | 最低版本。不兼容 Python 3.9。 |
| `uv` | Python 包管理工具。安装指南：`curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Claude Code | 需要 plugin 支持的 Claude Code 版本。 |
| BiBLE Atlas（可选） | 非必需。plugin 可离线运行（本地 buffer + injection），但 MCP 搜索、flush 等功能需要 BiBLE Atlas 连通。 |

---

## 2. 安装

### 2.1 Marketplace 安装（推荐）

上架 Claude Code Marketplace 后，用户一键安装：

> **[TBD]** 以下命令语法为推测，待 Marketplace 正式上线后确认。

```
npx @anthropic-ai/claude-code plugins install <publisher>/bible-cc
```

Marketplace 自动执行：
1. 下载 plugin 源码到 `~/.claude/plugins/cache/`
2. 注册到 `~/.claude/plugins/installed_plugins.json`
3. 触发 Setup hook → 首次安装流程

### 2.2 手动安装（开发/未上架）

```bash
# 从任意目录安装
cd <workspace>/bible-cc-plugin
./bible-cc install
```

`./bible-cc install` 执行：
1. `rsync` 将 workspace 文件拷贝到 `~/.claude/plugins/bible-cc-plugin/`
2. `uv sync` 安装依赖
3. 自动注册到 `~/.claude/settings.json`（`enabledPlugins.bible-cc-plugin@skills-dir`）
4. `uv run python scripts/setup.py --non-interactive`（生成 `.mcp.json`、写 config、测试连通性）

```bash
# 等价的手动步骤（无需 install 脚本时）：
cd ~/.claude/plugins/
git clone <bible-cc-plugin-repo-url>
cd bible-cc-plugin
uv sync
# 注册：编辑 ~/.claude/settings.json enabledPlugins 添加 "bible-cc-plugin@skills-dir": true
uv run python -m bible_cc_plugin.scripts.setup
```

---

## 3. Daemon 生命周期

Daemon 由 hook 自动管理，用户一般不需要手动干预。

| 操作 | 实现 |
|------|------|
| 启动 | SessionStart hook → `POST /daemon/start`（idempotent） |
| 停止 | `POST /daemon/stop` 或 `reload-plugin --force` |
| 重启 | `reload-plugin --force` 后下一次 SessionStart 自动重启 |
| 状态 | `/bible-cc:status` 或 `GET /daemon/health` |

Daemon 没有 idle timeout——运行到关机或手动停止。

---

## 4. 升级

详见 `10-deployment/upgrade.md`（L3）。

核心流程：

```bash
# Marketplace 自动
npx @anthropic-ai/claude-code plugins update bible-cc

# 手动
cd ~/.claude/plugins/bible-cc-plugin
git pull
uv sync
# 然后 reload-plugin --force
```

升级触发后：
1. 新代码拉取 + `uv sync` 安装新依赖
2. `reload-plugin --force` 或用户手动 restart daemon
3. 下次 SessionStart → daemon 重新启动 → schema migration 自动执行（CREATE TABLE IF NOT EXISTS，幂等）

**向后兼容**：所有 migration 使用 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN`（如有）。不破坏已有数据。

---

## 5. 卸载

```
# Marketplace 自动
npx @anthropic-ai/claude-code plugins uninstall bible-cc

# 手动（一键脚本）
./bible-cc uninstall
```

`./bible-cc uninstall` 执行：
1. 停止 daemon（`POST /daemon/stop`）
2. 删除 `~/.bible-cc/`（SQLite DB + config.json）
3. 删除 `~/.claude/plugins/bible-cc-plugin/`（plugin 目录）
4. 删除 `.mcp.json`（workspace 中的生成文件）
5. 清理 `~/.claude/settings.json` 中 `enabledPlugins.bible-cc-plugin@skills-dir`

> ⚠️ 卸载不可逆。SQLite DB 和 config 永久删除。flush 到 BiBLE Atlas 的数据不受影响（存储在服务端）。

---

## 6. 重载（Reload）

`reload-plugin --force` 是 Claude Code 的原生机制。bible-cc-plugin 不需要额外处理：

1. Claude Code 重新加载 plugin.json + hooks.json + commands
2. 当前运行的 daemon 进程不受影响（除非用户手动 stop）
3. 如需重启 daemon（更新代码生效）：手动 stop daemon → 下次 SessionStart 自动重启

---

## 7. 开发环境工作流

以下适用于开发迭代期间。与终端用户安装（§2，一次性 setup）不同，开发期间的循环是 **编辑 → 重载 → 测试**。

### 7.1 首次搭建

```bash
# 1. 进入项目目录
cd bible-cc-plugin

# 2. 安装依赖
uv sync

# 3. 首次配置
uv run python -m bible_cc_plugin.scripts.setup
```

### 7.2 日常迭代

| 改动类型 | 操作 | 频率 |
|----------|------|------|
| 改 daemon 代码（server.py, buffer.py, detector.py 等） | 编辑 → kill daemon → 等下次 SessionStart 自动重启 | 高频 |
| 改 hooks / commands（hook.py, hooks.json, *.md） | 编辑 → `reload-plugin --force`（Claude Code 命令） | 高频 |
| 改 MCP server（mcp/server.py） | 编辑 → MCP server 自动随 session 重启 | 中频 |
| 改 config schema（config.py） | 编辑 → 手改 `~/.bible-cc/config.json` 验证 | 低频 |
| 新增依赖 | 编辑 `pyproject.toml` → `uv sync` | 低频 |
| 跑测试 | `uv run pytest` | 每次改动后 |
| 跑 lint | `uv run ruff check` | 每次改动后 |

### 7.3 快速重载 daemon

开发 daemon 代码时最常见的循环：

```bash
# 1. 停止当前 daemon
curl -X POST http://127.0.0.1:9777/daemon/stop

# 2. 编辑代码...

# 3. 重启（触发方式任选）：
#    a) 下一次 SessionStart 自动重启（推荐，验证正常启动路径）
#    b) 手动启动：uv run python -m bible_cc_plugin.scripts.daemon --start
```

### 7.4 使用 BiBLE Test Mode

开发集成测试时，用 BiBLE Atlas 内置的 test mode server（无 OpenSearch/Celery 依赖）：

```bash
# 终端 1：启动 test server
cd ../BiBLE-Atlas
uv run python -m bible.test_mode.server --port 5555

# 终端 2：bible-cc-plugin 连接 test server
export BIBLE_ATLAS_BASE_URL="http://localhost:5555"
uv run python -m bible_cc_plugin.scripts.setup
```

### 7.5 一键脚本

减少手动操作失误，同时供 CI 环境复用。

```bash
# scripts/dev.sh — 开发环境一键管理

# 初始化（首次）
./scripts/dev.sh init        # uv sync + setup + 启动 daemon

# 日常迭代
./scripts/dev.sh reload       # stop daemon → 等下次 SessionStart 自动重启
./scripts/dev.sh restart      # stop daemon → 立即 restart（跳过 SessionStart 等待）

# 测试与 lint
./scripts/dev.sh test         # uv run pytest
./scripts/dev.sh lint         # uv run ruff check && uv run ruff format --check

# CI
./scripts/dev.sh ci           # 完整 CI 流水线：lint + test + build
```

约束：
- `dev.sh` 是幂等的——`init` 可安全重复执行。
- CI 环境不需要启动 daemon（无 Claude Code 进程），`ci` 只跑 lint + test。
- 所有操作使用 `uv run`，不依赖 `source .venv/bin/activate`。

### 7.6 不需要反复做的事情

- ❌ 每次改代码后跑 `uv run python -m bible_cc_plugin.scripts.setup`（仅首次配置需要）
- ❌ 每次改代码后 `uv sync`（仅新增/更新依赖时需要）
- ❌ 手改 `~/.bible-cc/config.json` 后跑 setup（setup 不会覆盖已有 config）

---

## 8. 目录与文件清单

| 路径 | 用途 | 生命周期 |
|------|------|---------|
| `~/.bible-cc/config.json` | 配置文件 | 持久。卸载时删除。 |
| `~/.bible-cc/daemon.db` | SQLite 数据库 | 持久。卸载时删除。flush 到 BiBLE 的数据不受影响。 |
| `~/.bible-cc/daemon.pid` | 运行时 PID 文件（辅助用途；daemon run state 主要靠 `/daemon/health` HTTP check 判断） | 临时。daemon 停止后清理。 |
| `~/.claude/plugins/bible-cc-plugin/` | 插件源码 | 安装时创建。卸载时删除。 |
| `~/.claude/plugins/bible-cc-plugin/.mcp.json` | MCP server 定义（由 setup.py 生成，不提交 git） | 安装时生成。卸载时随 plugin 目录删除。 |
| `~/.claude/settings.json` → `enabledPlugins.bible-cc-plugin@skills-dir` | Plugin 注册 | 安装时写入。卸载时删除。 |

---

## 9. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `10-deployment/upgrade.md` | 升级的完整生命周期：版本检测、schema migration 策略、回滚、数据兼容性 | ✅ 完成 |

---

## 10. 参考文档

- [`../../CLAUDE.md`](../../CLAUDE.md) — Build & Test commands（`uv sync`, `uv run pytest`）
- [`../bible-claude-code-plugin-feasibility-report.md`](../bible-claude-code-plugin-feasibility-report.md) — Q3（Python + uv 选型）、Distribution
- [`04-config.md`](04-config.md) — config.json 位置与结构
