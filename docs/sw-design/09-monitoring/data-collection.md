# 09-monitoring/data-collection.md — 数据采集（L3）

> 指标定义、存储、push 数据格式、server dashboard 约定。

---

## 1. Token 指标

| 指标 | 含义 | 来源 |
|------|------|------|
| `detection_llm_token` | Phase 1+2 LLM 调用 token 消耗 | Anthropic API `usage` 字段（plugin 侧唯一可获取的 token 数据） |
| `injection_token_count` | SessionStart 注入块的估计 token | `/context/inject` 计算 |

> ⚠️ 模型本身的 token 消耗在 Anthropic API 侧，plugin 无法直接获取。

---

## 2. 性能指标

| 指标 | 含义 | 计算 |
|------|------|------|
| `api_latency_p50/p95/p99` | daemon HTTP API 延迟 | per-request `end - start` |
| `sqlite_query_time_avg` | SQLite 查询平均耗时 | 慢查询记录 |
| `bible_latency_ms` | BiBLE Atlas 延迟 | `/daemon/health` 的 `bible_connectivity.latency_ms` |

---

## 3. 健康指标

| 指标 | 含义 |
|------|------|
| `daemon_uptime_seconds` | 运行时长 |
| `crash_count` | 异常退出次数 |
| `flush_success_count` / `flush_fail_count` | flush 成功/失败 |
| `moment_total` | 检测 moment 总数 |

---

## 4. SQLite 存储

DDL 见 [`03-daemon/sqlite-schema.md`](../03-daemon/sqlite-schema.md) §2.5——不在此处重复定义。

每 session 结束时聚合写入。30 天保留窗口，由 `/bible-cc:gc` 清理。

---

## 5. Push 附加数据格式

flush 的 `moments.json` 中附带 monitoring section：

```json
{
  "moments": [...],
  "monitoring": {
    "session_id": "abc-123",
    "started_at": "...",
    "ended_at": "...",
    "token": {"detection_llm_total": 4500},
    "performance": {"api_latency_p50_ms": 12, "api_latency_p95_ms": 150},
    "health": {"uptime_seconds": 9000, "flush_success": 12, "flush_fail": 0, "moment_total": 8}
  }
}
```

---

## 6. Server Dashboard 约定（参考）

- **Token 面板**：per-session detection token 趋势
- **延迟面板**：daemon API p50/p95/p99 时序
- **健康面板**：uptime、crash、flush 成功率

具体 UI 由 server 端定义。plugin 只负责采集和上报。

---

## 7. 参考文档

- [`../09-monitoring.md`](../09-monitoring.md) — L2 总览
- [`../../05-capture/flush.md`](../05-capture/flush.md) — flush 序列化
- [`../../02-interfaces.md`](../02-interfaces.md) — BiBLE import API
