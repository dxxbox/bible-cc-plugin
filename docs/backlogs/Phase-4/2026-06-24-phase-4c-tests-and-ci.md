# Phase 4c: Recall 集成测试 + 合约测试 + CI

> **依赖**: Phase 4a（consult 端点）+ Phase 4b（MCP polish）
> **被依赖**: Phase 5（commands + operability 的集成测试建立在 Phase 4 CI 框架之上）
> **父文档**: [Phase 4 总览](../plans/2026-06-13-phase-4-recall-pipeline.md)

**交付 Command**: 无新 command。CI 自动化测试对用户透明。

**预估: 1.5 天**

### 测试标注

4c.1（集成测试）需要 BiBLE test server → `[Integration] [Post]`。4c.2（合约测试）需要 BiBLE test server → `[Integration] [Post]`。4c.3（CI 扩展）`[CI] [Post]`。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## Scenario

> Consult 端点 + MCP polish 就绪后，需要验证 recall pipeline 的完整行为。集成测试使用 BiBLE test server 验证 MCP server stdio transport → BiBLE API 的端到端流程 + consult 跨域搜索的正确性。
>
> CI 在 Phase 3d 的基础上扩展：加入 MCP server 的 stdio 集成测试 + consult 集成测试。

---

## Feature 逐个讨论

### F4c.1 — Integration Tests

| 属性 | 说明 |
|------|------|
| **理由** | Recall pipeline 涉及 MCP server（独立 stdio 进程）+ BiBLE API（跨网络）——必须通过集成测试验证端到端行为。MCP server 以 subprocess 启动，通过 stdio JSON-RPC 发送 tool call，验证 response。 |
| **优先级** | P0 |
| **依赖** | BiBLE test server、Phase 4a + 4b 完成 |

**`tests/integration/test_mcp_server.py`**（Phase 3d 已创建基础版，Phase 4c 扩展 4 个新测试）:

*Phase 4c 新增*:
- `test_mcp_parallel_tool_calls` — 并发调用多个 tool → 各自独立返回
- `test_mcp_startup_diagnostics` — 验证启动日志包含 tool list + BiBLE health
- `test_mcp_tool_call_logging` — 验证 `[mcp:tool]` 日志格式
- `test_mcp_bible_recovery` — BiBLE 先不可达后恢复 → tools 从 error 恢复到正常返回

**`tests/integration/test_recall_consult.py`**（新建，7 个测试）:
- `test_consult_with_query` — 非空 query → 三域并行 → 合并结果
- `test_consult_empty_query` — 空 query → LLM 归纳 → 搜索
- `test_consult_single_domain` — domains=["memory"] → 只搜记忆
- `test_consult_limit` — limit=3 → 每域最多 3 条
- `test_consult_result_sorting` — 验证结果按 score 降序
- `test_consult_llm_fallback` — LLM 归纳失败 → fallback 错误提示
- `test_consult_parallel_isolation` — 一个域搜索失败 → 其他域不受影响

### F4c.2 — Contract Tests

| 属性 | 说明 |
|------|------|
| **理由** | MCP tools 是模型调用的——模型依赖 tool schema（inputSchema）来决定何时调用、传什么参数。Consult 端点也是后续 command 的依赖——响应结构必须稳定。 |
| **优先级** | P0 — 接口契约 |
| **依赖** | Phase 4a + 4b、BiBLE test server |

**`tests/contract/test_mcp_tools.py`**（Phase 3d 已创建基础版，Phase 4c 扩展 3 个新测试）:

*Phase 4c 新增*:
- `test_tool_input_schema_matches_api` — 每个 tool 的 inputSchema 参数与 BiBLE API 一一对应
- `test_tool_error_response_schema` — 错误时 response 结构（error, detail, suggestion）
- `test_tool_list_stable` — 验证 tool 列表不含多余/缺失工具

**`tests/contract/test_consult_api.py`**（新建，4 个测试）:
- `test_consult_response_schema` — 验证 `{query_used, hits: [{domain, id, title, snippet, score}]}`
- `test_consult_empty_query_schema` — 空 query 时 `query_used` 不为空（LLM 已生成）
- `test_consult_hit_fields` — 每个 hit 的必需字段不因 domain 变化
- `test_consult_error_schema` — BiBLE 不可达时返回 structured error

### F4c.3 — CI 扩展

| 属性 | 说明 |
|------|------|
| **理由** | Phase 4 的 MCP 和 consult 集成测试需要 BiBLE test server。在 Phase 3d CI 基础上增量扩展。 |
| **优先级** | P0 — CD 集成测试 |
| **依赖** | Phase 3d CI、BiBLE test server |

**`dev.sh ci` 扩展**：在 Phase 3d 基础上增加 MCP server 集成测试（stdio subprocess mode）+ consult 集成测试 + recall 合约测试（`test_consult_api.py`）。

---

## 实现顺序

```
F4c.1 (集成测试) ──► F4c.2 (合约测试) ──► F4c.3 (CI 扩展)
```

| 顺序 | Feature | 理由 |
|------|---------|------|
| **1st** | F4c.1 集成测试 | 需要 Phase 4a + 4b 全部功能就绪 |
| **2nd** | F4c.2 合约测试 | 在 F4c.1 基础上增加 schema 验证维度 |
| **3rd** | F4c.3 CI 扩展 | 所有测试通过后接入 CI |

---

## 验收标准

### 集成测试
- [ ] `test_mcp_server.py` 11 个测试全部通过（7 Phase 3d + 4 Phase 4c）
- [ ] `test_recall_consult.py` 7 个测试全部通过

### 合约测试
- [ ] `test_mcp_tools.py` 7 个测试全部通过（4 Phase 3d + 3 Phase 4c）
- [ ] `test_consult_api.py` 4 个测试全部通过

### CI
- [ ] `./scripts/dev.sh ci` 包含 Phase 3 + Phase 4 全部测试
- [ ] MCP server stdio 集成测试在 CI 中正常运行
- [ ] CI 全绿

---

## 产出文件

```
tests/integration/test_mcp_server.py     ← (修改: +4 个测试)
tests/integration/test_recall_consult.py ← (新文件: 7 个测试)
tests/contract/test_mcp_tools.py         ← (修改: +3 个测试)
tests/contract/test_consult_api.py       ← (新文件: 4 个测试)
scripts/dev.sh                           ← (修改: ci 函数扩展)
```
