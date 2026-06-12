# 06-recall/mcp-tools.md — MCP Tools（L3）

> 模型驱动的 MCP 工具：schema、BiBLE V4 映射、实现约束、错误处理。6 活跃 + 2 postponed。

---

## 1. Tool 清单

### 1.1 活跃（6 个）

| Tool | Parameters | BiBLE Endpoint | 说明 |
|------|-----------|----------------|------|
| `bible_memory_search` | `query`, `tag?` ("memory"), `top_k?`, `search_type?` | `POST /api/search/memory` | 搜索个人记忆 |
| `bible_memory_save` | `messages[]`, `title?`, `abstract?` | `POST /api/import/memory` | 手动保存记忆（序列化为文件上传） |
| `bible_memory_get` | `storage_path` | `POST /api/download/memory/file` → poll → artifact | 下载记忆文件 |
| `bible_knowledge_search` | `query`, `tag`, `top_k?`, `search_type?` | `POST /api/search/knowledge-base` | 搜索知识库 |
| `bible_skill_search` | `query`, `tag` ("skill"), `top_k?`, `search_type?` | `POST /api/search/skill` | 搜索技能 |
| `bible_skill_get` | `storage_path` | `POST /api/download/skill/file` → poll → artifact | 下载技能文件 |

### 1.2 Postponed（2 个）

| Tool | 原因 |
|------|------|
| `bible_memory_delete` | V4 API 未提供 delete 端点。MCP server placeholder（"not yet available"）。 |
| `bible_knowledge_list` | V4 API 未提供 list 端点。同上。 |

---

## 2. 实现约束

1. **纯 API 封装**：MCP Server 不访问 daemon SQLite，不调 daemon HTTP API。
2. **无状态**：每个调用独立。配置从 env / `.mcp.json` 读取。
3. **错误即返回**：BiBLE 不可达时返回结构化 error 给 model，不 crash。
4. **`uv run` 入口**：`.mcp.json` → `command: "uv"`, `args: ["run", "python", "-m", "bible_cc_plugin.mcp.server"]`。

---

## 3. 搜索工具实现参考

```python
def search_memory(query: str, tag: str = "memory", top_k: int = 8, search_type: str = None) -> dict:
    payload = {"query": query, "tag": tag, "top_k": top_k}
    if search_type: payload["search_type"] = search_type
    results = client.post("/api/search/memory", json=payload)
    # `search.default_min_score` (0.35) 作为 client-side filter，非 V4 API 参数
    return [r for r in results if r["score"] >= min_score]
```

默认值来自 `search.default_top_k`（8）。`search.default_min_score`（0.35）是 client-side filter——V4 API 不提供 min_score 参数，结果返回后由 client 按 score 阈值过滤。

---

## 4. 错误响应

```json
{
  "error": "BiBLE Atlas unreachable at http://localhost:5555",
  "detail": "Connection refused",
  "suggestion": "Check /bible-cc:check-bible or /bible-cc:status"
}
```

---

## 5. `.mcp.json`

```json
{
  "mcpServers": {
    "bible-cc": {
      "command": "uv",
      "args": ["run", "python", "-m", "bible_cc_plugin.mcp.server"],
      "env": {
        "BIBLE_ATLAS_BASE_URL": "http://localhost:5555"
      }
    }
  }
}
```

env 值为字面量，无 `${VAR:-default}` 语法。

---

## 6. 参考文档

- [`../../02-interfaces.md`](../../02-interfaces.md) — MCP tool schema、BiBLE V4 API、`.mcp.json`
- [`../../04-config.md`](../../04-config.md) — `search` config
- [`consult.md`](consult.md) — 用户主动跨域搜索
