# 03-daemon/port-conflict.md — Port Conflict（L3）

> 端口冲突检测、错误通知、auto_fallback 逻辑。本文覆盖 daemon 无法绑定端口时的所有处理路径。

---

## 1. 场景

Daemon 启动时（`POST /daemon/start` → Step 6 uvicorn），如果配置的端口已被占用，uvicorn 会抛出 `OSError: [Errno 48] Address already in use`。

默认行为：daemon 启动失败 → SessionStart hook 检测 → 输出 error hint 通知用户。

可选行为：`daemon.port_auto_fallback = true` → 自动尝试 port+1。

---

## 2. 默认行为（无 fallback）

### 2.1 流程

```
1. uvicorn.run(app, host="127.0.0.1", port=config.daemon.port)
2. OSError → catch → daemon 启动失败
3. SessionStart hook:
     POST /daemon/start → connection refused
     → hook 脚本输出 error hint to stdout
4. Error hint 格式（见 08-operability/hint-system.md）:
     ⎿ ❌ bible-cc daemon cannot start on port {port}.
       Port is occupied by pid {pid} ({process_name}).
       Fix: free port {port}, or set daemon.port in ~/.bible-cc/config.json,
       or enable daemon.port_auto_fallback to auto-select.
5. SessionStart hook 返回 {continue: true} — 不阻止 Claude Code 启动
6. UserPromptSubmit/PostToolUse hooks 检测 daemon /health 不可达 → 静默跳过
```

### 2.2 检测占用进程

```python
import subprocess

def get_port_owner(port: int) -> tuple[int, str] | None:
    """macOS/Linux: lsof -ti :{port} + -c name"""
    try:
        pid = subprocess.check_output(
            ["lsof", "-ti", f":{port}"], text=True, timeout=2
        ).strip()
        if pid:
            name = subprocess.check_output(
                ["ps", "-p", pid, "-o", "comm="], text=True, timeout=2
            ).strip()
            return (int(pid), name)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
```

- `lsof -ti`：`-t` 仅输出 pid，`-i` 匹配网络端口。
- macOS 和 Linux 均可使用。Windows 不支持 `lsof`——待实现时补充 `netstat` 方案。
- 如果 `lsof` 不可用（极端环境），仅报告端口被占，不提供 pid。

### 2.3 错误通知的通道

SessionStart hook 的 stdout 设置了 `inject: true`（见 `02-interfaces.md` §4.3），因此 error hint 同时出现在：
- conversation transcript（用户可见）
- system prompt（模型可见，可向用户解释）

这意味着：即使用户不主动检查，也能在下一个对话 turn 中看到错误。

---

## 3. auto_fallback 行为

### 3.1 启用条件

`daemon.port_auto_fallback = true`（默认 `false`）。

### 3.2 流程

```python
def find_available_port(start_port: int, max_attempts: int = 10) -> int:
    """Find first free port starting from start_port, incrementing by 1."""
    import socket
    for offset in range(max_attempts):
        port = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise PortExhaustedError(start_port, start_port + max_attempts - 1)
```

- 使用 `socket.bind()` 探测，不依赖 uvicorn 抛异常。
- `SO_REUSEADDR` 避免 TIME_WAIT 残留端口误判。
- `max_attempts = 10`（最多尝试 port+0 到 port+9）。
- 如果所有端口被占：抛 `PortExhaustedError`，走默认失败路径（error hint）。

### 3.3 选到新端口后的行为

- Daemon 在选到的端口上启动。
- SessionStart hook 正常执行。
- **不自动更新 config.json**。实际端口仅在 daemon 内存中，下次启动时如果原端口空闲可能换回来。
- `/daemon/health` 的 response 中返回实际端口 `{pid, port, status}`。
- `/bible-cc:status` 显示实际端口。

### 3.4 动机与权衡

auto_fallback 解决"端口碰巧被占"的场景（如另一个工具使用了 9777），但不解决"另一个 bible-cc daemon 已在运行"的场景。如果 bible-cc daemon 已在运行，`POST /daemon/start` 的幂等检测会直接返回 running 状态，不会走到端口绑定步骤。

---

## 4. 端口范围

| 配置路径 | Default | Env Override | 有效范围 |
|---------|---------|-------------|---------|
| `daemon.port` | `9777` | `BIBLE_CC_DAEMON_PORT` | 1024–65535 |

非法值（`< 1024` 或 `> 65535`）在 config 加载时 silent fallback 到 9777（见 `04-config/schema.md` §2.2）。

端口 `9777` 的选择理由：非 IANA 注册、非知名端口、与常见工具无冲突。

---

## 5. 不带 auto_fallback 的 Probe

SessionStart hook 在调用 `POST /daemon/start` 之前不做端口探测——那是 daemon 的职责。Hook 脚本仅检测 `/daemon/start` 的返回状态/连接状态。分离关注点：

```
Hook:     "daemon 是否 running？"（调 /daemon/start，不管端口如何）
Daemon:   "我能绑定端口吗？"（启动失败时提供上下文信息）
```

---

## 6. 参考文档

- [`startup.md`](startup.md) — Step 2 port resolution，Step 6 uvicorn 启动
- [`http-api.md`](http-api.md) — `/daemon/start` 端点 spec
- [`../../02-interfaces.md`](../../02-interfaces.md) — SessionStart hook error 行为
- [`../../04-config/schema.md`](../../04-config/schema.md) — `daemon.port`, `daemon.port_auto_fallback`
- [`../08-operability/hint-system.md`](../08-operability/hint-system.md) — error hint 格式
- [`../08-operability/failure-paths.md`](../08-operability/failure-paths.md) — F2 端口冲突诊断路径
