# 05-capture/flush.md — Flush to BiBLE Atlas（L3）

> moment 推送到 BiBLE Atlas 的完整设计：序列化、import API、mid_session_upload、retry、push-all。

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

moments（结构化 JSON）通过 multipart/form-data 上传到 BiBLE import API。

```python
import json, tempfile, os, httpx

def flush_moments(moments: list[dict], kb_index: str, base_url: str, token: str | None) -> str:
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump({"moments": moments, "flushed_at": datetime.now().isoformat()}, f)
        tmp_path = f.name

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with open(tmp_path, "rb") as f:
        files = {"files[]": ("moments.json", f, "application/json")}
        data = {"kb_index": kb_index, "tag": "memory"}
        response = httpx.post(
            f"{base_url}/api/import/memory",
            files=files, data=data, headers=headers, timeout=30
        )
    os.unlink(tmp_path)
    return response.json()["task_id"]
```

- `kb_index` 来自 `bible.kb_index`（默认 `"bible-cc"`），`tag` 固定 `"memory"`。
- 临时文件上传后立即删除。
- ⚠️ 文件格式需与 BiBLE parse_memory.py 的解析逻辑匹配，实现时确认。

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

异步轮询 BiBLE task status（间隔 5s，最多 60 次，5 分钟总超时）。

---

## 4. 错误处理

| 场景 | 行为 |
|------|------|
| BiBLE 不可达 | flushed 保持 0。retry-push 手动重试。 |
| Import task failed | flushed = -1。log error。 |
| 轮询超时 | flushed = -1。log warning。 |
| 连续失败 3 次 | 产出 warning hint（见 `08-operability/failure-paths.md` §F6） |

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

- [`../../02-interfaces.md`](../../02-interfaces.md) — BiBLE import API、flush 序列化设计待定注
- [`../../04-config.md`](../../04-config.md) — `bible.kb_index`, `capture.mid_session_upload`
- [`detection.md`](detection.md) — Phase 1/2 检测到 moment 后的 flush 调用
- [`../08-operability/failure-paths.md`](../08-operability/failure-paths.md) — F6 flush 失败恢复
