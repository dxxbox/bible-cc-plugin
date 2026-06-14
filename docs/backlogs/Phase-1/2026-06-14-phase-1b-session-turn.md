# Phase 1b: Session/Turn 端点

> **依赖**: Phase 1a（SQLite schema + buffer.py CRUD）
> **被依赖**: Phase 1c（/context/inject 依赖 /session/start 提供的 recovery 数据）
> **父文档**: [Phase 1 总览](2026-06-13-phase-1-daemon-core.md)

**交付 Command**: `/bible-cc:sessions`（新增 `commands/sessions.md` + `GET /daemon/sessions` 端点）

**预估: 1.5 天**

### 测试标注

1b.1-1b.3（session/turn 端点逻辑）默认 `[Unit] [Pre]`，端点 route handler 测试需要 FastAPI TestClient → `[Integration] [Post]`。
1b.4（契约测试）默认 `[Contract] [Post]`——需要 daemon HTTP 进程。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## 2. Sub-Phase 1b: Session/Turn 端点（1.5d）

### Scenario

> 用户开始新的 Claude Code session。SessionStart hook 按三步流程执行：① `POST /daemon/start`（幂等，确保 daemon 运行）→ ② `POST /session/start`（创建 session row，扫描 unclosed sessions）→ ③ `POST /context/inject`（注入本地上下文）。
>
> 对话中，UserPromptSubmit hook 调用 `POST /turn/user`，PostToolUse hook 调用 `POST /turn/tool`。每次调用 daemon 在 <10ms 内完成 SQLite insert 并立即返回（Phase 1 detection 异步执行）。
>
> Session 结束时 Stop hook 调用 `POST /session/end`，标记 session 完成。Phase 1 不做 LLM 调用和 BiBLE flush——仅标记 status='completed'。
>
> 用户执行 `/bible-cc:sessions` 可以查看当前活跃和已完成的 session 列表。

**交付 Command**: `/bible-cc:sessions`

用户输入 `/bible-cc:sessions`，CC 执行 `commands/sessions.md` → 调 `GET /daemon/sessions`（本 Phase 新增端点，返回 sessions 表中 status='active' 和 status='completed' 的 session_id + created_at + turn_count 列表）→ 转录中展示：

```
Active sessions:
  abc123-def456  3 turns  created 10:30

Completed sessions:
  xyz789-ghi012  15 turns  completed 09:45
```

> **新增端点**: `GET /daemon/sessions` — 轻薄端点，`SELECT session_id, status, created_at, turn_count FROM sessions ORDER BY created_at DESC LIMIT 50`。本 Phase 不实现分页/过滤。

---

### Feature 1b.1: POST /session/start（含 crash recovery 快路扫描）

**Scenario**: SessionStart hook 发 `POST /session/start {session_id}` → daemon 扫描 unclosed sessions（`SELECT * FROM sessions WHERE status='active' AND session_id != ?`）→ 读取 unclosed session 的 unflushed moments（快路，同步）→ 创建新 session row → 后台启动 async task 执行 Phase 2 retrospective + flush（慢路，Phase 2 实现）→ 返回 `{session_id, is_new, recovery: {unclosed_sessions_found, moments_recovered}}`。

| 属性 | 说明 |
|------|------|
| **理由** | Session 是数据组织的基本单元。Crash recovery scan 是整个 plugin 数据可靠性的关键——如果 daemon 或 Claude Code 异常终止，Stop hook 不会触发，session 保持 active。下次 SessionStart 必须发现并恢复这些 unclosed sessions。 |
| **优先级** | P0 |
| **依赖** | 1a.2（session CRUD）、1a.4（content-hash）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/http-api.md` | §3.1 | 5 步内部流程：验证 id → crash recovery 快路 → 慢路 async → INSERT → 返回 |
| `03-daemon/http-api.md` | §3.1 Response | `{session_id, is_new: bool, recovery: {unclosed_sessions_found, moments_recovered} | null}` |
| `03-daemon/startup.md` | §1.1 Step 5 | 快路：`SELECT * FROM sessions WHERE status='active'` → 读 moments/turns |
| `03-daemon/startup.md` | §5 | SessionStart hook 三步流程：daemon/start → session/start → context/inject |
| `02-interfaces.md` | §1.2 | `/session/start` 端点 spec，recovery 字段定义 |
| `01-architecture-overview.md` | §3.3 | SessionStart 链路：recovery + injection |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| session_id 为空或缺失 | FastAPI route handler 参数验证 | HTTP 400 + `INTERNAL_ERROR` "session_id required" | `daemon.log` 搜索 `POST /session/start.*400` | 检查 hooks.json 中 SessionStart 命令是否正确传递 `$CLAUDE_SESSION_ID` |
| Crash recovery 快路 SQL 失败 | `conn.execute()` 抛异常 → catch → log error | session 仍然创建成功（is_new=true），但 recovery=null | `daemon.log` 搜索 `crash recovery scan failed` | 手动 `/bible-cc:recover` 触发重扫 |
| 慢路 async task 创建失败 | `asyncio.create_task()` 抛异常 → catch → log error | 用户无感知（Phase 1 慢路仅占位） | `daemon.log` 搜索 `failed to create recovery task` | 不需要（Phase 1 慢路无实际逻辑） |

**Function-Level Steps**（按实现顺序）:

```python
# server.py

async def start_session(request: SessionStartRequest) -> SessionStartResponse:
    """POST /session/start route handler。
    1. 验证 session_id 非空
    2. 调用 scan_unclosed_sessions() 快路扫描
    3. 调用 get_recovery_moments() 读取恢复数据
    4. 调用 insert_session() 创建新 session
    5. 慢路: asyncio.create_task(retrospective_and_flush(...)) — Phase 1 占位
    6. 返回 SessionStartResponse
    """

# buffer.py

def scan_unclosed_sessions(conn: sqlite3.Connection, current_session_id: str) -> list[str]:
    """SELECT session_id FROM sessions WHERE status='active' AND session_id != ?。
    返回 unclosed session_id 列表。
    """

def get_recovery_moments(conn: sqlite3.Connection, session_ids: list[str]) -> list[dict]:
    """SELECT * FROM moments WHERE session_id IN (...) AND flushed IN (0, -1)。
    返回 recovery moments 列表（供 /context/inject 使用）。
    """
```

**交付标准**:

- [ ] `POST /session/start` 创建新 session → 返回 `is_new=true`
- [ ] 重复调用相同 session_id → `is_new=false`
- [ ] 存在 unclosed sessions → recovery 字段含 `unclosed_sessions_found > 0`
- [ ] 无 unclosed sessions → recovery 为 null
- [ ] session_id 为空 → 400 + `SESSION_NOT_FOUND`

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_start_session_creates_new` — 新 session_id → is_new=true, recovery=null
- [ ] `test_start_session_idempotent` — 重复 session_id → is_new=false
- [ ] `test_start_session_detects_unclosed` — 先创建 active session B → 以 session A 调用 → recovery.unclosed_sessions_found=1
- [ ] `test_start_session_missing_session_id` — 空 body → 400
- [ ] `test_start_session_skips_completed_sessions` — completed session 不出现在 unclosed 结果中

*意图测试*:
- [ ] `test_crash_recovery_does_not_include_current_session` — **意图: 数据隔离**。Crash recovery 扫描时排除当前 session_id，否则新建的 session 会把自己当作"recovery 源"，造成循环引用。验证方法：`scan_unclosed_sessions(conn, "current")` 中 "current" 刚被 insert（active），但不应出现在结果中。
- [ ] `test_session_start_fast_path_is_synchronous` — **意图: 快路不阻塞启动**。快路 SQLite 读取必须在 sync context 中完成（毫秒级），返回响应前恢复数据已就绪。慢路（LLM+flush）才走 async。验证方法：hook 脚本调用 `/session/start` 的 latency < 100ms（不含网络）。

---

### Feature 1b.2: POST /session/end

**Scenario**: Stop hook 发 `POST /session/end {session_id}`。Phase 1 的实现极简——仅验证 session 存在且 active，然后 `UPDATE sessions SET status='completed', closed_at=datetime('now') WHERE session_id=?`，返回 `{moments_flushed: 0, status: "completed"}`。Phase 2/3 才加入 LLM retrospective + flush。

| 属性 | 说明 |
|------|------|
| **理由** | Session 生命周期必须有明确的结束标记（crash recovery 依赖 `status='active'` 来发现异常终止）。Phase 1 不做 LLM 或 BiBLE 调用——这是设计决策。 |
| **优先级** | P0 |
| **依赖** | 1a.2（mark_session_completed CRUD）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/http-api.md` | §3.2 | Phase 1 response: `{session_id, moments_flushed: 0, status: "completed"}` |
| `03-daemon/http-api.md` | §3.2 错误 | session 已 completed → 返回 `status: "already_completed"` |
| `02-interfaces.md` | §1.2 | `/session/end` 端点 spec |
| `03-daemon/http-api.md` | §7 时序约束 | `/session/end` 预计 ~20s（Phase 2+3），Phase 1 仅 <50ms |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| session_id 不存在 | `SELECT` 返回空 → 404 | HTTP 404 + `SESSION_NOT_FOUND` | `daemon.log` 搜索 `POST /session/end.*404` | 可能是 session 已过期或被清理——无害 |
| session 已 completed | `status != 'active'` → 返回 200 | Response 含 `status: "already_completed"` | `daemon.log` 搜索 `session/end.*already_completed` | 无害，幂等。可能是 Stop hook 被重复调用 |

**Function-Level Steps**（按实现顺序）:

```python
# server.py

async def end_session(request: SessionEndRequest) -> SessionEndResponse:
    """POST /session/end route handler。
    1. 验证 session_id 非空
    2. 调用 get_session() 检查 session 存在
    3. 若 status != 'active' → 返回 {status: "already_completed"}
    4. 调用 mark_session_completed() 标记完成
    5. Phase 1: moments_flushed=0（无 flush）
    6. 返回 SessionEndResponse
    """
```

**交付标准**:

- [ ] Active session → status='completed', closed_at 非空
- [ ] 已 completed session → 返回 `status: "already_completed"`（非错误）
- [ ] 不存在 session → 404 `SESSION_NOT_FOUND`
- [ ] `moments_flushed` 固定为 0（Phase 1）

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_end_session_marks_completed` — active session → end → status='completed'
- [ ] `test_end_session_already_completed_returns_gracefully` — completed session → 200, status="already_completed"
- [ ] `test_end_session_unknown_returns_404` — 不存在的 session → 404
- [ ] `test_end_session_missing_session_id_returns_400` — 空 body → 400

*意图测试*:
- [ ] `test_phase1_end_session_does_no_llm_call` — **意图: Phase 边界清晰**。Phase 1 的 `/session/end` 不应有 Anthropic SDK import 或 BiBLE client import。验证方法：检查 server.py 中 end_session 函数的 import 列表不含 `anthropic` 或 `client`。
- [ ] `test_stop_hook_failure_does_not_block_claude_code_shutdown` — **意图: 永不阻塞 Claude Code**。Stop hook timeout 30s 后 hook 被 kill，Claude Code 正常退出。如果 daemon 在 30s 内未响应，数据留在 SQLite——下次 SessionStart crash recovery 处理。

---

### Feature 1b.3: POST /turn/user + POST /turn/tool

**Scenario**: 用户输入消息 → UserPromptSubmit hook 调用 `POST /turn/user {session_id, message}` → daemon 分配 seq、INSERT turn、更新 turn_count → <10ms 返回 `{turn_id, queued: true}`。类似地，模型调用工具后 PostToolUse hook 调用 `POST /turn/tool {session_id, tool_name, arguments, output}` → 完整 tool output 存入 SQLite（不截断）→ <10ms 返回。

| 属性 | 说明 |
|------|------|
| **理由** | `/turn/user` 和 `/turn/tool` 是数据流入链路的第一站。CLAUDE.md 硬性约束：必须立即返回（<100ms），Phase 1 detection 是异步队列任务。PostToolUse hook 传入的 tool output 完整存入 turns 表——不做机械截断，LLM 在 detection 阶段自己提取精华。 |
| **优先级** | P0 |
| **依赖** | 1a.2（turn CRUD）、1a.1（seq 计数器）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/http-api.md` | §4.1 /turn/user | `{turn_id, queued}`，<10ms 返回，seq 分配，阈值检查后 queue detection |
| `03-daemon/http-api.md` | §4.2 /turn/tool | tool_output 完整存储，无机械截断，LLM 提取精华 |
| `03-daemon/http-api.md` | §7 时序约束 | `/turn/user` <10ms, `/turn/tool` <10ms，不阻塞 |
| `03-daemon/http-api.md` | §4.1 内部流程 Step 2 | seq 分配：内存计数器 `session_seq[session_id] += 1` |
| `02-interfaces.md` | §1.3 | Turn 端点 request/response schema |
| `02-interfaces.md` | §4.4 环境变量 | `$TOOL_OUTPUT`（非 `$TOOL_RESULT`）|

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| session 不存在或已 completed | get_session() 返回 None 或 status≠'active' | HTTP 400 + `SESSION_NOT_FOUND` | `daemon.log` 搜索 `POST /turn/user.*SESSION_NOT_FOUND` | 确认 SessionStart hook 已触发。可能是 daemon 中途重启丢失内存状态 |
| seq UNIQUE 约束冲突 | `sqlite3.IntegrityError` on `UNIQUE(session_id, seq)` | 500 error | `daemon.log` 搜索 `UNIQUE constraint failed: turns.session_id, turns.seq` | 检查 seq 计数器恢复逻辑（1c.3） |
| tool_output 超长（> SQLite 限制） | SQLite 默认 max 约 1e9 bytes → 很少触发。若触发：`sqlite3.OperationalError` | 500 error | `daemon.log` 搜索 `tool_output too large` | 降级方案：超过 100KB 时存储 `<TRUNCATED: N bytes>` 标记 |

**Function-Level Steps**（按实现顺序）:

```python
# server.py

async def turn_user(request: TurnUserRequest) -> TurnResponse:
    """POST /turn/user route handler。
    1. 验证 session_id → 调用 get_session() 确认存在且 active
    2. 调用 get_next_seq() 分配 turn 序号
    3. 调用 insert_turn_user() 写入 SQLite
    4. 调用 increment_turn_count() 更新计数器
    5. Phase 1: 跳过阈值检查和 detection queue（Phase 2 实现）
    6. 返回 {turn_id, queued: false}  # Phase 1 无 detection → queued=false
    """

async def turn_tool(request: TurnToolRequest) -> TurnResponse:
    """POST /turn/tool route handler。
    1-2. 同上验证 session_id + 分配 seq
    3. 调用 insert_turn_tool() 写入 SQLite（完整 output，含 tool_arguments JSON 序列化）
    4. 调用 increment_turn_count()
    5. Phase 1: 跳过阈值检查
    6. 返回 {turn_id, queued: false}
    """
```

**交付标准**:

- [ ] `POST /turn/user` 写入 turns 表，role='user'，seq 递增
- [ ] `POST /turn/tool` 写入 turns 表，role='assistant'，含 tool_name/arguments/output
- [ ] tool_output 完整存储（验证 10000 字符原样）
- [ ] 两个端点返回时间 <50ms（不含网络）
- [ ] session 不存在 → 400 `SESSION_NOT_FOUND`
- [ ] session 已 completed → 400（拒绝写入）

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_turn_user_creates_turn` — POST → turn 写入 → 返回 turn_id=1
- [ ] `test_turn_user_seq_increments` — 3 次 user turn → seq 依次为 1,2,3
- [ ] `test_turn_tool_stores_full_output` — 10000 char output → 存入后读出完全一致
- [ ] `test_turn_user_unknown_session_returns_400` — 不存在的 session_id → 400
- [ ] `test_turn_user_completed_session_returns_400` — 已 end 的 session → 400
- [ ] `test_turn_count_incremented_on_turn` — turn 写入后 session.turn_count +1

*意图测试*:
- [ ] `test_turn_endpoints_return_within_50ms` — **意图: 不阻塞 Claude Code**。UserPromptSubmit 和 PostToolUse hooks 的超时是 3s。如果 daemon 的 turn 端点在 50ms 内返回，hook 有 2.95s 的余量应对网络延迟。如果 turn 端点因为做了同步 LLM 调用而耗时 2s，hook 在 3s 超时边界刚好完成——任何网络抖动就会超时，导致 hook 跳过。
- [ ] `test_turn_tool_uses_tool_output_not_tool_result` — **意图: 环境变量正确性**。Claude Code 的 PostToolUse hook 环境变量是 `$TOOL_OUTPUT` 而非 `$TOOL_RESULT`。如果 hook 脚本用错变量名，tool output 会是空字符串——所有 tool turn 丢失关键上下文。验证方法：hook.py 中的 `turn-tool` 命令检查参数名 `--output` 对应的变量。

---

### Feature 1b.4: 15 端点契约测试全覆盖

**Scenario**: 实现 Phase 1 所有 HTTP 端点后（1b.1-1b.3），针对每个端点编写 1 个契约测试——验证 response JSON 结构与 `02-interfaces.md` 定义的 schema 一致（status code、必需字段、字段类型）。契约测试不验证业务逻辑（那是单元测试的职责），只验证接口协议。

| 属性 | 说明 |
|------|------|
| **理由** | 契约测试是跨组件接口的保证。Hook 脚本、Commands、外部监控都依赖 daemon HTTP API 的稳定。如果 endpoint 返回字段名改了但契约测试没报，调用方会在生产环境 break。 |
| **优先级** | P0 — 接口契约 |
| **依赖** | 1b.1-1b.3（HTTP 端点已实现）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `02-interfaces.md` | §1.1-1.4 | 所有端点 request/response schema |
| `02-interfaces.md` | §1.6 错误格式 | `{error: {code, message}}`，HTTP status codes |
| `03-daemon/http-api.md` | §2-5 | 每个端点的完整 Response JSON 结构 |
| `11-testing/unit.md` | §3.7 | Hook 和 Command helpers 测试 |
| `README.md` | §0 TDD | "跨组件接口必须有独立的契约测试" |

**Troubleshooting 设计**: 契约测试本身是排障工具——当契约测试 FAIL 时，说明接口协议被打破，需要检查 `02-interfaces.md` 是否更新。

**Function-Level Steps**（按实现顺序）:

```python
# tests/contract/test_daemon_api.py

# Step 1: 契约测试 fixture — 启动 daemon test client
@pytest.fixture
async def daemon_client():
    """用 FastAPI TestClient 或 httpx.AsyncClient 连接测试 daemon 实例。"""

# Step 2: 逐端点测试
async def test_contract_daemon_start_response_schema():
    """POST /daemon/start → 验证 response keys: pid(int), port(int), status(str)"""

async def test_contract_daemon_health_response_schema():
    """GET /daemon/health → 验证所有 health 字段存在 + 类型正确"""

async def test_contract_session_start_response_schema():
    """POST /session/start → 验证 session_id, is_new, recovery 结构"""

async def test_contract_session_end_response_schema():
    """POST /session/end → 验证 session_id, moments_flushed, status"""

async def test_contract_turn_user_response_schema():
    """POST /turn/user → 验证 turn_id(int), queued(bool)"""

async def test_contract_turn_tool_response_schema():
    """POST /turn/tool → 验证 turn_id(int), queued(bool)"""

async def test_contract_context_inject_response_schema():
    """POST /context/inject → 验证 context(str), sources{turns, moments, crash_recovery}"""

async def test_contract_error_response_format():
    """非法 body → 422/400 → 验证 error.code, error.message 字段"""
```

**交付标准**:

- [ ] 每端点 ≥ 1 个 contract test case
- [ ] 错误 case：非法 body → 验证 error response 结构
- [ ] 所有 contract tests 在 CI 中运行（`dev.sh ci`）

**测试用例**（实现后编写——Contract tests 需要 daemon HTTP 进程）:

> 按约定，本 feature 所有测试默认 `[Contract] [Post]`。

*功能测试*:
- [ ] `test_contract_start_returns_required_fields` — response 含 pid, port, status
- [ ] `test_contract_health_has_all_fields` — 验证 health JSON 的 8 个顶层 key
- [ ] `test_contract_invalid_body_returns_structured_error` — 缺少 session_id → 422/400 + error.code + error.message

*意图测试*:
- [ ] `test_contract_tests_do_not_import_buffer_directly` — **意图: 契约测试是黑盒**。Contract tests 只通过 HTTP 调用 daemon，不 import buffer.py 或直接操作 SQLite。
- [ ] `test_all_phase1_endpoints_have_contract_test` — **意图: 零遗漏**。Phase 1 的 7 个端点每个都有对应的 contract test。

---

