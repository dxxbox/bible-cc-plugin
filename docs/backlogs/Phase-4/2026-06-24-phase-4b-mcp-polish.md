# Phase 4b: MCP Polish + Debuggability

> **依赖**: Phase 3b（MCP 真实调用已接入）+ Phase 3c（connectivity check 就绪）
> **被依赖**: Phase 4c（MCP 合约测试需要 polish 完成后的稳定接口）
> **父文档**: [Phase 4 总览](../plans/2026-06-13-phase-4-recall-pipeline.md)

**交付 Command**: 无新 command。MCP 改进对用户透明。

**预估: 1 天**

### 测试标注

4b.1（启动诊断）和 4b.2（调用追踪）属于日志/诊断增强，在现有 MCP unit test 基础上验证日志输出格式。不需要新测试文件。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## Scenario

### 启动诊断

> MCP server 通过 stdio transport 运行——用户看不到它的输出。当模型说"我搜不到"时，开发者必须能回溯 MCP server 到底收到什么请求、调了什么 API、返回了什么。
>
> MCP server 启动时输出：注册工具列表 + BiBLE 连通性检查结果。这是排查"模型找不到工具"或"工具调用失败"的第一手信息。

### 调用追踪

> 每个 MCP tool 调用输出：tool name + 参数摘要 + result count + latency。错误时输出详细原因。所有日志写入 `~/.bible-cc/daemon.log`（与 daemon 同一文件——Phase 0 规则 D）。

---

## Feature 逐个讨论

### F4b.1 — MCP 启动诊断增强

| 属性 | 说明 |
|------|------|
| **理由** | MCP server 是独立 stdio 进程——用户看不到它的 stderr。启动诊断信息是排查"模型找不到工具"或"BiBLE 不通"的第一手信息。Phase 3b 之后 MCP 已接入真实 BiBLE 调用，启动时应验证连通性。 |
| **优先级** | P0 |
| **依赖** | client.py（`check_health()`）、Phase 3c（connectivity check） |

**启动日志**（写入 `~/.bible-cc/daemon.log`）：
```
[mcp:server] starting on stdio transport
[mcp:server] registered 8 tools: bible_memory_search, bible_memory_save, bible_memory_get, bible_knowledge_search, bible_skill_search, bible_skill_get, bible_memory_delete, bible_knowledge_list
[mcp:server] BiBLE base_url=http://localhost:5555, health=OK (15ms)
```

**BiBLE 不可达时**：
```
[mcp:server] BiBLE base_url=http://localhost:5555, health=UNREACHABLE (connect timeout)
[mcp:server] WARNING: tools will return structured errors until BiBLE recovers
```

**base_url 未配置时**：
```
[mcp:server] BiBLE base_url=<not configured> — tools will return config errors
```

### F4b.2 — MCP 调用追踪完善

| 属性 | 说明 |
|------|------|
| **理由** | MCP tools 是模型调用的——用户不直接控制调用时机和参数。必须能回溯每次 tool 调用的完整信息。Phase 3b 已实现基础 `[mcp:tool]` 日志，Phase 4b 完善为结构化格式。 |
| **优先级** | P0 |
| **依赖** | Phase 3b（MCP 真实调用） |

**已实现（Phase 3b）**:
```
[mcp:tool] bible_memory_search(query="PostgreSQL", top_k=5) → 5 hits (0.3s)
[mcp:tool] ERROR: bible_memory_search → BiBLE unreachable (timeout 30s)
```

**Phase 4b 增强**:
- 增加 `content_len`（save 类操作记录内容长度）
- 增加 `task_id`（import 操作记录异步 task_id）
```
[mcp:tool] bible_memory_save(title="Rate limiting design", content_len=450) → task_id=abc (1.1s)
[mcp:tool] bible_memory_get(storage_path="/mem/abc123") → OK (0.5s)
```

### F4b.3 — Debug 端点

| 属性 | 说明 |
|------|------|
| **理由** | MCP server 是 stdio 进程，与 daemon HTTP API 分离。当用户想问"刚才模型调了什么 MCP tool"时，需要一个查询入口。 |
| **优先级** | P0 |
| **依赖** | F4b.2（调用追踪） |

**实现方式**: MCP server 在每次 tool 调用时，通过 HTTP POST 将调用记录上报到 daemon。Daemon 维护 MCP 调用 ring buffer（`collections.deque`，maxlen=100）。

**新增端点**: `GET /daemon/debug/mcp-calls?limit=50`

**响应**:
```json
{
  "calls": [
    {
      "time": "2026-06-24T15:30:00Z",
      "tool": "bible_memory_search",
      "args": {"query": "PostgreSQL", "top_k": 5},
      "result": "5 hits",
      "latency_ms": 300,
      "status": "ok"
    }
  ]
}
```

> **注意**: MCP server 上报到 daemon 使用 best-effort——上报失败（daemon 不可达）时 MCP tool 调用不受影响。日志仍写入文件作为兜底。

---

## 实现顺序

```
F4b.1 (启动诊断) ──► F4b.2 (调用追踪) ──► F4b.3 (debug 端点)
```

| 顺序 | Feature | 理由 |
|------|---------|------|
| **1st** | F4b.1 启动诊断 | 独立——只依赖 client.py 的 `check_health()` |
| **2nd** | F4b.2 调用追踪 | 增强 Phase 3b 已有的日志格式 |
| **3rd** | F4b.3 debug 端点 | 依赖 F4b.2 + daemon 新增接收端点 |

---

## 验收标准

### 启动诊断
- [ ] MCP server 启动时输出注册工具列表
- [ ] MCP server 启动时检查 BiBLE 连通性（reachable / unreachable）
- [ ] base_url 未配置时输出明确提示

### 调用追踪
- [ ] 每个 tool 调用输出 `[mcp:tool]` 日志（tool name + args + result + latency）
- [ ] save 类操作记录 `content_len`
- [ ] import 类操作记录 `task_id`
- [ ] 错误时输出详细原因

### Debug 端点
- [ ] `GET /daemon/debug/mcp-calls?limit=50` 返回 MCP 调用历史
- [ ] MCP server 上报失败不影响 tool 调用（best-effort）

---

## 产出文件

```
src/bible_cc_plugin/mcp/server.py        ← (修改: 启动诊断 + 调用追踪 + 上报)
src/bible_cc_plugin/daemon/server.py     ← (修改: debug/mcp-calls 端点)
```
