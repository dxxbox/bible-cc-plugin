# 09 — Monitoring

> L2 | 领域总览 | daemon 端数据采集：指标定义、存储、上报策略。数据随 push 发送到 BiBLE Atlas，由 server dashboard 展示。

---

## 1. 定位

Monitoring 是 daemon 后台功能——在正常操作中采集 token 用量和性能数据，存入本地 SQLite，flush 时随 moments 一起推送到 BiBLE Atlas。不是面向用户的命令。

---

## 2. 全局约束

1. **后台采集无感知**：不阻塞任何用户操作。
2. **随 push 上报**：不单独建立上报通道。token/perf 数据作为 push payload 的附加 section。
3. **本地保留 30 天**：过期数据由 `/bible-cc:gc` 清理。
4. **server 侧展示**：plugin 不实现 dashboard。BiBLE Server 消费数据生成图表。
5. **数据采集不占命令**：`/bible-cc:token-usage` 是唯一的本地轻量查看命令，完整展示在 server 侧。

---

## 3. 采集指标

详见 `09-monitoring/data-collection.md`。

| 类别 | 指标 | 频率 |
|------|------|------|
| Token | session total、injection token、detection LLM token | per-session |
| Performance | daemon API p50/p95/p99、SQLite query time | per-request 采样 |
| Health | uptime、crash count、flush success/fail ratio | per-session |

---

## 4. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `09-monitoring/data-collection.md` | 指标定义、存储 schema、push 数据格式、server dashboard 约定 | ✅ 完成 |

---

## 5. 参考文档

- [`05-capture/flush.md`](05-capture/flush.md) — flush 时附带监控数据
- [`../command-priority-table.md`](../command-priority-table.md) — `token-usage` 命令
