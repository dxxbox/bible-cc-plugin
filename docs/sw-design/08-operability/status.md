# 08-operability/status.md — 诊断命令（L3）

> status / check-bible / context 三个诊断命令的实现逻辑、输出格式、边界条件。

---

## 1. `/bible-cc:status`

### 1.1 数据来源

```
GET /daemon/health
```

### 1.2 输出域

| 域 | 字段 | 含义 |
|----|------|------|
| daemon | status, uptime, pid, port | 进程状态 |
| sessions | active, completed | session 统计 |
| buffer | total_turns, pending_moments | 当前 session 缓冲数据 |
| bible | reachable, latency_ms, base_url | BiBLE Atlas 连通性 |
| sqlite | integrity, schema_version, size_bytes, db_path | 数据库健康 |

### 1.3 逻辑

```
1. 调 GET /daemon/health
2. 如果 daemon 无响应 → 展示 "daemon: not running"，提示检查端口
3. 格式化各域输出
4. bible_connectivity: 展示 daemon 启动时的连通性检查结果
```

### 1.4 输出示例

```
bible-cc v1.0.0

daemon
  status:     running (pid 12345, port 9777)
  uptime:     3h 22m

sessions
  active:     2
  completed:  15

buffer (current session)
  turns:      12
  pending moments: 3

bible atlas (http://localhost:5555)
  status:     reachable
  latency:    12ms

sqlite (~/.bible-cc/daemon.db)
  integrity:  ok
  schema:     v1
  size:       2.3 MB
```

---

## 2. `/bible-cc:check-bible`

### 2.1 数据来源

BiBLE Atlas 提供两个探测端点：
- `GET {base_url}/health` — 轻量心跳（无认证）
- `GET {base_url}/api/v1/system/status` — 系统状态（含组件详情，需认证）

`/bible-cc:check-bible` 默认使用 `GET /health`（参考 bible-hermes-plugin 的 `client.system_status()` 实现）。

### 2.2 输出

```
BiBLE Atlas: reachable (http://localhost:5555, 12ms)

BiBLE Atlas: UNREACHABLE (http://localhost:5555)
  → Connection refused. Check if BiBLE Atlas is running.
  → Current config: bible.base_url = http://localhost:5555
  → /bible-cc:config to change URL.
```

---

## 3. `/bible-cc:context`

### 3.1 数据来源

daemon 缓存最近一次 `/context/inject` 的结果（不触发新的注入）。

### 3.2 逻辑

展示缓存中最近一次 SessionStart 注入的内容摘要，不展示完整注入文本（太长）。如果缓存为空（daemon 刚启动尚无注入），展示 "no injection recorded yet"。

### 3.3 输出

```
last injection (SessionStart, 5 min ago)
  sources:  3 turns summary, 2 unflushed moments
  tokens:   ~350 / 1200 budget
  preview:  "Session started: implementing auth module. Decision: PostgreSQL..."

如果注入为空：
last injection: empty (new session, no local buffer data)
```

---

## 4. 参考文档

- [`../../02-interfaces.md`](../../02-interfaces.md) — `/daemon/health` 响应格式
- [`../08-operability.md`](../08-operability.md) — 全局约束
