# 05-capture/flush.md — Flush to BiBLE Atlas（L3）

> moment 推送到 BiBLE Atlas 的完整设计：序列化、import API、mid_session_upload、retry、push-all。
>
> **⚠️ Phase 阶段**: 本文描述最终态完整设计。Phase 3b 只实现 submit（§1-2, §5），
> 状态机轮询（§3.2-3.3, flushed=2/-1）延后到 Phase 3d（F3d.4）。
> 实现计划以 `docs/backlogs/Phase-3/2026-06-24-phase-3b-flush-and-mcp.md` 为准。

---

## 1. Flush 触发路径

| 路径 | 触发 | 范围 |
|------|------|------|
| mid_session_upload | Phase 1 检测到 moment 后立即 flush | 单 moment |
| `/session/end` | Stop hook → Phase 2 完成 → flush | 当前 session 所有 unflushed moments |
| `/bible-cc:push` | 用户手动命令 | 当前 session 所有 unflushed moments |
| `/bible-cc:push-all` | 用户手动命令 | 所有 session 的 pending moments |
| `/bible-cc:retry-push` | 用户手动命令 | 上次失败的 flush 重试 |

---

## 2. 序列化

moments 通过 multipart/form-data 上传到 BiBLE memory import API。

**BiBLE 契约**（`bible/api/upload/memory_upload_api.py` + `memory_parser/`）：

- multipart 字段名为 **`files`**（不是 `files[]`）。
- 必须包含**恰好一个** `meta.json`：必填 `memory_id` / `title` / `abstract`；
  `title ≤ 200`、`abstract ≤ 500`、`overview ≤ 2000`。
- 其余文件作为附件持久化；BiBLE 的检索 chunk **只来自** `abstract` + `overview`。

**Flush 映射（每 session 一个 memory 条目）：**

| meta.json 字段 | 来源 |
|---|---|
| `memory_id` | `bible-cc-{session_id}`（单 moment flush：`bible-cc-{session_id}-m{moment_id}`） |
| `title` | `Claude Code session {sid[:8]}: {首个 moment 标题}`（截断 200） |
| `abstract` | 所有 moment 的 `[{type}] {title}` 以 ` \| ` 连接（截断 500） |
| `overview` | 所有 moment 的 `[{type}] {title}: {narrative}` 换行连接（截断 2000） |
| `created_at` | `sessions.created_at`（转 ISO-8601） |
| `updated_at` | flush 时间（ISO-8601 UTC） |
| `source_client` | `"bible-cc"` |
| `language` | `"zh"` |

附件：`moments-{sid[:8]}-{ts}.json`（完整 moments 数组 + session_id + metrics），
不参与检索，仅供回溯。

```python
files = [
    ("meta.json", meta_bytes, "application/json"),
    (f"moments-{session_id[:8]}-{ts}.json", moments_bytes, "application/json"),
]
```

- `kb_index` 来自 `bible.kb_index`（默认 `"bible-cc"`），`tag` 固定 `"memory"`。
- 失败时 moments 保持 `flushed=0`，`retry_count` 递增（见 §3.4）。

---

## 3. Flush 状态管理

### 3.1 moments 表字段

以下字段纳入 `03-daemon/sqlite-schema.md` 的 moments 表定义：

```sql
-- flush tracking (from initial schema, not migration)
import_task_id TEXT,              -- BiBLE async task id
flushed_at TEXT,                  -- flush timestamp
retry_count INTEGER DEFAULT 0     -- consecutive failure count
```

### 3.2 状态流转

```
flushed = 0  → pending
flushed = 1  → sent to BiBLE (task_id recorded)
flushed = 2  → BiBLE confirmed completed
flushed = -1 → flush failed (retry needed)
```

### 3.3 结果轮询

`_poll_flush_results(session_id)`：对 `flushed=1` 的 moments 按 `import_task_id`
查询 BiBLE task 状态（间隔 5s，最多 60 次，5 分钟总超时）。

- `completed` → `flushed=2`（`mark_moments_confirmed`）
- `failed` / `cancelled` → `flushed=-1`（`mark_moments_failed`，下次 flush 重试）
- 轮询超时 → `flushed=-1` + warning 日志

触发点：

- `/session/end` flush 成功后**后台调度**（`_schedule_poll`，不阻塞 hook 的 30s 预算）
- `POST /daemon/session/poll` 单次检查（`max_attempts=1`，不改状态，用于诊断）

`_poll_flush_results` 返回 `{session_id, confirmed, failed, pending, checked_tasks}`。

---

## 4. 错误处理

| 场景 | 行为 |
|------|------|
| BiBLE 不可达 | flushed 保持 0。retry-push 手动重试。 |
| Import task failed | flushed = -1。log error。 |
| 轮询超时 | flushed = -1。log warning。 |
| 连续失败 3 次 | warning 日志（CLI hint 待命令层实现，见 `08-operability/failure-paths.md` §F6） |

---

## 5. mid_session_upload

```
Phase 1 detects moment → dedup → INSERT (flushed=0) → if mid_session_upload: immediately flush (flushed=1)
```

---

## 6. Push All

`/bible-cc:push-all`：`SELECT * FROM moments WHERE flushed IN (0, -1)` → 按 session 分组 → 逐 session flush。

---

## 7. 参考文档

- [`../../02-interfaces.md`](../02-interfaces.md) — BiBLE import API、flush 序列化设计待定注
- [`../../04-config.md`](../04-config.md) — `bible.kb_index`, `capture.mid_session_upload`
- [`detection.md`](detection.md) — Phase 1/2 检测到 moment 后的 flush 调用
- [`../08-operability/failure-paths.md`](../08-operability/failure-paths.md) — F6 flush 失败恢复
