# 06-recall/mcp-tools.md — MCP Tools（L3）

> 模型驱动的 MCP 工具：schema、BiBLE V4 映射、实现约束、错误处理。6 活跃 + 2 postponed。
>
> **⚠️ Phase 阶段**: 本文描述最终态完整设计。Phase 3b 实现 6 活跃 tool 真实调用 +
> degradation + 下载轮询。§3 代码示例为 sync 伪代码，实际实现以 async handler（见
> `docs/backlogs/Phase-3/2026-06-24-phase-3b-flush-and-mcp.md`）为准。

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

### 3.1 `bible_memory_save` 序列化

`bible_memory_save` 接受 `messages[]`（对话消息数组），需序列化为文件后通过 `POST /api/import/memory`（multipart/form-data）上传。

```python
import json, tempfile, os

def serialize_and_save(messages: list[dict], title: str | None, abstract: str | None) -> str:
    payload = {
        "messages": messages,    # [{"role":"user","content":"..."}, ...]
        "title": title or "",
        "abstract": abstract or "",
        "saved_at": datetime.now().isoformat()
    }
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(payload, f, ensure_ascii=False)
        tmp_path = f.name

    # 上传同 flush 模式：files + data → POST /api/import/memory → task_id
    os.unlink(tmp_path)
    return task_id
```

- `kb_index` 取自 `bible.kb_index`（默认 `"bible-cc"`），`tag` 固定 `"memory"`。
- 上传后立即删除临时文件。

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

由 setup wizard（`bible_cc_plugin.scripts.setup`）在 install 时动态生成于 plugin 目录根，**不提交 git**。示例如下（实际值由 setup 根据用户配置写入）：

```json
{
  "mcpServers": {
    "bible-cc": {
      "command": "uv",
      "args": ["run", "python", "-m", "bible_cc_plugin.mcp.server"],
      "env": {
        "BIBLE_ATLAS_BASE_URL": "http://localhost:5555",
        "BIBLE_ATLAS_TOKEN": ""
      }
    }
  }
}
```

env 值为字面量，无 `${VAR:-default}` 语法。生命周期详见 `02-interfaces.md` §3.2。

---

## 6. 参考文档

- [`../../02-interfaces.md`](../02-interfaces.md) — MCP tool schema、BiBLE V4 API、`.mcp.json`
- [`../../04-config.md`](../04-config.md) — `search` config
- [`consult.md`](consult.md) — 用户主动跨域搜索
