# bible-cc-plugin User Guide

> 适用于首次安装和测试。当前版本: **Phase 0 — 1st Call**（安装、配置、daemon 生命周期、反复重装测试）。

---

## 1. 前置条件

| 条件 | 如何检查 | 如何安装 |
|------|---------|---------|
| Python 3.10+ | `python3 --version` | https://www.python.org/downloads/ |
| `uv` | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Claude Code | 已安装即可 | — |
| BiBLE Atlas（可选）| 有可访问的 URL 即可 | Phase 0 不需要 |

---

## 2. 安装

```bash
cd ~/.claude/plugins/
git clone <repo-url> bible-cc-plugin
cd bible-cc-plugin
uv sync
```

---

## 3. 配置

### 3.1 交互式配置（首次使用）

```bash
uv run python scripts/setup.py
```

```
=== bible-cc-plugin Setup ===

BiBLE Atlas URL [http://localhost:5555]:   ← 输入地址，回车用默认
Token (optional, press Enter to skip):     ← 需要认证时输入

Config written to ~/.bible-cc/config.json
Testing BiBLE connectivity... OK (15ms)
Setup complete.
```

幂等——重复运行不会覆盖已有配置：
```
Config already exists at ~/.bible-cc/config.json
To reconfigure, use --reset. To skip prompts, use --non-interactive.
```

### 3.2 非交互式配置（自动化/CI）

```bash
# 全部用默认值
uv run python scripts/setup.py --non-interactive

# 指定参数
uv run python scripts/setup.py --non-interactive --base-url http://bible:5555 --token sk-ant-xxx

# 通过环境变量
BIBLE_ATLAS_BASE_URL=http://bible:5555 uv run python scripts/setup.py --non-interactive
```

### 3.3 重置配置

```bash
# 删除旧 config + 停止残留 daemon，然后交互式重新配置
uv run python scripts/setup.py --reset

# 重置 + 无交互
uv run python scripts/setup.py --reset --non-interactive
```

### 3.4 Debug 模式

```bash
uv run python scripts/setup.py --debug          # 交互式 + 详细诊断
uv run python scripts/setup.py --non-interactive --debug  # 自动化 + 详细诊断
```

---

## 4. Daemon 生命周期

Daemon 是后台 HTTP 服务（FastAPI），监听 `127.0.0.1:9777`。

### 启动

```bash
uv run python scripts/daemon.py start
# → [daemon] Starting on 127.0.0.1:9777... OK (pid=12345, port=9777)
```

幂等——已运行时不会重复启动。

### 状态

```bash
uv run python scripts/daemon.py status
# → Daemon: running
#     PID:    12345
#     Port:   9777
#     Uptime: 3m 22s
```

### 停止

```bash
uv run python scripts/daemon.py stop            # 优雅关闭
uv run python scripts/daemon.py stop --force    # 优雅关闭失败则 kill -9
```

### 重启

```bash
uv run python scripts/daemon.py restart
```

### Debug 模式

```bash
uv run python scripts/daemon.py start --debug
# stderr 输出所有 HTTP request/response
```

---

## 5. 验证 Health Check

```bash
curl -s http://127.0.0.1:9777/daemon/health | python3 -m json.tool
```

```json
{
    "status": "ok",
    "pid": 12345,
    "port": 9777,
    "uptime": 42,
    "sessions": {"active": 0, "completed": 0},
    "buffer": {"total_turns": 0, "pending_moments": 0},
    "bible_connectivity": {"reachable": null, "latency_ms": null},
    "sqlite": {"integrity": "ok", "schema_version": 0, "size_bytes": 0}
}
```

| 字段 | Phase 0 状态 |
|------|-------------|
| `status`, `pid`, `port`, `uptime` | ✅ 真实值 |
| `sessions`, `buffer` | Phase 1 实现（恒为 0） |
| `bible_connectivity` | Phase 3 实现（恒为 null） |
| `sqlite` | Phase 1 实现（占位值） |

---

## 6. 反复重装测试

### 一键验证

```bash
./scripts/verify-install.sh
```

自动执行 16 项检查：
1. 基础环境（uv、Python、pyproject.toml、.venv）
2. 配置（config.json 存在 + 有效，不存在时自动 `setup --non-interactive`）
3. Daemon 生命周期（start → status → health → stop → 确认停止）
4. 代码质量（ruff lint + format）
5. 测试（unit + contract）

全部 PASS → exit 0。任一 FAIL → exit 1。

### 一键卸载

```bash
./scripts/uninstall.sh           # 停止 daemon + 删除 ~/.bible-cc/ + 删除插件目录
./scripts/uninstall.sh --force   # 同上，daemon 无响应时 kill -9
```

### 完整重装循环

```bash
# 1. 彻底清理
./scripts/uninstall.sh --force

# 2. 重新安装
git clone <repo> ~/.claude/plugins/bible-cc-plugin
cd ~/.claude/plugins/bible-cc-plugin
uv sync

# 3. 无交互配置
uv run python scripts/setup.py --non-interactive

# 4. 验证
./scripts/verify-install.sh

# 5. 开始测试...

# 6. 重置重来（不重新 clone）
uv run python scripts/setup.py --reset --non-interactive
./scripts/verify-install.sh
```

---

## 7. 开发工具

```bash
./scripts/dev.sh init       # uv sync + setup
./scripts/dev.sh test       # 全部测试
./scripts/dev.sh lint       # Lint + 格式化检查
./scripts/dev.sh ci         # 完整 CI（lint → unit → contract）
./scripts/dev.sh reload     # 停止 daemon（下次 SessionStart 自动重启）
./scripts/dev.sh restart    # 停止 + 启动 daemon
```

---

## 8. 配置参考

配置文件：`~/.bible-cc/config.json`

```json
{
  "bible": {"base_url": "http://localhost:5555", "token": null, "kb_index": "bible-cc"},
  "daemon": {"port": 9777, "port_auto_fallback": false}
}
```

环境变量覆盖（最高优先级）：

| 环境变量 | 对应配置 |
|---------|---------|
| `BIBLE_ATLAS_BASE_URL` | `bible.base_url` |
| `BIBLE_ATLAS_TOKEN` | `bible.token` |
| `BIBLE_CC_DAEMON_PORT` | `daemon.port` |
| `BIBLE_CC_DB_PATH` | `daemon.db_path` |

```bash
# 用不同端口启动
BIBLE_CC_DAEMON_PORT=9780 uv run python scripts/daemon.py start
```

---

## 9. 故障排查

### 端口被占用

```bash
lsof -i :9777
# 换端口或 kill
BIBLE_CC_DAEMON_PORT=9780 uv run python scripts/daemon.py start
uv run python scripts/daemon.py stop --force
```

### BiBLE 连不上

```bash
curl -v http://<your-bible-url>/health
```

### uv sync 失败

```bash
uv --version      # >= 0.4.0
python3 --version # >= 3.10
```

### Daemon 进程残留

```bash
uv run python scripts/daemon.py stop --force
# 或
lsof -ti :9777 | xargs kill -9
```

---

## 10. 当前限制

Phase 0 只实现 1st Call + 反复重装测试。后续 Phase：

| 功能 | Phase |
|------|-------|
| Session 追踪 + Context 注入 | 1 |
| Key moment LLM 检测 | 2 |
| Moment 推送到 BiBLE Atlas | 3 |
| MCP 工具（模型搜索）| 4 |
| Slash commands | 5 |
