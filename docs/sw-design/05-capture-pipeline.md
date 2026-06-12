# 05 — Capture Pipeline

> L2 | 领域总览 | 定义了采集管线的完整路径：hook → buffer → threshold → moment detection (Phase 1/2) → dedup → flush → BiBLE Atlas。各 L3 文件不得与本文约束冲突。

---

## 1. 定位

Capture pipeline 是 bible-cc-plugin 的数据流入链路。它把 Claude Code 的 hook 事件转化为持久化的 key moments。

```
hook → buffer (SQLite) → Phase 1 detection (async, mid-session) → dedup → flush → BiBLE Atlas
                       → Phase 2 detection (async, session end)   → dedup → flush → BiBLE Atlas
```

---

## 2. 全局约束

1. **Non-blocking**: `/turn/user` 和 `/turn/tool` 端点必须立即返回。检测是异步队列任务。
2. **完整 tool output**: PostToolUse 传入的 tool output 完整存入 turns 表。不做机械截断。LLM 在检测时提取 ≤250 char 精华。
3. **阈值触发**: `commit_threshold_turns`（默认 8）和 `commit_threshold_chars`（默认 16000）以先到达者为准触发 Phase 1 检测。
4. **两层去重**: Phase 2 prompt 注入已知 moments + content-hash UNIQUE 约束。详见 L3 detection.md。
5. **Flush 幂等**: 同一 moment 重复 flush 不产生副作用。BiBLE import 是异步的——返回 task_id，daemon 不等待完成。
6. **mid_session_upload=false 时不立即 flush**: Phase 1 检测到的 moments 积累在 SQLite（flushed=0），等 session end 时统一 flush。

---

## 3. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `05-capture/hook-flow.md` | hook → buffer 数据流：/turn/user, /turn/tool, tool output 摘要 | ✅ 完成 |
| `05-capture/detection.md` | Phase 1/2 检测：prompt 设计、阈值、两层去重、LLM 调用 | ✅ 完成 |
| `05-capture/flush.md` | flush → BiBLE import：序列化、mid_session_upload、retry、push-all | ✅ 完成 |

---

## 4. 参考文档

- [`01-architecture-overview.md`](01-architecture-overview.md) — 采集链路 + 结束链路数据流
- [`02-interfaces.md`](02-interfaces.md) — `/turn/user`, `/turn/tool`, `/session/end`, BiBLE import API
- [`03-daemon.md`](03-daemon.md) — Phase 1/2 阈值、去重约束、SQLite schema
- [`04-config.md`](04-config.md) — capture config 域
- [`../../CLAUDE.md`](../../CLAUDE.md) — Moment Detection Design、Dedup Strategy
