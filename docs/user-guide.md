# bible-cc-plugin User Guide

> 当前版本: **Phase 0 — 1st Call**。所有操作通过 `./bible-cc` 一键脚本完成。

---

## 速查

```
./bible-cc install      首次安装
./bible-cc start        启动 daemon
./bible-cc stop         停止 daemon
./bible-cc restart      重启 daemon
./bible-cc status       查看状态
./bible-cc verify       验证一切正常
./bible-cc reinstall    完整重装循环
./bible-cc reset        重置配置 + 重启
./bible-cc uninstall    彻底清理
./bible-cc ci           CI 流水线
./bible-cc help         完整菜单
```

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
./bible-cc install
```

`install` 等价于 `uv sync` + `python scripts/setup.py --non-interactive`。

---

## 3. 配置

所有配置通过 `./bible-cc setup` 完成。

### 交互式

```bash
./bible-cc setup
```

### 自动化

```bash
./bible-cc setup --non-interactive
./bible-cc setup --non-interactive --base-url http://bible:5555 --token sk-ant-xxx
```

### 重置

```bash
./bible-cc setup --reset               # 交互式
./bible-cc setup --reset --non-interactive  # 自动化
```

### 调试

```bash
./bible-cc setup --debug
```

> 底层命令: `uv run python scripts/setup.py <args>`

---

## 4. Daemon 生命周期

```bash
./bible-cc start          # 启动（幂等）
./bible-cc start --debug  # 启动 + stderr 全量日志
./bible-cc status         # 查看状态
./bible-cc stop           # 停止（强制，无响应时 kill -9）
./bible-cc restart        # 重启
```

> 底层命令: `uv run python scripts/daemon.py <action>`

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

```bash
./bible-cc verify       # 16 checks: env → config → daemon → code → tests
./bible-cc reinstall    # 完整循环: uninstall → install → start → verify
./bible-cc reset        # 重置配置 + 重启 daemon
./bible-cc uninstall    # 彻底清理
```

典型测试循环：
```bash
./bible-cc reinstall    # 全自动重装验证
# ... 测试 ...
./bible-cc reset        # 重置，重来
# ... 再测 ...
```

---

## 7. 开发工具

```bash
./bible-cc test     # 全部测试
./bible-cc lint     # lint + format check
./bible-cc ci       # 完整 CI（lint → unit → contract）
./bible-cc format   # 自动格式化 + fix
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
BIBLE_CC_DAEMON_PORT=9780 ./bible-cc start
```

---

## 9. 故障排查

### 端口被占用

```bash
lsof -i :9777                           # 查看占用者
BIBLE_CC_DAEMON_PORT=9780 ./bible-cc start  # 换端口
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
./bible-cc stop          # 已包含 --force
lsof -ti :9777 | xargs kill -9  # 兜底
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
