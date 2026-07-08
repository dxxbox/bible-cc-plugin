# Phase 3b: Flush Logic + MCP 真实调用

> **依赖**: Phase 3a（client.py）
> **被依赖**: Phase 3c（connectivity 需要 flush 链路就绪才能测 degradation）
> **父文档**: [Phase 3 总览](../plans/2026-06-13-phase-3-bible-integration.md)

**交付 Command**: 无新 command。`/bible-cc:push` 在 Phase 5 实现。Phase 3b 只交付 `/daemon/session/flush` 端点供后续 command 调用。

**预估: 2 天**

### 测试标注

3b.1（flush logic）和 3b.2（MCP tools）默认 `[Unit] [Pre]`（mock client.py）。3b.3（session/end 集成）使用 FastAPI TestClient → `[Integration] [Post]`。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## Scenario

### Flush 场景

> `/session/end` 同步等待 Phase 2 retrospective 完成后，SQLite 中有一批 `flushed IN (0, -1)` 的 moments（Phase 1 + Phase 2 检测到的，及之前失败的）。Phase 3b 将它们提交到 BiBLE Atlas（**submit-only，不确认 async 完成**）。
>
> Flush 流程：`get_retryable_moments(flushed IN (0,-1))` → 按固定 schema 序列化为 `moments.json` → `client.import_memory(files, kb_index, tag="memory")` → `mark_moments_submitted(moment_ids, task_id)`（写入 `flushed=1`）。
> 空列表时 skip，不调用 client。
>
> 失败处理：`_flush_session_moments()` 内部 catch 异常 → `increment_retry_count(moment_ids)` → 返回 `FlushResult(moment_ids, 0, task_id=None, retryable=..., error=...)`。HTTP endpoint 始终返回 200（graceful degradation）。
>
> 手动触发 `POST /daemon/session/flush` — 不结束 session，不等待 Phase 2。

### MCP 真实调用场景

> 6 个活跃 tool 从 degradation stub 切换为真实 `BiBLEClient` 调用。`_handle_tool()` 改为 async function，每次 tool call `async with BiBLEClient(...)`。
>
> Degradation 路径保留：`BiBLEError`（4xx/INVALID_RESPONSE）/ `BibleUnreachableError` → 3 种文案的 structured error（不 crash）。下载 tool 实现 3-step async 流程（submit → poll 2s×30 → artifact）。

---

## Feature 逐个讨论

### F3b.1 — Flush Logic

| 属性 | 说明 |
|------|------|
| **理由** | Moment 必须到达 BiBLE Atlas 才算完成采集链路。Flush 连接了本地 SQLite buffer 和 BiBLE Atlas 的 async import pipeline。 |
| **优先级** | P0 |
| **依赖** | client.py、buffer.py（moments table CRUD）、config.py（`bible.kb_index` 用于 import 请求） |

**buffer.py 补齐**（现有 `get_unflushed_moments()` 保留给 review 显示）：

| 方法 | 状态 | 说明 |
|------|------|------|
| `get_unflushed_moments(conn, session_id)` | ✅ 已有 | `flushed=0`，review 显示用 |
| `get_retryable_moments(conn, session_id)` | **新增** | `flushed IN (0, -1)`，flush/retry 路径 |
| `mark_moments_submitted(conn, moment_ids, task_id)` | **新增** | 写入 `flushed=1` + `import_task_id` + `flushed_at` |
| `increment_retry_count(conn, moment_ids)` | **新增** | flush 失败时递增 `retry_count` |

Phase 3d 预留：`mark_moments_confirmed(conn, moment_ids)`（`flushed=2`）、`mark_moments_failed(conn, moment_ids, reason)`（`flushed=-1`）。
4. Daemon 新增 `_flush_session_moments(session_id: str) -> FlushResult` 函数
   - 返回 `FlushResult` dataclass（`moment_ids: list[int]`, `flushed_count: int`, `task_id: str | None`, `retryable: bool`, `error: str | None`）
   - **Per-session `asyncio.Lock`** 防并发重复提交
   - **Guard clause**：空列表 → `FlushResult(moment_ids=[], flushed_count=0, task_id=None, retryable=False, error=None)`
   - **失败处理**：所有异常都 `increment_retry_count()`，moment 保持 `flushed=0`。
     - `FlushResult.retryable` 是**纯诊断字段**，不改变 flush 行为——moment 下次仍会被 `get_retryable_moments()` 捞取并重试
     - `retryable=False` 语义："此错误不太可能因重试自愈"——hint 系统可对用户显示不同提示（如"请检查 BiBLE 配置"），但不阻止自动/手动 retry
     - `BibleUnreachableError` → `retryable=True`
     - `BiBLEError`（4xx）→ `retryable=False`（提示：配置问题，重试大概率仍失败）
     - `BiBLEError`（INVALID_RESPONSE）→ `retryable=True`
   - HTTP endpoint 不负责查询/递增 moment_ids——直接从 `FlushResult` 读取
5. 触发点：
   - `/session/end` 中 Phase 2 retrospective 之后 → `_flush_session_moments(session_id)`（批量）
   - `POST /daemon/session/flush` 手动触发 → `_flush_session_moments(session_id)`（批量）
   - `mid_session_upload=true` 时 Phase 1 检测到 moment → `_flush_single_moment(conn, moment_id)`（**单个**，不影响其他 pending moments）
6. 新增 `_flush_single_moment(conn, moment_id: int) -> FlushResult`：
   - 从 moment 获取 `session_id`，按同一把 `_flush_locks[session_id]` 加锁——**与批量 flush 共享锁**，防止并发重复提交
   - 仅 flush **一个 moment**（从 moments 表读该 moment 的完整字段）
   - 使用与批量 flush 相同的 payload schema（`moments` 数组只含 1 个条目）
   - 失败时 `increment_retry_count([moment_id])`
   - 解决 `mid_session_upload` 的粒度问题——不会意外提交其他 pending moments

**Flush Payload Schema**（`moments.json` 通过 `files[]` multipart 上传）：

```json
{
  "moments": [
    {
      "id": 123,
      "moment_type": "decision",
      "title": "PostgreSQL for auth storage",
      "narrative": "Chose PostgreSQL over SQLite for concurrent writes and team expertise.",
      "content_hash": "a1b2c3...",
      "turn_range_start": 5,
      "turn_range_end": 8,
      "phase": "1",
      "detected_at": "2026-06-30T12:00:00"
    }
  ],
  "session_id": "abc-def-123",
  "flushed_at": "2026-06-30T12:05:00",
  "metrics": {
    "total_turns": 15,
    "total_chars": 4500
  }
}
```

| 字段 | 来源 | 说明 |
|------|------|------|
| `moments[]` | `get_retryable_moments()` 返回的 dict 列表 | 只包含 BiBLE parse_memory.py 关心的字段 |
| `moments[].id` | `moments.id` | 用于 client↔server 关联 |
| `moments[].content_hash` | `moments.content_hash` | content-hash dedup |
| `moments[].phase` | `moments.phase` | 区分 Phase 1/2 |
| `session_id` | buffer `sessions.session_id` | 关联 session |
| `flushed_at` | `datetime.now().isoformat()` | flush 时间戳 |
| `metrics.total_turns` | buffer `sessions.turn_count` | 当前 session 总 turns |
| `metrics.total_chars` | buffer `sessions.buffered_chars` | 当前 session 已缓冲 chars |

Multipart 结构：
- `files[]`: `("moments-{session_id[:8]}-{ts}.json", <JSON bytes>, "application/json")`
- `kb_index`: string form field（来自 `bible.kb_index`）
- `tag`: `"memory"`（固定）

**测试要求**：单测必须断言 multipart 请求中 `files[]` 的 filename/content/type，以及 form data 中的 `kb_index`/`tag`。

**Flush 诊断日志**：
```
[flush] session=abc123, moments=3, including monitoring data
[flush] bundling → payload_size=2.4KB
[flush] POST /api/import/memory... OK → task_id=xyz-456
[flush] updating 3 moments → flushed=1
[flush] DONE (total=1.8s)
```

**⚠️ Phase 3b 状态机边界**：与 `05-capture/flush.md` 定义的完整 4 状态机不同，**Phase 3b 只实现 submit（flushed 0→1）**。

| 状态 | 含义 | Phase 3b | Phase 3d |
|------|------|----------|----------|
| `flushed=0` | pending，未发送 | ✅ | — |
| `flushed=1` | sent to BiBLE，task_id 已记录 | ✅ | — |
| `flushed=2` | BiBLE 确认 task completed | — | ✅ async poll |
| `flushed=-1` | task failed / poll timeout | — | ✅ |

延后内容（Phase 3d）：
- `get_task_status()` 轮询（每 5s，最多 60 次）
- `flushed → 2` 确认完成
- `flushed → -1` 失败标记
- poll timeout 处理
- import task failed 处理

Phase 3b 验收标准中 `flushed=1` = "已提交到 BiBLE，不确认异步完成"。

### F3b.2 — MCP Tools 真实调用

| 属性 | 说明 |
|------|------|
| **理由** | 用户选择在 Phase 3 就激活 MCP tools——不等 Phase 4。client.py 就绪后，MCP server 的 6 个 tool 可以立即从 degradation stub 切换为真实 BiBLE 调用。 |
| **优先级** | P0 |
| **依赖** | client.py、mcp/server.py（现有骨架） |

**Tool → Client 映射**：

| MCP Tool | Client 方法 | 参数映射 |
|----------|------------|---------|
| `bible_memory_search` | `client.search_memory(query, tag="memory", top_k=None, ...)` | query → query, tag → tag, top_k → top_k |
| `bible_memory_save` | `client.import_memory(files, kb_index, tag="memory")` | MCP adapter 负责将 messages+title+abstract 序列化为 JSON bytes → `files` 参数。**guard clause**：`messages` 为空时直接返回 error，不调用 client（Phase 3a 禁止 empty files） |
| `bible_memory_get` | `client.request_memory_download(storage_path, tag="memory")` → `client.get_task_status(task_id)` → `client.get_download_artifact("memory", artifact_id)` | storage_path → storage_path（异步三步流程） |
| `bible_knowledge_search` | `client.search_knowledge_base(query, tag, top_k=None, ...)` | query → query, tag → tag（⚠️ tag 必填，无默认值）, top_k → top_k |
| `bible_skill_search` | `client.search_skill(query, tag="skill", top_k=None, ...)` | query → query, tag → tag, top_k → top_k |
| `bible_skill_get` | `client.request_skill_download(storage_path, tag="skill")` → `client.get_task_status(task_id)` → `client.get_download_artifact("skill", artifact_id)` | storage_path → storage_path（异步三步流程） |

**Async Adapter**：

`_handle_tool()` 改为 **async function**，FastMCP 原生支持 async tool handler。不需要 `asyncio.run()`。

```python
# tool registration（main() 中）:
def make_handler(n: str):
    async def handler(**kwargs):            # ← async
        return await _handle_tool(n, kwargs) # ← await
    return handler

# tool dispatch:
async def _handle_tool(name: str, arguments: dict) -> list:
    base_url = os.getenv("BIBLE_ATLAS_BASE_URL", "")
    token = os.getenv("BIBLE_ATLAS_TOKEN", "")
    kb_index = os.getenv("BIBLE_CC_KB_INDEX", "bible-cc")
    config = BibleConfig(base_url=base_url, token=token, kb_index=kb_index)
    
    try:
        async with BiBLEClient(config) as client:
            return await _dispatch(client, name, arguments)
    except BiBLEError as exc:
        return _degradation_error(name, exc)
    except BibleUnreachableError as exc:
        return _degradation_error(name, exc)
```

- **无 `asyncio.run()` 风险**：async handler 直接运行在 FastMCP 的 event loop 内。
- 每次调用创建新 client（`async with`），用完自动关闭。
- `BIBLE_ATLAS_BASE_URL` / `BIBLE_ATLAS_TOKEN` 从 env 读取（`.mcp.json` 设置），token 空时 `BibleConfig(token="")` → `None`（兼容 test mode）。
- `kb_index`：使用 `BibleConfig` 默认值 `"bible-cc"`，或读取 `BIBLE_CC_KB_INDEX` env var（可选，`.mcp.json` 中可追加）。当前 `.mcp.json` 不声明此字段，MCP server 回退到 `"bible-cc"`。

**下载轮询策略**（`bible_memory_get` / `bible_skill_get`）：

```
1. client.request_memory_download(storage_path) → task_id
   → log: [mcp:tool] bible_memory_get submitted task_id=xxx, polling (60s timeout)
2. poll loop: get_task_status(task_id) every 2s, 30 max (60s timeout)
   → 每 5 次 poll (10s) log 进度: [mcp:tool] bible_memory_get polling task_id=xxx (10s/60s, status=queued)
3. status = completed → get_download_artifact("memory", artifact_id) → bytes
4. status = failed/cancelled → structured error with task_id
5. poll timeout (60s) → structured error with task_id:
   {"error": "Download timeout after 60s", "task_id": "xxx", "suggestion": "Check BiBLE /bible-cc:status or retry later"}
```

Artifact ID 位置：`task_record["result"]["artifact_id"]`（V4 API 响应嵌套结构）。

**Degradation 路径保留** — 所有 client 异常统一映射为 structured error，不 crash：

| 异常 | error 文案 | detail | suggestion |
|------|-----------|--------|------------|
| `BibleUnreachableError` | `"BiBLE Atlas unreachable"` | `"Connection to BiBLE Atlas failed: {exc}"` | `"Check /bible-cc:status for connectivity."` |
| `BiBLEError` (4xx) | `"BiBLE Atlas request error"` | `"[{code}] {message}"` | `"Check your BiBLE configuration (base_url, token, kb_index)."` |
| `BiBLEError` (INVALID_RESPONSE) | `"BiBLE Atlas protocol error"` | `"{message}"` | `"BiBLE returned an unexpected response. Check BiBLE server version compatibility."` |

```json
{
  "error": "BiBLE Atlas <category>.",
  "detail": "<specific cause>",
  "suggestion": "<actionable next step>"
}
```

- client.py import 失败 → 回退 degradation error

**MCP 调用追踪日志**（与 Phase 4 F4.6 对齐）：
```
[mcp:tool] bible_memory_search(query="PostgreSQL", top_k=5) → 5 hits (0.3s)
[mcp:tool] bible_memory_search(query="unknown") → 0 hits (0.2s)
[mcp:tool] ERROR: bible_memory_search → BiBLE unreachable (timeout 30s)
```

### F3b.3 — session/end 集成

| 属性 | 说明 |
|------|------|
| **理由** | 当前 `/session/end` 先 mark completed、后异步入队 Phase 2、立即返回。Phase 3b 必须修复这个时序才能让 flush 覆盖 Phase 2 moments。 |
| **优先级** | P0 |
| **依赖** | F3b.1（flush）、Phase 2 detection worker |

**时序要求**（对齐 `02-interfaces.md` §1.2）：

```
POST /session/end
  → 1. 入队 Phase 2 detection task 到 worker
  → 2. 同步等待 Phase 2 完成（session-level asyncio.Event）
  → 3. Phase 2 moments 已写入 SQLite
  → 4. _flush_session_moments(session_id) — 覆盖 Phase 1 + Phase 2
  → 5. mark_session_completed(session_id)
  → 6. return {moments_flushed: N, status: "completed"}
```

**关键变更**（与当前实现对比）：

| 当前 | Phase 3b 要求 |
|------|-------------|
| `mark_completed()` 先于 Phase 2 入队 | mark completed 在 flush 之后 |
| Phase 2 异步入队，不等待结果 | 同步等待 Phase 2 完成（via `asyncio.Event`） |
| Phase 2 失败不阻塞返回 | Phase 2 失败 → log error，仍 flush Phase 1 moments |
| `moments_flushed: 0` 硬编码 | 返回真实 flush 数量 |

**同步等待机制**（per-session completion object）：

```python
@dataclass
class _Phase2Completion:
    event: asyncio.Event
    cancelled: bool = False          # set on timeout — worker skips write
    error: Exception | None = None
    completed_at: str | None = None

_phase2_events: dict[str, _Phase2Completion] = {}  # module-level
```

| 角色 | 行为 |
|------|------|
| `/session/end` | 创建 `_Phase2Completion(event=asyncio.Event())` → 存入 `_phase2_events[session_id]` → 入队 Phase 2 task |
| Worker（处理 phase=2） | `try: _process_detection_task()` → `except: comp.error = exc` → **`finally: comp.completed_at = now; comp.event.set()`** |
| `/session/end`（等待） | `await asyncio.wait_for(comp.event.wait(), timeout=20.0)` |
| Timeout | 设置 `comp.cancelled = True` → 日志 warn → flush Phase 1 moments → mark completed → 返回 |
| Timeout 后 worker 完成 | Worker 检查 `comp.cancelled` → **跳过写入**，日志 warn `"Phase 2 completed after timeout — moments discarded for {sid}"` |
| Phase 2 抛错 | 日志 `comp.error`，仍 flush Phase 1 moments（graceful degradation） |
| 清理 | `/session/end` 返回前 `_phase2_events.pop(session_id, None)` — 防内存泄漏 |

**关键约束**：event **必须在 `finally` 中 set**，确保即使 Phase 2 worker 异常也释放等待者。Completion object 在 session/end 返回后清理。

**SessionEnd hook timeout**：`hooks.json` SessionEnd 30s → 覆盖 Phase 2 LLM（~10-15s）+ flush（~3s）+ 余量。

**验收**：
- [ ] `/session/end` 同步等待 Phase 2 完成后才 flush
- [ ] `moments_flushed` 返回真实数量（非硬编码 0）
- [ ] `mark_session_completed()` 在 flush 之后
- [ ] Phase 2 失败时仍 flush Phase 1 moments（graceful degradation）
- [ ] Phase 2 timeout（~20s）后日志 warn，继续 flush
- [ ] `already_completed` 边缘情况行为不变

---

## 实现顺序

```
F3a (client.py) 完成
    │
    ├──► F3b.1 (flush logic) ──► F3b.3 (session/end 集成)
    │
    └──► F3b.2 (MCP 真实调用)
```

| 顺序 | Feature | 理由 | 可并行 |
|------|---------|------|--------|
| **1st** | F3b.1 flush logic | 依赖 client.py。buffer CRUD + flush 函数 | ✅ 可与 F3b.2 并行 |
| **2nd** | F3b.2 MCP 真实调用 | 依赖 client.py。替换 `_handle_tool` | ✅ 可与 F3b.1 并行 |
| **3rd** | F3b.3 session/end 集成 | 依赖 F3b.1 + Phase 2 worker 同步机制。需要在 flush 前等待 retrospective 完成 | — |

---

## 验收标准

### Flush 验收
- [ ] `buffer.py` **补齐** 3 个 flush CRUD 方法（`get_retryable_moments` 新增、`mark_moments_submitted` 新增、`increment_retry_count` 新增）。现有 `get_unflushed_moments` 不重复实现
- [ ] `get_retryable_moments()` 查询 `flushed IN (0, -1)`，覆盖 pending + 失败重试
- [ ] `_flush_session_moments()` 使用 `get_retryable_moments()` — 完整链路：query →（空列表则 skip）→ bundle → POST import → update (`flushed=1`)
- [ ] Flush payload 符合 schema：moments JSON 结构（id/moment_type/title/narrative/content_hash/turn_range/phase/detected_at）+ session_id + flushed_at + metrics
- [ ] Multipart 结构正确：`files[]` 的 filename（`moments-{sid[:8]}-{ts}.json`）/content（JSON bytes）/type（`application/json`），form fields `kb_index`/`tag`
- [ ] 单测断言 multipart 请求内容（filename、content type、JSON schema、form fields）
- [ ] `mark_moments_submitted()` 写入 `flushed=1` + `import_task_id` + `flushed_at`（**不确认 async 完成**）
- [ ] `/session/end` 在 Phase 2 retrospective 之后自动调用 flush
- [ ] `POST /daemon/session/flush` 端点可用
- [ ] Flush 每步输出诊断日志（bundle → import → update → DONE）
- [ ] BiBLE 不可达时 moments 保持 `flushed=0`，`retry_count` 递增
- [ ] `mid_session_upload=true` 时 Phase 1 检测后调用 `_flush_single_moment()`——只 flush 刚检测到的 moment，且与批量 flush 共享同一把 `_flush_locks[session_id]`
- [ ] `_flush_session_moments()` 含 `# TODO(Phase 3d): poll get_task_status() for flushed=2/-1 completion`
- [ ] **Flush idempotency**：同一批 moments flush 两次，第二次 `get_retryable_moments()` 返回空（因为已是 `flushed=1`），直接 skip
- [ ] **并发 flush 安全**：per-session `asyncio.Lock`（`_flush_locks[session_id]`），`async with lock` 包裹 flush。并发触发时仅第一次真正调用 `client.import_memory()`，第二次等锁后 skip。单测验证：3 个并发 flush → 只有 1 次 client 调用
- [ ] **No pending moments**：无 retryable moments 时不调用 `client.import_memory()`，返回 `0`
- [ ] **错误责任在 `_flush_session_moments()` 内部闭环**：
  - 返回 `FlushResult(moment_ids, flushed_count, task_id, retryable, error)` — 不抛异常
  - 失败时内部调用 `increment_retry_count(moment_ids)` 后再返回 error 结果
  - HTTP endpoint 直接从 `FlushResult` 读取所有字段，不需要自己查询 moment_ids
- [ ] **Import returns INVALID_RESPONSE**（missing task_id）→ `FlushResult(moment_ids, 0, task_id=None, retryable=True, error="INVALID_RESPONSE")` → endpoint 返回 `{moments_flushed: 0, task_id: null, flush_error: "..."}`
- [ ] **BiBLE 4xx（配置错误）** → `retryable=False`（纯诊断提示——不阻止自动 retry，但 hint 系统可提示用户检查配置）
- [ ] **BiBLE unreachable** → `retryable=True`，下次 push/session end 自动重试
- [ ] **INVALID_RESPONSE** → `retryable=True`（临时异常，可能自愈）
- [ ] Endpoint 返回 `task_id` 字段（成功时指向 BiBLE async import task，失败时为 null），用户可通过 `/bible-cc:status` 追踪
- [ ] Flush 失败 **不改变 HTTP status**（始终 200），不 crash daemon。Moments 保持 `flushed=0`

### Session/End 集成验收
- [ ] `/session/end` 同步等待 Phase 2 完成后才 flush
- [ ] 同步机制：per-session `_Phase2Completion`（event + error + completed_at），存入 module-level dict
- [ ] Worker **在 `finally` 中 set event**（Phase 2 异常不阻塞 session/end）
- [ ] Phase 2 抛错 → 记录 `comp.error` → `/session/end` 日志 error → 仍 flush Phase 1 moments
- [ ] Phase 2 await timeout（20s）→ 设置 `comp.cancelled = True` → 日志 warn → 仍 flush Phase 1 moments
- [ ] Timeout 后 worker 完成 → 检查 `comp.cancelled` → **跳过写入** "Phase 2 completed after timeout — moments discarded"
- [ ] `/session/end` 返回前 `_phase2_events.pop(session_id)` — 清理 completion object，防内存泄漏
- [ ] `moments_flushed` 返回真实数量（非硬编码 0）
- [ ] `mark_session_completed()` 在 flush 之后
- [ ] `already_completed` 边缘情况行为不变

### MCP 验收
- [ ] 6 个活跃 tool 全部调用 `client.py` 对应方法（via **async handler**，无 `asyncio.run()`）
- [ ] 每次 tool call 创建新 `BiBLEClient`（`async with`），用完自动关闭
- [ ] `bible_memory_get` / `bible_skill_get` 实现 3-step 异步流程（submit → poll 2s×30 → artifact）
- [ ] Submit 后立即 log `task_id` + "polling (60s timeout)"；每 5 次 poll（10s）log 进度
- [ ] 下载 poll timeout（60s）/ task failed → structured error **包含 `task_id`**，方便后续排查
- [ ] BiBLE 不可达（`BibleUnreachableError`）→ 返回 structured error（不 crash）
- [ ] `BiBLEError`（4xx / INVALID_RESPONSE）→ 返回 structured error with detail
- [ ] 每次 tool 调用输出 `[mcp:tool]` 追踪日志
- [ ] 2 个 postponed tool（delete、list）行为不变
- [ ] `BIBLE_ATLAS_BASE_URL` 未设置时返回配置指导（同当前行为）
- [ ] **MCP postponed tools unchanged**：`bible_memory_delete` / `bible_knowledge_list` 仍返回 "not yet available"（不受 Phase 3b 影响）
- [ ] **Client lifecycle**：每次 call `async with` 正确关闭，无异步 client 泄漏。单测验证 `aclose()` 被调用
- [ ] **Async handler 正确 await**：单测调用 handler 后断言返回值是 `list[TextContent]`（已解析的 JSON text），非 coroutine 对象。验证 FastMCP 正确 await 了 async handler

---

## 产出文件

```
src/bible_cc_plugin/daemon/buffer.py     ← (修改: flush CRUD 方法)
src/bible_cc_plugin/daemon/server.py     ← (修改: flush 端点 + session/end 集成)
src/bible_cc_plugin/mcp/server.py        ← (修改: _handle_tool 替换为真实调用)
tests/unit/test_buffer.py                ← (修改: flush CRUD 测试)
tests/unit/test_mcp.py                   ← (修改: 真实调用测试)
```
