# Phase 4 Review Findings

> 日期：2026-11-06
> 范围：Phase 4 总览 + 4a/4b/4c backlog + `06-recall/consult.md` + L1 `/daemon/consult`
> 基线：v0.28.0（Phase 3a–3d 已完成）

---

## A. 硬冲突（实施前必须裁决）

| # | 冲突 | 证据 | 裁决 |
|---|------|------|------|
| **A1** | 4b.3 要求 MCP → daemon HTTP 上报，违反 CLAUDE.md「MCP server and daemon are independent: No direct communication between them」 | `Phase-4b:91` vs `CLAUDE.md` Key Rules | **裁决：方案 B — 共享 JSONL（MCP 写、daemon 读），无 HTTP 通信** |
| **A2** | 4a 用 `ThreadPoolExecutor` 调用 async client，技术上不可行 | `Phase-4a:76` vs `client.py`（httpx.AsyncClient）；SW design `consult.md:51` 已用 `asyncio.gather` | **裁决：以 SW design 为准，使用 `asyncio.gather(return_exceptions=True)`** |
| **A3** | consult 响应 schema 三份不一致 | L1 `02-interfaces.md:106` `{context, query_used, hits:[{domain,id,title,snippet,score}]}`；4a 缺 `context`；SW design hits 用 `doc_id/section_title/content` | **裁决：方案 A — 更新 L1 为 BiBLE 原生字段** |

---

## B. 文档缺口 / 不一致

| # | 问题 | 位置 | 处理 |
|---|------|------|------|
| B1 | `GET /daemon/debug/mcp-calls` 只在总览/4b 出现，L1/L3 未定义 | `02-interfaces.md`、`http-api.md` | 待 A1 裁决后同步 |
| B2 | MCP 上报的接收端点（路径/schema）未定义 | `Phase-4b:91` | 待 A1 裁决 |
| B3 | 4a Request 含 `domains`/`limit`，L1 仅 `{session_id, query?}` | `02-interfaces.md:105` | 更新 L1（可选参数） |
| B4 | 4a 写 `client.search_knowledge()`，实际为 `search_knowledge_base()` | `Phase-4a:60` | 实现时以代码为准 |
| B5 | 总览 F4.5 说「6 个工具全部注册」，实际 8 个（6 active + 2 postponed）；4c 用 7 个测试口径 | 总览:88 / 4c:108 | 统一为 8 个工具、7 个合约测试 |
| B6 | consult 时序 ~5s，但 LLM 5s + 三域搜索（每域 10s）可能超 | `02-interfaces` 时序表 | 重新标注（建议 ~10s / 最坏 15s） |
| B7 | 总览 F4.5 的 `limit=3` 与 tool schema 参数 `top_k` 不一致 | 总览:85 | 统一为 `top_k` |

---

## C. 与现状重复（3b/3d 已完成，Phase 4 只需增量）

| # | 已实现 | Phase 4 实际增量 |
|---|--------|-----------------|
| C1 | MCP 6 工具真实调用 + `[mcp:tool]` 基础日志 | 4b.2：`content_len` / `task_id` 字段 |
| C2 | MCP 启动诊断已有 `starting` / `base_url` / `tools:` / `health=` | 4b.1：格式细化（8 tools、latency） |
| C3 | `test_mcp_server.py`（7）、`test_mcp_tools.py`（4）已在 3d 创建 | 4c：扩展 +4 / +3 |
| C4 | `bible_memory_save` 已改为 `meta.json` + 附件 | 4a/总览的 `files[]` 描述已过时 |

---

## D. 技术风险

| # | 风险 | 建议 |
|---|------|------|
| D1 | LLM 归纳需复用 detector 的 Anthropic client（内部 `_create_client`） | 新增 `summarize_query()`，支持 `DETECTOR_TEST_MODE` 返回固定 query |
| D2 | 去重按 `doc_id`，跨域可能同名 | 用 `(domain, doc_id)` 复合键 |
| D3 | `context` 字段生成逻辑未定义 | 用 hits 拼接 `<relevant-memories>` 风格文本 |
| D4 | `domains` 校验与 knowledge tag 来源 | 仅 memory/knowledge/skill；tag 用 `config.search.default_knowledge_tag`（默认 `"design"`，已存在） |
| D5 | `test_mcp_bible_recovery` 需中途停/启 test server，但 `bible_test_url` 是 session 级 fixture | 该测试单独启停 server，或改为 mock 不可达 |
| D6 | MCP 启动 health 检查（3s 超时）会延迟启动 | 可接受，确保不阻塞 |

---

## E. 建议的实施顺序

1. 先裁决 A1/A3（A2 已裁）。
2. **4a**：`asyncio.gather`；Request/Response 以 L1 为准并补 `domains`/`limit`，同步更新 L1/L3；LLM 归纳复用 detector client + test mode。
3. **4b**：启动诊断/日志增强直接做；`mcp-calls` 按 A1 裁决实现。
4. **4c**：基于 3d 扩展（+7 集成 +7 合约），不重复创建文件。
5. 版本 0.29.0。

---

## 裁决记录

### A1 — MCP 调用记录（方案 B：共享 JSONL）

- MCP server 每次 tool 调用**追加一行 JSON** 到 `~/.bible-cc/mcp-calls.jsonl`：
  `{time, tool, args_summary, result_summary, latency_ms, status, task_id?, content_len?}`
- daemon 的 `GET /daemon/debug/mcp-calls?limit=50` 读取该文件尾部 N 行并返回。
- **不新增 MCP → daemon HTTP 通信**，符合 CLAUDE.md 独立性规则。
- 写文件为 best-effort：失败不影响 tool 调用。
- 轮转：文件超过 5 MB 时 MCP 侧重命名 `.1`（保留一份），daemon 只读当前文件。

### A2 — consult 并行（已裁决）

`asyncio.gather(*tasks, return_exceptions=True)`；单域失败跳过，不影响其他域。

### A3 — consult 响应 schema（方案 A：BiBLE 原生字段）

- L1 更新为：
  `{context: string, query_used: string, hits: [{domain, doc_id, section_id, section_title, content, score}]}`
- 不做字段映射；`snippet` 由下游命令按需从 `content` 截断。
- 去重键：`(domain, doc_id, section_id)`。
- SW design `consult.md` 与 4a backlog 同步更新。
