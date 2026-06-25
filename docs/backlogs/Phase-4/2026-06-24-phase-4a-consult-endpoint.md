# Phase 4a: Consult 端点（跨域搜索）

> **依赖**: Phase 3（client.py + flush + connectivity 全部就绪）
> **被依赖**: Phase 4c（consult 集成测试）
> **父文档**: [Phase 4 总览](../plans/2026-06-13-phase-4-recall-pipeline.md)

**交付 Command**: 无新 command。`/bible-cc:consult` 在 Phase 5 实现。Phase 4a 只交付 `/daemon/consult` 端点供后续 command 调用。

**预估: 1.5 天**

### 测试标注

4a.1（consult 核心）默认 `[Unit] [Pre]`（mock client.py 的 search 方法）。4a.2（LLM 归纳）默认 `[Unit] [Pre]`（stub LLM）。4a.3（端点集成）使用 FastAPI TestClient → `[Integration] [Post]`。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## Scenario

> MCP tools 是模型调用的——模型决定何时搜索。但用户也需要主动搜索能力。`/daemon/consult` 是用户触发的跨域搜索端点。
>
> 两个创新点：
> 1. **Query 为空时 LLM 自动归纳对话**——用户不需要想 search term。LLM 读当前 session 的最近 turns → 总结关键主题 → 生成搜索 query
> 2. **并行搜三个域**（memory + knowledge + skill）——不是串行。三个搜索同时发出，结果合并后按 score 排序返回
>
> 用户说 "consult" 而不给 query → LLM 推断用户想找与当前对话相关的历史记忆和知识。用户给 query → 直接按 query 搜索。

---

## Feature 逐个讨论

### F4a.1 — Consult 核心端点

| 属性 | 说明 |
|------|------|
| **理由** | MCP 是模型调用的，但用户也需要手动搜索能力。Consult 补充了 pull model 的第二条路径——用户主动从 BiBLE Atlas 拉取跨 session 知识。 |
| **优先级** | P0 |
| **依赖** | client.py（search_memory、search_knowledge、search_skill）、buffer.py（读取 session turns） |

**端点**: `POST /daemon/consult`

**请求**:
```json
{
  "session_id": "abc123",
  "query": "PostgreSQL migration",
  "domains": ["memory", "knowledge", "skill"],
  "limit": 5
}
```
- `query`: 可选，为空时 LLM 归纳
- `domains`: 可选，默认全部三域
- `limit`: 可选，默认 5

**流程**:
1. 接收 query（可能为空）
2. Query 为空 → 从 buffer 读取最近 N turns → 调 LLM 归纳对话 → 生成 search query（见 F4a.2）
3. Query 非空 → 直接用
4. 并行调用 `client.search_memory()` + `client.search_knowledge()` + `client.search_skill()`
5. 合并结果，按 score 降序排列
6. 返回 `{query_used, hits: [{domain, id, title, snippet, score}]}`

**响应**:
```json
{
  "query_used": "PostgreSQL migration",
  "hits": [
    {"domain": "memory", "id": "mem-1", "title": "Postgres migration plan", "snippet": "...", "score": 0.92},
    {"domain": "knowledge", "id": "kb-3", "title": "Postgres best practices", "snippet": "...", "score": 0.85},
    {"domain": "skill", "id": "sk-2", "title": "Database migration script", "snippet": "...", "score": 0.78}
  ]
}
```

**并行搜索**: 使用 `concurrent.futures.ThreadPoolExecutor`（3 个 worker）并行发出三个 `client.search_*()` 调用。每个调用独立 timeout（默认 10s）。单个域搜索失败不影响其他域。

### F4a.2 — LLM 归纳对话生成 Query

| 属性 | 说明 |
|------|------|
| **理由** | 用户不想手动构造 search term——consult 应该"理解"当前对话上下文，自动生成有效的搜索 query。LLM 读最近 N turns → 提取关键主题 → 生成 1-2 句搜索 query。 |
| **优先级** | P0 |
| **依赖** | detector.py（Anthropic client wrapper）、buffer.py（session turns） |

**Prompt 设计**:
```
System: You are summarizing a conversation to generate a search query.
User: Here are the recent turns of a conversation. Extract 1-2 key topics
that would be useful to search for in a knowledge base. Output ONLY the
search query string, no explanation.

Recent conversation:
[recent turns text]

Search query:
```

**关键行为**:
- 只取最近 10 个 turns（避免 token 浪费）
- 使用 `DETECTOR_TEST_MODE` 时返回固定 query（"test search query"）
- LLM 调用失败 → fallback 到空 query → 返回错误提示用户手动输入 query
- 超时 5s（consult 不应等太久）

### F4a.3 — Consult 诊断日志

| 属性 | 说明 |
|------|------|
| **理由** | Consult 涉及 LLM 归纳和并行搜索——任何一个环节出问题都需要分解诊断。 |
| **优先级** | P0 |

**日志格式**:
```
[consult] query="PostgreSQL migration" → direct search
[consult] memory_search → 5 hits (0.3s)
[consult] knowledge_search → 2 hits (0.4s)
[consult] skill_search → 0 hits (0.2s)
[consult] merged: 7 hits, top_score=0.92
[consult] DONE (total=0.9s)

[consult] query="" → LLM summarization... query="bible atlas deployment"
[consult] LLM latency=1.5s, tokens=120
[consult] memory_search → 3 hits (0.3s)
[consult] knowledge_search → 1 hits (0.4s)
[consult] skill_search → 2 hits (0.2s)
[consult] merged: 6 hits, top_score=0.85
[consult] DONE (total=2.5s)
```

---

## 实现顺序

```
F4a.1 (consult 核心) ──► F4a.2 (LLM 归纳) ──► F4a.3 (诊断日志)
```

| 顺序 | Feature | 理由 |
|------|---------|------|
| **1st** | F4a.1 consult 核心 | 独立——只依赖 client.py 的 search 方法 + buffer。非空 query 路径 |
| **2nd** | F4a.2 LLM 归纳 | 依赖 F4a.1。空 query 时 LLM 生成搜索词 |
| **3rd** | F4a.3 诊断日志 | 依赖 F4a.1 + F4a.2。完整的 consult 追踪日志 |

---

## 验收标准

- [ ] `POST /daemon/consult` 非空 query → 三域并行搜索 → 合并结果按 score 排序
- [ ] `POST /daemon/consult` 空 query → LLM 归纳对话 → 生成 query → 搜索
- [ ] 单个域搜索失败不影响其他域（isolation）
- [ ] `domains` 参数可限制搜索范围（如只搜 memory）
- [ ] `limit` 参数控制每域返回数量
- [ ] LLM 归纳失败 → fallback 错误提示（不 crash）
- [ ] stderr 输出 consult 分解日志（每域耗时、合并结果）
- [ ] `DETECTOR_TEST_MODE` 时 LLM 归纳返回固定 query

---

## 产出文件

```
src/bible_cc_plugin/daemon/server.py     ← (修改: POST /daemon/consult 端点)
```
