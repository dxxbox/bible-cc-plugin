# Phase 3d: Integration Tests + Contract Tests + CI

> **依赖**: Phase 3c（所有端点就绪才能写集成测试）
> **被依赖**: Phase 4（recall pipeline 的集成测试建立在 Phase 3 CI 框架之上）
> **父文档**: [Phase 3 总览](../plans/2026-06-13-phase-3-bible-integration.md)

**交付 Command**: 无新 command。CI 自动化测试对用户透明。

**预估: 2 天**

### 测试标注

3d.1（集成测试）需要 BiBLE test server → `[Integration] [Post]`。3d.2（合约测试）需要 BiBLE test server（验证 API 契约）→ `[Integration] [Post]`。3d.3（CI 扩展）`[CI] [Post]`。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## Scenario

> client.py + flush + MCP + connectivity 全部就绪后，需要验证它们在一起真正工作。集成测试使用 BiBLE Atlas 内置的 test mode server——不需要 OpenSearch、Celery 或真实存储。
>
> CI 从纯 unit test 升级为 unit + contract + integration test 三层流水线。`dev.sh ci` 启动 BiBLE test server → 跑全部测试 → 停止 test server → 输出报告。

---

## Feature 逐个讨论

### F3d.1 — Integration Tests

| 属性 | 说明 |
|------|------|
| **理由** | BiBLE 通信是跨进程、跨网络的——必须通过集成测试验证。Unit test 的 mock 验证不了真实的 HTTP 交互、timeout 行为、concurrency 场景。使用 BiBLE Atlas 内置 test mode server（`uv run python -m bible.test_mode.server --port 5555`），不需要 OpenSearch/Celery。 |
| **优先级** | P0 |
| **依赖** | BiBLE test server 可用、Phase 3c 完成 |

**测试文件**：

**`tests/integration/test_client.py`**（12 个测试）：
- `test_health_check_returns_latency` — `GET /health` → 验证 `{status, latency_ms}`
- `test_import_memory_returns_task_id` — `POST /api/import/memory` → 验证 task_id
- `test_search_memory_returns_results` — `POST /api/search/memory` → 验证 `results.memory` 域键结构
- `test_search_knowledge_base_returns_results` — `POST /api/search/knowledge-base` → 验证 `results.knowledge_base` 域键结构
- `test_search_skill_returns_results` — `POST /api/search/skill` → 验证 `results.skill` 域键结构
- `test_download_memory_file_returns_task_id` — `POST /api/download/memory/file` → 验证 task_id（异步步骤 1/3）
- `test_download_skill_file_returns_task_id` — `POST /api/download/skill/file` → 验证 task_id（异步步骤 1/3）
- `test_get_task_status_polls` — `GET /api/control/admin/tasks/{id}` → 验证 status（import + download 通用）
- `test_get_download_artifact_returns_content` — `GET /api/download/{domain}/artifact/{id}` → 验证二进制流（异步步骤 3/3）
- `test_timeout_handling` — 超时 → `BibleUnreachableError`
- `test_auth_token_header` — token 设置 → 验证 `Authorization: Bearer xxx`

**`tests/integration/test_capture_flush.py`**（5 个测试）：
- `test_full_flush_pipeline` — Phase 1 detection → moment storage → session/end → flush → verify import task
- `test_flush_failure_retry` — BiBLE 不可达 → flush 失败 → moments 保持 flushed=0 → retry_count 递增
- `test_mid_session_upload` — mid_session_upload=true → Phase 1 检测后立即 flush
- `test_manual_flush_endpoint` — `POST /daemon/session/flush` → 不结束 session → moments flushed
- `test_flush_idempotent` — 同一批 moments flush 两次 → 无副作用

**`tests/integration/test_mcp_server.py`**（7 个测试）：
- 6 个 tool 端到端调用（search/save/get for memory + search for knowledge + search/get for skill）
- `test_mcp_bible_unreachable` — BiBLE test server 停止 → structured error（不 crash）

### F3d.2 — Contract Tests

| 属性 | 说明 |
|------|------|
| **理由** | BiBLE V4 API 的 request/response schema 在 02-interfaces.md §2 中定义。契约测试验证 client.py 发送的请求格式正确 + 返回的响应结构符合 spec。BiBLE Atlas 版本升级或 API 变更时，这些测试第一个报错。 |
| **优先级** | P0 — 接口契约 |
| **依赖** | client.py、BiBLE test server |

**`tests/contract/test_bible_api.py`**（10 个测试）：
- `test_import_memory_schema` — response 含 `task_id: string`
- `test_search_memory_schema` — response 含 `results.memory: [{doc_id, section_id, section_title, score, content}]`
- `test_search_knowledge_base_schema` — response 含 `results.knowledge_base: [{doc_id, section_id, section_title, score, content}]`
- `test_search_skill_schema` — response 含 `results.skill: [{doc_id, section_id, section_title, score, content}]`
- `test_download_file_schema` — `POST /api/download/memory/file` → 含 `task_id, status: "queued"`
- `test_download_artifact_schema` — `GET /api/download/{domain}/artifact/{id}` → 二进制流 + `Content-Disposition`
- `test_health_check_schema` — response 含 `status, timestamp`
- `test_error_404_schema` — 404 响应 → 含 `error: {code, message}`
- `test_get_task_status_schema` — 验证状态流转 `queued → running → completed/failed/cancelled`
- `test_search_hit_structure_invariant` — hit 必需字段（doc_id, section_id, section_title, score, content）不因 domain 变化

**`tests/contract/test_mcp_tools.py`**（4 个测试）：
- 验证 8 个 tool 全部注册（6 active + 2 postponed）
- 验证每个 tool 的 inputSchema 字段与 BiBLE API 参数一致
- 验证 MCP tool response 结构（成功 + 错误）
- 验证 tool list 无多余/缺失工具

### F3d.3 — CI Pipeline 扩展

| 属性 | 说明 |
|------|------|
| **理由** | Phase 3 首次需要 BiBLE test server——CI 必须能启动 test server、跑集成测试、然后清理。这是 CI 从纯 unit test 到 integration test 的升级点。 |
| **优先级** | P0 — CD 集成测试 |
| **依赖** | BiBLE test server（`bible.test_mode.server`） |

**`dev.sh ci` 扩展**：
```bash
ci() {
  local test_port=5555
  local test_pid=""

  # Lint
  uv run ruff check .
  uv run ruff format --check .

  # Start BiBLE test server
  echo "Starting BiBLE test server on port $test_port..."
  (cd ../BiBLE-Atlas && uv run python -m bible.test_mode.server --port $test_port) &
  test_pid=$!
  sleep 2

  # Run all tests
  uv run pytest tests/unit tests/contract tests/integration -v

  # Cleanup
  kill $test_pid 2>/dev/null
}
```

CI 失败时保留 test server 日志（写到 `$TMPDIR/bible-test-server.log`）供排查。

---

## 实现顺序

```
F3d.1 (集成测试) ──► F3d.2 (合约测试) ──► F3d.3 (CI 扩展)
```

| 顺序 | Feature | 理由 |
|------|---------|------|
| **1st** | F3d.1 集成测试 | 需要 Phase 3c 全部功能就绪。先确保功能正确 |
| **2nd** | F3d.2 合约测试 | 在 F3d.1 基础上增加 schema 验证维度 |
| **3rd** | F3d.3 CI 扩展 | 所有测试通过后，接入 CI 自动化流水线 |

---

## 验收标准

### 集成测试
- [ ] `test_client.py` 10 个测试全部通过（8 个 API + timeout + auth）
- [ ] `test_capture_flush.py` 5 个测试全部通过（flush pipeline + failure + mid_session + manual + idempotent）
- [ ] `test_mcp_server.py` 7 个测试全部通过（6 tools + unreachable）

### 合约测试
- [ ] `test_bible_api.py` 10 个测试全部通过（schema 验证 + error + invariants）
- [ ] `test_mcp_tools.py` 通过（tool 注册 + inputSchema + response schema）

### CI
- [ ] `./scripts/dev.sh ci` 完整流水线 green
- [ ] CI 失败时 test server 日志保留
- [ ] CI 在 60s 内完成（test server 启动 2s + 测试 30s + cleanup 1s）

---

## 产出文件

```
tests/integration/test_client.py         ← F3d.1
tests/integration/test_capture_flush.py  ← F3d.1
tests/integration/test_mcp_server.py     ← F3d.1
tests/contract/test_bible_api.py         ← F3d.2
tests/contract/test_mcp_tools.py         ← F3d.2
scripts/dev.sh                           ← (修改: ci 函数)
```
