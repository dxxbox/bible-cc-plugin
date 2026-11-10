# 06-recall/consult.md — `/bible-cc:consult`（L3）

> 用户主动跨域搜索：query / LLM summarize → BiBLE V4 → 合并 → inject。

---

## 1. 触发

```
/bible-cc:consult "PostgreSQL auth方案"
/bible-cc:consult  （无 query → LLM 归纳）
```

---

## 2. 流程

```
1. 有 query? → Yes: 直接用; No: LLM 归纳对话生成 query（≤50 words, blocking, 5s 超时）
2. 并行调 BiBLE V4 三域 search（asyncio.gather(return_exceptions=True)）
3. 合并结果: 三域归一化为统一字段 → score 降序 → (domain, doc_id, section_id) 去重
   → 每域结果截断到 limit（limit 作为每域 top_k，不做合并后全局截断）
4. 构建 <relevant-memories> → 注入
5. 返回 {context, query_used, hits}；hits 为归一化字段
   {domain, doc_id, section_id, section_title, content, score}
```

**hit 归一化**（三域原生字段异构）：

| domain | doc_id | section_id | section_title | content |
|---|---|---|---|---|
| `memory` | `doc_id` 或 `id` | `""` | `title` | `content` → `overview` → `abstract` |
| `skill` | `doc_id` 或 `id` | `""` | `name` | `content` → `description` |
| `knowledge` | `doc_id` | `section_id` | `section_title` | `content` |

---

## 3. LLM Query Synthesis（无 query 时）

`detector.summarize_query(turns, config)` —— 同步函数，server 用 `asyncio.to_thread` 调用。

```
Model: detection.model, max_tokens=128, temperature=0.0
Timeout: 5.0s（client.with_options(timeout=5.0, max_retries=0)）
DETECTOR_TEST_MODE ∈ {1,true,yes} → 直接返回 "test search query"

Prompt:
"Recent conversation:
 [user] ...
 [assistant] ...

 Generate a concise search query (<=50 words) to find relevant
 memories, knowledge, and skills.
 Search query:"
```

- 只取最近 10 个 turns（正序）；每条截断 500 字符，总长 ≤4000。
- 工具 turn 以 `[Used tool: name]` 形式出现。
- 任何失败（超时 / 网络 / 空输出）→ 返回 `None`，绝不抛异常。
- **失败回退**：取全表最近一条非空 user turn 内容（截断 500 字符）作为 query。
- 无任何 turn → 不发起搜索，返回空 hits + 提示 context（HTTP 200）。

---

## 4. 并行搜索

```python
domains = ["memory", "knowledge", "skill"]
tag = config.search.default_knowledge_tag  # default "design"
tag_map = {"memory": "memory", "knowledge": tag, "skill": "skill"}
tasks = [
    _timed_search(domain, search(domain, tag_map[domain], query, top_k=limit,
                                  base_url, token))
    for domain in domains
]
results = await asyncio.gather(*tasks, return_exceptions=True)

# gather preserves order: zip() maps exceptions back to their domain.
for domain, result in zip(domains, results):
    if isinstance(result, BaseException):
        skipped.append(domain)  # includes per-domain 10s timeout
        continue
    items, elapsed_ms = result
    hits.extend(normalize(domain, items[:limit]))

# sort by score desc; dedup by (domain, doc_id, section_id)
unique = []
seen = set()
for h in sorted(hits, key=lambda x: x["score"], reverse=True):
    key = (h["domain"], h["doc_id"], h["section_id"])
    if key not in seen:
        seen.add(key)
        unique.append(h)
```

- 单域失败 → 跳过，其余域正常合并。
- 每个域搜索由 `asyncio.wait_for(..., timeout=10.0)` 限制（并行执行）；总最坏 ≈ LLM 5s + 10s = 15s。
- 所有域失败 → HTTP 200、`hits=[]`、context 内 "BiBLE Atlas unreachable" 提示。
- 不做 `search.default_min_score` 过滤（Phase 4 范围外，与 MCP 工具行为一致）。

---

## 5. 错误处理

| 场景 | 行为 |
|------|------|
| BiBLE 不可达（全部域） | HTTP 200、空 hits、context 含 "BiBLE Atlas unreachable" 提示 |
| 某个 domain 搜索失败 | 跳过该 domain，合并其余 |
| LLM query synthesis 失败 | fallback: 最近一条非空 user turn 内容作为 query |
| 空 query 且无任何 turn | HTTP 200、空 hits、context 含 "no conversation context" 提示 |
| domains 含非法值 | 400 `BAD_REQUEST` |
| session_id 缺失 | 422 `BAD_REQUEST` |

---

## 6. 日志

```
[consult] session=<id8> query="PostgreSQL migration" → direct search
[consult] session=<id8> LLM latency=1500ms
[consult] session=<id8> query="bible atlas deployment" → LLM summarization
[consult] session=<id8> LLM fallback → latest user turn
[consult] memory_search    → 5 hits (312ms)
[consult] knowledge_search → 2 hits (401ms)
[consult] skill_search failed: BibleUnreachableError(...)
[consult] merged: 7 hits, top_score=0.92
[consult] DONE (total=905ms)
```

- 每个域单独一行（成功带耗时与命中数；失败带异常）。
- 合并行记录去重后的总数与最高分。
- 全部域失败时额外 warning：`[consult] all domains failed: memory, knowledge, skill`。

---

## 7. 与 MCP 工具的关系

```
MCP tools: model 自动调用，单 domain，结果给模型
consult:   用户主动，三 domain 并行+合并，结果注入上下文
```

同一套 BiBLE V4 API，不同触发者和消费方式。

---

## 8. 参考文档

- [`../../02-interfaces.md`](../02-interfaces.md) — `/daemon/consult`、BiBLE V4 API
- [`../../04-config.md`](../04-config.md) — `search.default_top_k`、`search.default_knowledge_tag`
- [`mcp-tools.md`](mcp-tools.md) — MCP 工具 schema
