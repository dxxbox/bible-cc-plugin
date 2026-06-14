# Phase 4: Recall Pipeline（MCP + Consult）

> **For agentic workers:** Phase 4 实现 pull model 的第二条路径——模型/用户主动从 BiBLE Atlas 拉取跨 session 知识。
>
> **/orchestrate 并行提示**: Phase 4 和 Phase 3 在 Phase 2 完成后可并行开发。MCP tools 和 BiBLE flush 互不依赖。MCP contract tests 可用 stub/mock 先行（不依赖 BiBLE 中有真实数据），完整端到端验证在 Phase 3 完成后补充。

**Goal:** MCP server 就绪（6 活跃工具）、/daemon/consult 端点可用（跨域搜索）、pull model 完整。

**Architecture:** MCP Python SDK (stdio transport) → BiBLE V4 API (via client.py) + consult endpoint (daemon HTTP → parallel domain search → merge results)。

**Tech Stack:** MCP Python SDK, httpx, asyncio

**预估: 4-5 天**

---

## Feature 逐个讨论

### F4.1 — MCP Server（mcp/server.py）

| 属性 | 说明 |
|------|------|
| **理由** | 模型主动 pull 跨 session 知识的唯一方式。CLAUDE.md 定义 6 个活跃 MCP 工具（memory search/save/get, knowledge search, skill search/get）+ 2 个 postponed（delete, list）。MCP server 通过 stdio 与 Claude Code 通信，每个 session 一个 MCP server 实例。MCP server 与 daemon 完全独立——不通过 daemon 中转，直接用 client.py 调 BiBLE Atlas。 |
| **优先级** | P0 — recall pipeline 核心 |
| **依赖** | client.py（BiBLE API calls）、config.py（base_url）、types.py |

6 个 tool 实现：
- `bible_memory_search(query, limit)`: `POST /api/search/memory` → 返回记忆列表
- `bible_memory_save(title, content, tags?)`: `POST /api/import/memory` → 保存新记忆
- `bible_memory_get(memory_id)`: `POST /api/download/memory/{id}` → 返回完整记忆
- `bible_knowledge_search(query, limit)`: `POST /api/search/knowledge` → 返回知识条目
- `bible_skill_search(query, limit)`: `POST /api/search/skill` → 返回技能列表
- `bible_skill_get(skill_id)`: `POST /api/download/skill/{id}` → 返回完整技能定义

每个 tool 定义 inputSchema（JSON Schema），BiBLE 不可达时返回 structured error（模型可 retry/continue）。

### F4.2 — Consult 端点（/daemon/consult）

| 属性 | 说明 |
|------|------|
| **理由** | MCP 是模型调用的，但用户也需要手动搜索能力。`/daemon/consult` 是用户触发的跨域搜索。两个创新点：(1) query 为空时 LLM 自动归纳对话生成 query——用户不需要想 search term；(2) 并行搜三个域（memory + knowledge + skill），不是串行——减少等待时间。 |
| **优先级** | P0 |
| **依赖** | client.py（domain search）、detector.py（query 为空时 LLM 归纳对话） |

流程：
1. 接收 query（可能为空）
2. 空 → LLM 读 session turns → 总结 → 生成 search query
3. 非空 → 直接用 query
4. 并行 `POST /api/search/memory` + `/api/search/knowledge` + `/api/search/skill`
5. 合并结果（按 score 排序）
6. 返回 `{query_used, context, hits: [{domain, id, title, snippet, score}]}`

### F4.3 — Integration Tests（Recall）

| 属性 | 说明 |
|------|------|
| **优先级** | P0 — TDD |
| **依赖** | MCP server、consult endpoint、BiBLE test server |

- `test_mcp.py`（unit）: MCP tool schema 定义正确、tool handler 映射正确
- `test_mcp_server.py`（integration）: MCP server stdio → BiBLE test server 端到端（每个 tool 一个 test case + BiBLE 不可达 error case）、parallel tool calls
- `test_recall_consult.py`（integration）: consult 非空 query → 三域并行 → 合并结果、空 query → LLM 归纳 → 搜索、结果格式校验

### F4.4 — CI Pipeline 扩展：MCP Integration Test 接入

| 属性 | 说明 |
|------|------|
| **理由** | Phase 4 的 MCP server 使用 stdio transport——CI 中测试 MCP 需要启动 MCP server 进程 + BiBLE test server。CI 验证 MCP tools 的端到端行为（tool call → BiBLE API → response）。 |
| **优先级** | P0 — CD 集成测试 |
| **依赖** | Phase 3 CI、BiBLE test server、F4.1（MCP server） |

实现：`dev.sh ci` 扩展包含 MCP integration tests。MCP server 以 subprocess 启动，通过 stdio JSON-RPC 发送 tool call，验证 response。

### F4.5 — Contract Tests：MCP ↔ BiBLE API 契约

| 属性 | 说明 |
|------|------|
| **理由** | MCP tools 是模型调用的——模型依赖 tool schema（inputSchema）来决定何时调用、传什么参数。如果 tool schema 与 BiBLE API 实际行为不一致，模型会做出错误决策。契约测试验证每个 MCP tool 的 inputSchema 与 BiBLE API 的 request schema 一致 + tool 返回的 output 结构符合 tool 声明的 schema。 |
| **优先级** | P0 — 接口契约 |
| **依赖** | F4.1（MCP server）、BiBLE test server、02-interfaces.md §3（MCP tool schema） |

实现：
- `tests/contract/test_mcp_tools.py`：使用 BiBLE test server
  - 每个 tool：验证 inputSchema 字段与 BiBLE API 参数一致
  - `bible_memory_search("test", limit=3)` → 验证 response 结构（title, snippet, score, id）
  - `bible_memory_save(...)` → 验证 task_id 返回
  - Error: BiBLE 不可达 → 验证 tool 返回 structured error（不抛异常）
- Tool list：验证 6 个工具全部注册，无多余工具

### F4.6 — Debuggability：MCP 调用追踪 + Consult 查询分解

| 属性 | 说明 |
|------|------|
| **理由** | MCP tools 是模型调用的——用户不直接控制调用时机和参数。当模型说"我搜不到"时，必须能回溯 MCP server 到底收到什么请求、调了什么 API、返回了什么。Consult 涉及 LLM 归纳和并行搜索——任何一个环节出问题都需要分解诊断。 |
| **优先级** | P0 — 模型调用调试 |
| **依赖** | mcp/server.py、client.py、Phase 2 detection logging |

实现：

**MCP 调用追踪日志**（写入 `~/.bible-cc/daemon.log`，与 daemon 同一文件——Phase 0 复盘规则 D）：
```
[mcp:tool] bible_memory_search(query="PostgreSQL", limit=5) → 5 hits (0.3s)
[mcp:tool] bible_memory_save(title="Rate limiting design", content_len=450) → task_id=abc (1.1s)
[mcp:tool] bible_memory_search(query="unknown") → 0 hits (0.2s)
[mcp:tool] ERROR: bible_memory_search → BiBLE unreachable (timeout 30s)
[mcp:tool] bible_knowledge_search(query="auth pattern") → 3 hits (0.4s)
```
> **注意**: MCP server 是独立 stdio 进程，不能依赖 daemon 的 stderr 重定向。必须在 MCP server 内部显式打开 `~/.bible-cc/daemon.log` 追加写入。

**MCP 启动诊断**：
```
[mcp:server] starting on stdio transport
[mcp:server] registered 6 tools: bible_memory_search, bible_memory_save, bible_memory_get, bible_knowledge_search, bible_skill_search, bible_skill_get
[mcp:server] BiBLE base_url=http://localhost:5555, health=OK (15ms)
```

**Consult 查询分解日志**（daemon stderr）：
```
[consult] query="PostgreSQL migration" → direct search
[consult] memory_search → 5 hits (0.3s)
[consult] knowledge_search → 2 hits (0.4s)
[consult] skill_search → 0 hits (0.2s)
[consult] merged: 7 hits, top_score=0.92
[consult] DONE (total=0.9s)

[consult] query="" → LLM summarization... query="biBLE atlas deployment"
[consult] LLM latency=1.5s, tokens=120
[consult] memory_search → 3 hits (0.3s)
[consult] knowledge_search → 1 hits (0.4s)
[consult] skill_search → 2 hits (0.2s)
[consult] merged: 6 hits, top_score=0.85
[consult] DONE (total=2.5s)
```

**Debug 端点**：
- `GET /daemon/debug/mcp-calls?limit=50` → MCP server 最近调用记录（daemon 通过 shared log 或 MCP server 上报）

---

## Phase 4 验收标准

- [ ] `./scripts/dev.sh ci` 通过（lint + unit test + contract test + integration test）
- [ ] MCP server stdio transport 可用，Claude Code 可发现 6 个 MCP 工具
- [ ] `tests/contract/test_mcp_tools.py` 通过：每个 tool 的 inputSchema 验证 + BiBLE 不可达 error case
- [ ] MCP server 启动时 stderr 输出注册工具列表 + BiBLE 连通性检查
- [ ] 每个 MCP tool 调用时 stderr 输出 tool name + args + result count + latency
- [ ] MCP tool 失败时 stderr 输出详细错误（timeout / unreachable / API error）
- [ ] 每个 MCP tool 正确调用 BiBLE V4 API（使用 test mode server 验证）
- [ ] BiBLE 不可达时 MCP tools 返回 structured error（不 crash，不阻塞模型 turn）
- [ ] consult 非空 query：三域并行搜索 + 结果合并正确，stderr 可见每域耗时
- [ ] consult 空 query：LLM 归纳对话 → 生成 query → 搜索可用，stderr 可见生成的 query
- [ ] `GET /daemon/debug/mcp-calls?limit=50` 返回调用历史
- [ ] 集成测试全部通过

---

## Phase 4 产出文件

```
src/bible_cc_plugin/
├── mcp/
│   ├── __init__.py
│   └── server.py               ← F4.1, F4.4 (MCP stdio server, 6 tools + 调用追踪)
tests/
├── unit/
│   └── test_mcp.py             ← F4.3 (tool schema unit tests)
├── integration/
│   ├── test_mcp_server.py      ← F4.3 (MCP server + BiBLE test)
│   └── test_recall_consult.py  ← F4.3 (consult endpoint + BiBLE test)
```
