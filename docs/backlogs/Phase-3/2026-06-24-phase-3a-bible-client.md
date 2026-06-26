# Phase 3a: BiBLE HTTP Client（client.py）

> **依赖**: Phase 2（config.py 的 bible 域、types.py）
> **被依赖**: Phase 3b（flush + MCP 真实调用）
> **父文档**: [Phase 3 总览](../plans/2026-06-13-phase-3-bible-integration.md)

**交付 Command**: 无新 command。client.py 是被调用的库，对用户透明。

**预估: 1.5 天**

### 测试标注

3a 全部 `[Unit] [Pre]`（使用 `pytest-httpx` mock 所有 BiBLE API 响应）。不依赖 BiBLE test server——mock 层面验证 request/response 正确性。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## Scenario

> client.py 是所有 BiBLE Atlas 通信的**唯一通道**。Daemon 和 MCP server 都通过它调用 BiBLE V4 API——不得各自实现 HTTP 调用。
>
> 封装 9 个 API 调用：import memory、search memory/knowledge/skill、request download memory/skill、task status、artifact download、health check。每个调用统一处理认证（Bearer token）、超时（默认 30s）、错误分类（4xx → BiBLEError、5xx/timeout/network error → BibleUnreachableError）。
>
> 每次 API 调用输出 `[bible:req]` 追踪日志——method、URL、status、latency、result summary。这是排查跨网络故障的基础设施。

---

## Feature 逐个讨论

### F3a.1 — BiBLE HTTP Client 核心

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束——BiBLE HTTP client 是唯一实现。daemon 和 MCP server 共用同一个 client，不得重复。client 封装了所有 BiBLE V4 API 调用、认证、错误处理、超时。这是与 BiBLE Atlas 服务的唯一通信通道。 |
| **优先级** | P0 — Phase 3/4 基础设施 |
| **依赖** | config.py（`bible.base_url`, `bible.token`, `bible.kb_index`）、types.py |

封装的 API 调用：

| 方法 | HTTP | 用途 |
|------|------|------|
| `check_health()` | `GET /health` | 连通性检查，返回 `HealthResult(reachable: bool, latency_ms: float)` |
| `import_memory(files, kb_index, tag="memory", parser_script=None, vector_model=None, parser_context=None)` | `POST /api/import/memory` | 导入 memory（multipart: `files[]`, `kb_index`, `tag="memory"`）。`files` 为 `list[tuple[str, bytes, str]]`（filename, content, content_type）。序列化责任在调用方（daemon flush 层 / MCP adapter 层）。返回 `{"task_id": "abc-123"}` |
| `search_memory(query, tag="memory", top_k=None, search_type=None, kb_index=None, vector_model=None, vector_weight=None)` | `POST /api/search/memory` | 搜索记忆，返回 `results.memory` 域键结果 |
| `search_knowledge_base(query, tag, top_k=None, search_type=None, kb_index=None, vector_model=None, vector_weight=None)` | `POST /api/search/knowledge-base` | 搜索知识库。`tag` 必填（无默认值——KNOWLEDGE_BASE 的 tag 为用户自定义，如 `design`/`flow`/`alg`） |
| `search_skill(query, tag="skill", top_k=None, search_type=None, kb_index=None, vector_model=None, vector_weight=None)` | `POST /api/search/skill` | 搜索技能，返回 `results.skill` 域键结果 |
| `request_memory_download(storage_path, tag="memory")` | `POST /api/download/memory/file` | 提交 memory 下载任务（异步步骤 1/3），返回 `{"task_id": "..."}`。调用方需 poll `get_task_status()` 后 `get_download_artifact()` |
| `request_skill_download(storage_path, tag="skill")` | `POST /api/download/skill/file` | 提交 skill 下载任务（异步步骤 1/3），返回 `{"task_id": "..."}`。调用方需 poll `get_task_status()` 后 `get_download_artifact()` |
| `get_task_status(task_id)` | `GET /api/control/admin/tasks/{task_id}` | 轮询异步 import/download 任务状态（步骤 2/3） |
| `get_download_artifact(domain, artifact_id)` | `GET /api/download/{domain}/artifact/{artifact_id}` | 获取下载产物二进制流（步骤 3/3） |

### F3a.2 — 错误处理 + 追踪日志

| 属性 | 说明 |
|------|------|
| **理由** | BiBLE 通信是跨网络的——必须区分"BiBLE 拒绝了请求"（4xx）和"BiBLE 不可达"（5xx/网络错误）。两类错误的行为不同：4xx 是配置错误不应重试，5xx 是临时故障可稍后重试。追踪日志是排查网络问题的唯一手段。 |
| **优先级** | P0 |

**错误类型**：
- `BiBLEError(code, message)` — 4xx 响应（auth error、bad request、not found）
- `BibleUnreachableError(message)` — 5xx/timeout/network error，可重试的临时故障

**追踪日志规范**（每次 API 调用通过 Python `logger` 输出，不 `print`）：

- **记录**：method、path、status、latency、result summary（如 `task_id`、`total`）
- **禁止记录**：token/Authorization header、完整 query string、完整 request/response body
- 使用 `logger.info()` 记录成功/正常响应，`logger.error()` 记录失败

```
[bible:req] POST /api/import/memory → 202 (1.2s) task_id=abc-def-123
[bible:req] POST /api/search/memory → 200 (0.3s) total=5
[bible:req] POST /api/search/knowledge-base → 200 (0.3s) total=3
[bible:req] GET /health → 200 (0.05s)
[bible:req] POST /api/import/memory → ERROR timeout (30.0s) → BibleUnreachableError
```

---

## 实现顺序

```
F3a.1 (client 核心) ──► F3a.2 (错误处理 + 追踪日志)
```

| 顺序 | Feature | 理由 |
|------|---------|------|
| **1st** | F3a.1 client 核心 | 独立——只依赖 config + httpx。所有 8 个 API 方法 + 认证逻辑 |
| **2nd** | F3a.2 错误处理 + 日志 | 依赖 F3a.1。错误分类、追踪日志格式 |

---

## 设计要点

### 传输层
- `httpx.AsyncClient`（异步）。daemon 运行在 FastAPI async event loop 上，且 `/daemon/consult` 需并行调用三个域 search 端点——同步 client 会阻塞 event loop，async client 通过 `asyncio.gather` 实现零成本并发。MCP server 或测试等同步调用方用 `asyncio.run()` 包装单次 async 调用即可。

### 认证
```python
headers = {}
if self.token:
    headers["Authorization"] = f"Bearer {self.token}"
```
token 为空时不发送 Authorization header（test mode server 不需要认证）

### 生命周期

`httpx.AsyncClient` 是长连接池，必须有明确的创建/释放约定：

| 调用方 | 创建 | 释放 | 说明 |
|--------|------|------|------|
| Daemon | FastAPI `lifespan` startup，存入 `app.state.client` | `lifespan` shutdown 调用 `await client.aclose()` | 整个 daemon 生命周期复用同一个 client |
| MCP server | `mcp/server.py` 启动时创建 | 进程退出时 `finally: await client.aclose()` | daemon 和 MCP 是独立进程，各自持有自己的 client，不共享 |
| 集成测试 | `async with BiBLEClient(...) as client:` | context manager 自动关闭 | 每个测试独立 client，避免跨测试连接泄漏 |
| 单元测试 | 不需要真实 client | — | 使用 `pytest-httpx` mock，不创建真实连接 |

### 错误类型

`BiBLEError` 和 `BibleUnreachableError` 定义在 `client.py` 中（与 httpx 紧密相关，无需单独 exceptions 模块）。同时定义 `HealthResult` dataclass：

```python
from dataclasses import dataclass

class BiBLEError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"[{code}] {message}")

class BibleUnreachableError(Exception):
    def __init__(self, message: str, original_error: Exception | None = None):
        self.original_error = original_error
        super().__init__(message)

@dataclass
class HealthResult:
    reachable: bool
    latency_ms: float
```

### Async Context Manager 协议

`BiBLEClient` 实现 `__aenter__` / `__aexit__` 协议，`__aexit__` 调用 `self.aclose()`。同时保留独立的 `aclose()` 方法供非 context manager 场景（MCP server 的 try/finally）：

```python
async def __aenter__(self) -> "BiBLEClient":
    return self

async def __aexit__(self, *args) -> None:
    await self.aclose()

async def aclose(self) -> None:
    if self._client is not None:
        await self._client.aclose()
        self._client = None
```

### 超时
- 默认 30s，构造时从 config 读取
- `httpx.Timeout(connect=10, read=30, write=10, pool=10)`

### 配置来源
```python
from bible_cc_plugin.config import load_config
config = load_config()
client = BiBLEClient(base_url=config.bible.base_url, token=config.bible.token, kb_index=config.bible.kb_index)
```

---

## 验收标准

- [ ] `uv run pytest tests/unit/test_client.py -v` — 9 个 API 全部有 unit test 覆盖
- [ ] ⚠️ unit mock 验证请求 payload 和 endpoint 与 `docs/sw-design/02-interfaces.md` 一致——mock 通过不代表真实 API 兼容，Phase 3d contract test 负责兜底
- [ ] 正常返回 → 验证 response 解析正确（task_id、total、domain-keyed results 结构、latency_ms）
- [ ] download 异步流程完整：file → task_id → poll task status → get artifact
- [ ] search 方法签名包含 `query` + `tag`（必填）+ 可选 `top_k`/`search_type`/`kb_index`/`vector_model`/`vector_weight`
- [ ] 端点、请求体、响应解析必须对齐 BiBLE Atlas V4 API 文档（`BiBLE-Atlas/docs/designs/server_part/v4/02_API接口文档.md`），`docs/sw-design/02-interfaces.md` §2 为其摘要镜像
- [ ] 4xx 响应 → 抛出 `BiBLEError`，包含 code + message
- [ ] 5xx 响应 / timeout / 网络错误 → 抛出 `BibleUnreachableError`
- [ ] connect timeout（10s）→ `BibleUnreachableError`（非静默吞掉）
- [ ] read timeout（30s）→ `BibleUnreachableError`（非静默吞掉）
- [ ] 200 响应但 body 非合法 JSON → 不应静默吞掉，抛 `BiBLEError` 或 `BibleUnreachableError`
- [ ] token=null 时请求不包含 Authorization header
- [ ] 每次 API 调用输出 `[bible:req]` 追踪日志
- [ ] client 构造始终成功——`base_url` 来自 `config.bible.base_url`（默认 `http://localhost:5555`），无需额外校验；BiBLE 连通性是运行时问题，由 `BibleUnreachableError` 表达
- [ ] client 生命周期正确：daemon 通过 FastAPI lifespan 管理、MCP server 独立创建/释放、测试用 `async with` 或 `pytest-httpx` mock

---

## 产出文件

```
src/bible_cc_plugin/daemon/client.py     ← F3a.1, F3a.2
tests/unit/test_client.py                ← F3a.1, F3a.2
```
