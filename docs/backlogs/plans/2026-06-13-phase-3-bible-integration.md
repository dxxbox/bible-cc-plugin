# Phase 3: BiBLE Integration + Flush

> **For agentic workers:** Phase 3 是本地数据到达 BiBLE Atlas 的桥梁。
>
> **/orchestrate 并行提示**: Phase 3 和 Phase 4 在 Phase 2 完成后可并行开发——两者都依赖 client.py + buffer，但互不依赖。BiBLE flush（Phase 3）和 MCP tools（Phase 4）是独立功能。Phase 4 的 MCP 工具端到端验证需要 Phase 3 的 BiBLE 里有数据——但 MCP contract test 可用 stub/mock 先行。建议：两个独立的 `/orchestrate custom` 调用，在 Phase 5 汇合。

**Goal:** BiBLE HTTP client 完成、flush 链路打通（daemon → BiBLE Atlas import）、graceful degradation（BiBLE 不可达时本地不受影响）。

**Architecture:** httpx async client → BiBLE Atlas V4 REST API → flush pipeline（bundle moments → import → update flushed status）。

**Tech Stack:** httpx, asyncio

**预估: 4-5 天**

---

## Feature 逐个讨论

### F3.1 — BiBLE HTTP Client（client.py）

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 明确要求：BiBLE HTTP client 是唯一实现。daemon 和 MCP server 共用同一个 client——不得重复实现。client 封装了所有 BiBLE V4 API 调用、认证、错误处理、超时。这是与 BiBLE Atlas 服务的唯一通信通道。 |
| **优先级** | P0 — BiBLE 通信基础 |
| **依赖** | config.py（base_url, token, kb_index）、types.py |

封装的 API 调用：
- `import_memory(session_id, moments, metrics)` → `POST /api/import/memory` → 返回 task_id（async import）
- `search_memory(query, limit)` → `POST /api/search/memory`
- `search_knowledge(query, limit)` → `POST /api/search/knowledge`
- `search_skill(query, limit)` → `POST /api/search/skill`
- `get_memory(id)` → `POST /api/download/memory/{id}`
- `get_skill(id)` → `POST /api/download/skill/{id}`
- `get_task_status(task_id)` → `GET /api/control/admin/tasks/{task_id}`
- `check_health()` → `GET /health` → 返回 latency_ms

错误处理：timeout（默认 30s）、4xx（auth error, bad request）→ structured error with code + message、5xx（server error, unreachable）→ connectivity error。

### F3.2 — Flush Logic

| 属性 | 说明 |
|------|------|
| **理由** | Moment 必须到达 BiBLE Atlas 才算完成采集链路。Flush 连接了本地 SQLite buffer 和 BiBLE Atlas 的 async import pipeline。Flush 是幂等的——同一个 moment 重复 flush 不产生副作用。BiBLE import 是异步的（返回 task_id），daemon 不等待 import 完成——避免阻塞 session end。 |
| **优先级** | P0 |
| **依赖** | client.py、buffer.py（moments table 读写）、config.py（kb_index） |

流程：
1. 查询 `flushed=0` 的 moments
2. Bundle into import payload（session metadata + moments + monitoring data）
3. `POST /api/import/memory` → 获得 task_id
4. 更新 moments 表：`SET flushed=1, import_task_id=X, flushed_at=NOW`
5. 触发点：
   - `/session/end` 自动触发（Phase 2 retrospective 之后）
   - `/daemon/session/flush` 手动触发（push 命令用，flush 但不结束 session）
   - `mid_session_upload=true` 时 Phase 1 检测后立即触发

### F3.3 — BiBLE Connectivity Check

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束——graceful degradation 要求用户随时知道 BiBLE 连通状态。`bible_connectivity: {reachable, latency_ms}` 是 `/daemon/health` 的必需字段。用户通过 `/bible-cc:status` 看到的第一个信息就是 BiBLE 是否通。 |
| **优先级** | P0 |
| **依赖** | client.py（check_health） |

集成到 `/daemon/health` 端点：后台测 BiBLE `/health`，超时不阻塞 health check 返回，超时后 reachable=false。

### F3.4 — Graceful Degradation（BiBLE 不可达）

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束——BiBLE 断连时本地操作不中断，变 red 时不 crash。具体行为：flush 失败时 moments 保持 `flushed=0`（不丢数据）、MCP tools 返回 error（模型可继续）、health check 显示 reachable=false（用户知情）、不自动重试（mark, notify, move on）。 |
| **优先级** | P0 |
| **依赖** | flush logic、health check |

实现：flush 时 catch 网络错误 → moment 保持 flushed=0 → 下次 push 可重试。CLI status hint 更新 bible_connectivity 状态。`retry_count` 字段递增用于统计，但不触发自动重试。

### F3.5 — Integration Tests

| 属性 | 说明 |
|------|------|
| **理由** | BiBLE 通信是跨进程、跨网络的——必须通过集成测试验证。使用 BiBLE Atlas 内置的 test mode server，不需要 OpenSearch/Celery。 |
| **优先级** | P0 |
| **依赖** | BiBLE test server 可用 |

- `test_client.py`: import memory → verify task_id → poll task status、search memory → verify results、auth（token=null 时不发送 header）、timeout handling
- `test_capture_flush.py`: Phase 1 detection → moment storage → flush → verify import task、flush 失败后 retry 行为、mid_session_upload 行为
- `test_concurrency.py`: 多 session 并发 flush

### F3.6 — CI Pipeline 扩展：Integration Test 接入

| 属性 | 说明 |
|------|------|
| **理由** | Phase 3 首次需要 BiBLE test server——CI 必须能启动 test server、跑集成测试、然后清理。这是 CI 从纯 unit test 到 integration test 的升级点。 |
| **优先级** | P0 — CD 集成测试 |
| **依赖** | Phase 2 CI、BiBLE test server（`bible.test_mode.server`） |

实现：`dev.sh ci` 现在包含：(1) 启动 BiBLE test server on dynamic port，(2) 跑 unit + contract + integration tests，(3) 停止 test server。CI 失败时保留 test server 日志。

### F3.7 — Contract Tests：Daemon ↔ BiBLE API 契约

| 属性 | 说明 |
|------|------|
| **理由** | client.py 是 daemon 与 BiBLE 的唯一通信通道。BiBLE V4 API 的 request/response schema 在 `02-interfaces.md` §2 中定义。契约测试验证 client.py 发送的请求格式正确 + 返回的响应结构符合 spec。BiBLE Atlas 版本升级或 API 变更时，这些测试第一个报错。 |
| **优先级** | P0 — 接口契约 |
| **依赖** | F3.1（client.py）、BiBLE test server |

实现：
- `tests/contract/test_bible_api.py`：使用 BiBLE test server
  - import memory → 验证 response 含 task_id
  - search memory/knowledge/skill → 验证 response schema（hits array, score, snippet）
  - get memory/skill → 验证 response 含完整内容
  - health check → 验证 status + latency
  - Auth: token=null 时验证无 Authorization header
  - Error: 404 → 验证 structured error code + message
- JSON schema validation（使用 `jsonschema` 库）

### F3.8 — Debuggability：BiBLE 请求追踪 + Flush 诊断 + 连通性分解

| 属性 | 说明 |
|------|------|
| **理由** | Phase 3 首次引入外部网络通信（BiBLE Atlas）。网络问题是排查频率最高的故障类型。每个 BiBLE API 调用的请求/响应必须可追踪——method、URL、status、latency、response body 摘要。Flush 是数据离开本地到达 BiBLE 的唯一通道——丢失 moment 是严重 bug，flush 的每一步都需要诊断信息。 |
| **优先级** | P0 — 跨网络调试 |
| **依赖** | client.py、flush logic、Phase 1 请求追踪 |

实现：

**BiBLE 请求追踪日志**（client.py 每次调用输出到 daemon stderr）：
```
[bible:req] POST /api/import/memory → 202 (1.2s) task_id=abc-def-123
[bible:req] POST /api/search/memory → 200 (0.3s) hits=5
[bible:req] GET /health → 200 (0.05s)
[bible:req] POST /api/import/memory → ERROR timeout (30.0s) → BiBLE unreachable
```

**Flush 诊断日志**（flush 流程每步输出）：
```
[flush] session=abc123, moments=3, including monitoring data
[flush] bundling → payload_size=2.4KB
[flush] POST /api/import/memory... OK → task_id=xyz-456
[flush] updating 3 moments → flushed=1
[flush] DONE (total=1.8s)
```

**BiBLE 连通性分解测试**（`/bible-cc:check-bible --verbose` 或 API `check_health(verbose=True)` 时）：
```
BiBLE Connectivity Test (base_url=http://localhost:5555)
  DNS resolution... OK (5ms) → 127.0.0.1
  TCP connect... OK (2ms)
  HTTP GET /health... OK (15ms) → status=200
  Total: reachable (22ms)
```
失败时：
```
BiBLE Connectivity Test (base_url=http://bible.atlas.internal:5555)
  DNS resolution... FAIL → NXDOMAIN
  Total: unreachable — check base_url config
```

**Debug 端点**：
- `GET /daemon/debug/flush-log?session_id=X` → 该 session 的 flush 历史：时间、moments_count、task_id、status、error
- `GET /daemon/debug/bible-requests?limit=50` → 最近 50 个 BiBLE API 请求日志

---

## Phase 3 验收标准

- [ ] `./scripts/dev.sh ci` 通过（lint + unit test + contract test + integration test）
- [ ] BiBLE client 正确实现所有 V4 API 调用（memory import, search×3, download, task status, health）
- [ ] `tests/contract/test_bible_api.py` 通过：每个 BiBLE API 的 request/response schema 验证
- [ ] 每个 BiBLE API 调用输出 stderr 追踪日志（method, URL, status, latency, result summary）
- [ ] Flush 链路完整：unflushed moments → bundle → POST import → update flushed=1
- [ ] Flush 每步输出 stderr 诊断日志
- [ ] `GET /daemon/debug/flush-log?session_id=X` 返回 flush 历史
- [ ] `GET /daemon/debug/bible-requests?limit=50` 返回 BiBLE API 请求历史
- [ ] `check_health(verbose=True)` 输出 DNS → TCP → HTTP 三级连通性分解
- [ ] `mid_session_upload=true` 时 Phase 1 检测后立即 flush
- [ ] BiBLE 不可达时 daemon 正常运行，health check 显示 `reachable=false`，stderr 可见超时日志
- [ ] Flush 失败时 moments 保持 flushed=0（不丢失数据），retry_count 递增，stderr 可见错误原因
- [ ] 集成测试全部通过（使用 BiBLE test mode server）
- [ ] `/daemon/health` 返回 bible_connectivity 字段

---

## Phase 3 产出文件

```
src/bible_cc_plugin/
├── daemon/
│   ├── client.py               ← F3.1, F3.8 (BiBLE HTTP client + 请求追踪)
│   ├── server.py               ← (修改: health check + bible_connectivity, debug endpoints)
│   └── buffer.py               ← (修改: flush 相关 CRUD)
tests/integration/
├── test_client.py              ← F3.5
├── test_capture_flush.py       ← F3.5
└── test_concurrency.py         ← F3.5
tests/contract/
└── test_bible_api.py           ← F3.7 (BiBLE API 契约)
├── test_capture_flush.py       ← F3.5
└── test_concurrency.py         ← F3.5
```
