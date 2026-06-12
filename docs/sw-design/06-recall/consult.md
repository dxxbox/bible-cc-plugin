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
1. 有 query? → Yes: 直接用; No: LLM 归纳对话生成 query（≤50 words, blocking）
2. 并行调 BiBLE V4 三域 search（asyncio.gather）
3. 合并结果: score 降序 → doc_id 去重 → 截断到 top_k
4. 构建 <relevant-memories> → 注入
5. 返回 {context, query_used, hits}
```

---

## 3. LLM Query Synthesis（无 query 时）

```
Model: detection.model, max_tokens=128, temperature=0.0

Prompt:
"Based on the following conversation, generate a concise search query
 (≤50 words) to find relevant memories, knowledge, and skills:
 {recent_turns_summary}
 Search query:"
```

LLM 调用是 synchronously blocking——command timeout 控制上限。

---

## 4. 并行搜索

```python
async def search_all_domains(query: str, base_url: str, top_k: int, token: str):
    domains = [("memory", "memory"), ("knowledge-base", "design"), ("skill", "skill")]
    # KNOWLEDGE_BASE 的 tag 是必填项。默认 "design"，可通过 config 覆盖。
    # 如果某个 domain 没有 tag 可用 → 跳过该 domain
    tasks = [search(domain, tag, query, top_k, base_url, token) for domain, tag in domains]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    hits = []
    for r in results:
        if isinstance(r, Exception): continue
        domain_key = r["domain"].lower()
        for item in r.get("results", {}).get(domain_key, []):
            hits.append({**item, "domain": r["domain"]})

    # dedup by doc_id, sort by score desc
    seen = set(); unique = []
    for h in sorted(hits, key=lambda x: x.get("score", 0), reverse=True):
        if h["doc_id"] not in seen: seen.add(h["doc_id"]); unique.append(h)
    return unique[:top_k]
```

---

## 5. 错误处理

| 场景 | 行为 |
|------|------|
| BiBLE 不可达 | 返回 error context: "BiBLE Atlas unreachable." |
| 某个 domain 搜索失败 | 跳过该 domain，合并其余 |
| LLM query synthesis 失败 | fallback: user_message 作为 query |
| 全部失败 | 返回空 context + hint |

---

## 6. 与 MCP 工具的关系

```
MCP tools: model 自动调用，单 domain，结果给模型
consult:   用户主动，三 domain 并行+合并，结果注入上下文
```

同一套 BiBLE V4 API，不同触发者和消费方式。

---

## 7. 参考文档

- [`../../02-interfaces.md`](../../02-interfaces.md) — `/daemon/consult`、BiBLE V4 API
- [`../../04-config.md`](../../04-config.md) — `search.default_top_k`
- [`mcp-tools.md`](mcp-tools.md) — MCP 工具 schema
