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

> Session 结束时，daemon 已完成 Phase 2 retrospective detection。此刻 SQLite 中有一批 `flushed=0` 的 moments——它们被 Phase 1 和 Phase 2 检测到，但还没有推送到 BiBLE Atlas。
>
> Flush 流程：查询 unflushed moments → 与 session metadata + monitoring data 一起打包 → `POST /api/import/memory`（multipart 含 `files[]`、`kb_index`（来自 `config.bible.kb_index`）、`tag="memory"`）→ 获得 task_id → 更新每个 moment 的 `flushed=1, import_task_id=X, flushed_at=NOW`。
>
> 如果 BiBLE 不可达：moments 保持 `flushed=0`，`retry_count` 递增，日志输出失败原因。下次 push 或 session end 重试。
>
> Flush 也可手动触发（`POST /daemon/session/flush`）——不结束 session，只推送已累积的 moments。

### MCP 真实调用场景

> 当前 MCP server 的 6 个活跃 tool 全部返回 "BiBLE Atlas not yet connected (Phase 3)"。在 client.py 就绪后，将 `_handle_tool()` 改为真实调用 client.py 的对应方法。
>
> 保留 degradation 路径：
> - BiBLE 不可达（`BibleUnreachableError`）→ 返回 structured error（不 crash）
> - client.py import 失败 → 回退到 degradation error

---

## Feature 逐个讨论

### F3b.1 — Flush Logic

| 属性 | 说明 |
|------|------|
| **理由** | Moment 必须到达 BiBLE Atlas 才算完成采集链路。Flush 连接了本地 SQLite buffer 和 BiBLE Atlas 的 async import pipeline。 |
| **优先级** | P0 |
| **依赖** | client.py、buffer.py（moments table CRUD）、config.py（`bible.kb_index` 用于 import 请求） |

**流程**：
1. `buffer.py` 增加 `get_unflushed_moments(session_id: str) -> list[dict]`
2. `buffer.py` 增加 `mark_moments_flushed(moment_ids: list[int], task_id: str) -> None`
3. `buffer.py` 增加 `increment_retry_count(moment_ids: list[int]) -> None`
4. Daemon 新增 `_flush_session_moments(session_id: str) -> dict` 函数
5. 触发点：
   - `/session/end` 中 Phase 2 retrospective 之后自动调用
   - `mid_session_upload=true` 时 Phase 1 检测后立即 flush 单个 moment
   - `POST /daemon/session/flush` 手动触发

**Flush 诊断日志**：
```
[flush] session=abc123, moments=3, including monitoring data
[flush] bundling → payload_size=2.4KB
[flush] POST /api/import/memory... OK → task_id=xyz-456
[flush] updating 3 moments → flushed=1
[flush] DONE (total=1.8s)
```

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
| `bible_memory_save` | `client.import_memory(session_id, moments, metrics)` | messages+title+abstract → memory payload（序列化为 multipart `files[]`） |
| `bible_memory_get` | `client.download_memory_file(storage_path, tag="memory")` → `client.get_task_status(task_id)` → `client.get_download_artifact("memory", artifact_id)` | storage_path → storage_path（异步三步流程） |
| `bible_knowledge_search` | `client.search_knowledge_base(query, tag, top_k=None, ...)` | query → query, tag → tag, top_k → top_k |
| `bible_skill_search` | `client.search_skill(query, tag="skill", top_k=None, ...)` | query → query, tag → tag, top_k → top_k |
| `bible_skill_get` | `client.download_skill_file(storage_path, tag="skill")` → `client.get_task_status(task_id)` → `client.get_download_artifact("skill", artifact_id)` | storage_path → storage_path（异步三步流程） |

**Degradation 路径保留**：
- BiBLE 不可达（`BibleUnreachableError`）→ 返回 structured error：
  ```json
  {
    "error": "BiBLE Atlas unreachable.",
    "detail": "Connection to BiBLE Atlas failed. Your data is safe locally.",
    "suggestion": "Check /bible-cc:status for connectivity. Moments will be flushed when BiBLE recovers."
  }
  ```
- client.py import 失败 → 回退 degradation error

**MCP 调用追踪日志**（与 Phase 4 F4.6 对齐）：
```
[mcp:tool] bible_memory_search(query="PostgreSQL", top_k=5) → 5 hits (0.3s)
[mcp:tool] bible_memory_search(query="unknown") → 0 hits (0.2s)
[mcp:tool] ERROR: bible_memory_search → BiBLE unreachable (timeout 30s)
```

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
| **3rd** | F3b.3 session/end 集成 | 依赖 F3b.1。在 session/end 中调用 flush | — |

---

## 验收标准

### Flush 验收
- [ ] `buffer.py` 新增 3 个 flush CRUD 方法（get_unflushed、mark_flushed、increment_retry）
- [ ] `_flush_session_moments()` 完整链路：query → bundle → POST import → update
- [ ] `/session/end` 在 Phase 2 retrospective 之后自动调用 flush
- [ ] `POST /daemon/session/flush` 端点可用
- [ ] Flush 每步输出 stderr 诊断日志（bundle → import → update → DONE）
- [ ] BiBLE 不可达时 moments 保持 `flushed=0`，`retry_count` 递增
- [ ] `mid_session_upload=true` 时 Phase 1 检测后立即 flush

### MCP 验收
- [ ] 6 个活跃 tool 全部调用 `client.py` 对应方法
- [ ] BiBLE 不可达（`BibleUnreachableError`）→ 返回 structured error（不 crash）
- [ ] 每次 tool 调用输出 `[mcp:tool]` 追踪日志
- [ ] 2 个 postponed tool（delete、list）行为不变

---

## 产出文件

```
src/bible_cc_plugin/daemon/buffer.py     ← (修改: flush CRUD 方法)
src/bible_cc_plugin/daemon/server.py     ← (修改: flush 端点 + session/end 集成)
src/bible_cc_plugin/mcp/server.py        ← (修改: _handle_tool 替换为真实调用)
tests/unit/test_buffer.py                ← (修改: flush CRUD 测试)
tests/unit/test_mcp.py                   ← (修改: 真实调用测试)
```
