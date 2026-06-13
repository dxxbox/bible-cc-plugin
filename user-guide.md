# bible-cc-plugin User Guide

> 适用于首次安装和测试。当前版本: **Phase 0 — 1st Call**（安装、配置、daemon 生命周期）。

---

## 1. 前置条件

| 条件 | 如何检查 | 如何安装 |
|------|---------|---------|
| Python 3.10+ | `python3 --version` | https://www.python.org/downloads/ |
| `uv` | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Claude Code | 已安装即可 | — |
| BiBLE Atlas（可选）| 有可访问的 URL 即可 | Phase 0 不需要，但 setup wizard 会测试连通性 |

---

## 2. 安装

```bash
cd ~/.claude/plugins/
git clone <repo-url> bible-cc-plugin
cd bible-cc-plugin
uv sync
```

---

## 3. 首次配置

```bash
uv run python -m bible_cc_plugin.scripts.setup
```

交互式流程：
```
=== bible-cc-plugin Setup ===

BiBLE Atlas URL [http://localhost:5555]:   ← 输入地址，回车用默认
Token (optional, press Enter to skip):     ← 需要认证时输入

Config written to /Users/xxx/.bible-cc/config.json

Testing BiBLE connectivity... OK (15ms)

Setup complete.
```

Setup wizard 是幂等的——重复运行不会覆盖已有配置：
```bash
uv run python -m bible_cc_plugin.scripts.setup
# → Config already exists at ~/.bible-cc/config.json
# → To reconfigure, delete this file and re-run setup.
```

BiBLE 不可达时安装仍成功：
```
Testing BiBLE connectivity... UNREACHABLE (Connection refused)
  Daemon will start but BiBLE features will be unavailable.
```

查看详细错误：`--debug` flag。

---

## 4. Daemon 生命周期

Daemon 是后台 HTTP 服务（FastAPI），监听 `127.0.0.1:9777`。Claude Code SessionStart hook 会自动启动它。

### 启动

```bash
uv run python -m bible_cc_plugin.scripts.daemon start
# → [daemon] Starting on 127.0.0.1:9777... OK (pid=12345, port=9777)
```

幂等——已运行时不会重复启动：
```bash
uv run python -m bible_cc_plugin.scripts.daemon start
# → Daemon already running (pid=12345, port=9777)
```

### 状态

```bash
uv run python -m bible_cc_plugin.scripts.daemon status
# → Daemon: running / not running
```

### 停止 / 重启

```bash
uv run python -m bible_cc_plugin.scripts.daemon stop
uv run python -m bible_cc_plugin.scripts.daemon restart
```

### Debug 模式

```bash
uv run python -m bible_cc_plugin.scripts.daemon start --debug
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

## 6. 开发工具

```bash
./scripts/dev.sh test       # 全部测试
./scripts/dev.sh lint       # Lint + 格式化检查
./scripts/dev.sh ci         # 完整 CI（lint → unit → contract）
```

---

## 7. 配置

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
BIBLE_CC_DAEMON_PORT=9780 uv run python -m bible_cc_plugin.scripts.daemon start
```

---

## 8. 故障排查

### 端口被占用

```bash
lsof -i :9777                    # 查看占用者
BIBLE_CC_DAEMON_PORT=9780 uv run python -m bible_cc_plugin.scripts.daemon start  # 换端口
```

### BiBLE 连不上

```bash
curl -v http://<your-bible-url>/health   # 手动测试
```

### uv sync 失败

```bash
uv --version      # 确认 >= 0.4.0
python3 --version # 确认 >= 3.10
```

---

## 9. 卸载

```bash
uv run python -m bible_cc_plugin.scripts.daemon stop
rm -rf ~/.bible-cc/
rm -rf ~/.claude/plugins/bible-cc-plugin
```

---

## 10. 当前限制

Phase 0 只实现 1st Call。后续 Phase 将逐步添加：

| 功能 | Phase |
|------|-------|
| Session 追踪 + Context 注入 | 1 |
| Key moment LLM 检测 | 2 |
| Moment 推送到 BiBLE Atlas | 3 |
| MCP 工具（模型搜索）| 4 |
| Slash commands | 5 |
