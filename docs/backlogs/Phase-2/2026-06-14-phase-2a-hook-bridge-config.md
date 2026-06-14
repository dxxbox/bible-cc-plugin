# Phase 2a: Hook Bridge 补全 + Config 扩展

> **依赖**: Phase 1d（完整 daemon HTTP API + SQLite 数据层）
> **被依赖**: Phase 2b（detection 需要 hook 喂数据进 buffer + 需要 config capture 域）
> **父文档**: [Phase 2 总览](../plans/2026-06-13-phase-2-capture-pipeline.md)

**交付 Command**: `/bible-cc:sessions`、`/bible-cc:context`（从 Phase 1 占位落地为可工作 slash command）

**预估: 1.5 天**

### 测试标注

2a.1（hook action 补全）默认 `[Unit] [Pre]`（mock httpx）。2a.2（config 调整）默认 `[Unit] [Pre]`。2a.3（daemon_launcher 重构）默认 `[Unit] [Pre]`。2a.4（command 文件 + GET /daemon/moments + 契约测试）中端点测试 `[Integration] [Post]`，契约测试 `[Contract] [Post]`。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## 1. Sub-Phase 2a: Hook Bridge + Config（1.5d）

### Scenario

> Phase 1 已完成所有 daemon HTTP 端点（15 个），但 hook.py 仍是 Phase 0 的骨架——`session-start` 仅自启动 daemon，`turn-user`/`turn-tool`/`session-end` 全是空 pass。数据流入链路实际上断了：hook 事件触发但 turn 数据从未进入 buffer。
>
> 本阶段补全 hook.py 的四个 action + 更新 hooks.json 传入 CC 环境变量，打通 Claude Code 生命周期事件 → daemon HTTP API → SQLite 的数据流入链路。同时调整 config.py 中已有的 `CaptureConfig`/`DetectionConfig` 使其与 SW design 一致。一并解决 Phase 0 留下的技术债：`hook.py` 和 `daemon.py` 中 subprocess spawning 的重复代码。
>
> 新增 `GET /daemon/moments?session_id=X` 端点（从 2c 提前至 2a），因为 2b detection 产生的 moment 需要 hook 在 2b 就能通过此端点读取并格式化为 hint 输出到 transcript。

**交付 Command**: `/bible-cc:sessions`、`/bible-cc:context`

Phase 1 的 `commands/sessions.md` 和 `commands/context.md` 尚未创建（仅 `status.md` 在 1a 中落地）。2a 将这两个 command 文件创建为可工作的 slash command。

---

### 实现顺序

```
2a.2 (config) → 2a.3 (daemon_launcher) → 2a.1 (hook actions) → 2a.4 (commands + tests)
```

| 顺序 | Feature | 理由 | 可并行 |
|------|---------|------|--------|
| **1st** | 2a.2 Config 调整 | 完全独立——只改 `config.py`，3 项调整 | ✅ 可与 2a.3 并行 |
| **2nd** | 2a.3 daemon_launcher | 从已有代码提取共享函数（重构先行）。2a.1 的 session-start 第 1 步就调用 `ensure_daemon_started()` | ✅ 可与 2a.2 并行 |
| **3rd** | 2a.1 Hook + hooks.json | 核心——4 个 action 补全。session-start 直接使用 2a.3 的共享函数 | — |
| **4th** | 2a.4 Commands + GET /moments + 契约测试 | 验证层——依赖 2a.1 的 hook 实现。契约测试确认 hook↔daemon 交互正确 | — |

2a.2 和 2a.3 互相独立，可并行开发。2a.1 必须等 2a.3 完成（session-start 调用 `ensure_daemon_started()`）。2a.4 必须最后（契约测试验证完整链路）。

---

### Feature 2a.1: Hook 四个 action 全面补全 + hooks.json 更新

**Scenario**: 同时更新两个文件——

**hooks.json 侧**：四个 hook command 模板传入 CC 环境变量（与 feasibility report L506-519 一致）。

```json
SessionStart:       "hook session-start --session-id \"$CLAUDE_SESSION_ID\""      (inject: true)
UserPromptSubmit:   "hook turn-user --session-id \"$CLAUDE_SESSION_ID\" --message \"$USER_PROMPT\""
PostToolUse:        "hook turn-tool --session-id \"$CLAUDE_SESSION_ID\" --tool \"$CLAUDE_TOOL_NAME\" --input \"$CLAUDE_TOOL_INPUT\" --output \"$CLAUDE_TOOL_OUTPUT\""
Stop:               "hook session-end --session-id \"$CLAUDE_SESSION_ID\""
```

**hook.py 侧**：argparse 扩展接收 `--session-id`、`--message`、`--tool`、`--output` 参数。四个 action 的 handler 函数为同步（`httpx.Client`），不在 hook 脚本中使用 asyncio。

| 属性 | 说明 |
|------|------|
| **理由** | Hook 脚本是 Claude Code 生命周期事件到 daemon 的唯一桥梁。当前 hook.py（Phase 0）只有 session-start 做了 daemon 自启动，其余 action 空 pass——所有 Phase 1 HTTP 端点从未被 hook 触发过。Graceful degradation 是硬性约束——UserPromptSubmit/PostToolUse hook 在 daemon 不可达时必须静默跳过（exit code 0）。 |
| **优先级** | P0 — 数据入口，Phase 2b 的前置 |
| **依赖** | Phase 1d server.py（全部 15 端点）、Phase 0 hook.py（已有骨架）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `feasibility-report.md` | L506-519 | hooks.json 完整 sketch：四个 action 的 command 模板 + `inject: true` |
| `02-interfaces.md` | §2 Hook Conventions | 四个 action 的调用约定、环境变量、stdout 输出格式 |
| `02-interfaces.md` | §1.2-1.4 | `/session/start`、`/turn/user`、`/turn/tool`、`/context/inject`、`/session/end` 的 request/response schema |
| `05-capture/hook-flow.md` | §1-2 | `/turn/user` 和 `/turn/tool` 的完整处理流程 |
| `03-daemon/startup.md` | §5 | SessionStart hook 三步流程：daemon/start → session/start → context/inject |
| `01-architecture-overview.md` | §5 硬性约束 | "Hook 失败必须静默跳过，不阻塞 Claude Code" |
| CC Docs | Hooks Reference | PostToolUse: `CLAUDE_TOOL_NAME`、`CLAUDE_TOOL_INPUT`（JSON 字符串）、`CLAUDE_TOOL_OUTPUT` |
| ⚠️ 验证风险 | — | `$CLAUDE_SESSION_ID` 和 `$USER_PROMPT` 未通过 CC 官方文档确认（搜索结果提到 `SESSION_ID` 无 `CLAUDE_` 前缀）。实施前需用 CC 真实环境验证变量名。 |
**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Daemon 不在且启动失败 | `ensure_daemon_started()` timeout → tail log → stderr WARN | SessionStart hook stdout error hint | `daemon.log` 搜索 `FATAL` | 检查 `daemon.log` → 修复启动问题 → 下次 SessionStart 自动重试 |
| Daemon 运行但 `/session/start` 返回 500 | HTTP 500 → 解析 error body → stderr WARN | hook exit 0，session 未创建 | `daemon.log` 搜索 `POST /session/start.*500` | 检查 `daemon.log` 中 500 根因 |
| turn-user/tool 时 daemon 不可达 | `httpx.ConnectError` → stderr WARN → exit 0 | 静默跳过——turn 数据丢失但 Claude Code 不阻塞 | hook stderr `[hook:turn-user] daemon unreachable` | 检查 daemon 是否仍运行 → 下次 SessionStart 恢复 |
| `$CLAUDE_TOOL_OUTPUT` 为空 | PostToolUse hook 未正确传递环境变量 | turn 写入但 tool_output 为空字符串 | `daemon.log` 搜索 `turn/tool.*output_len=0` | 检查 hooks.json 中 PostToolUse command 是否正确使用 `$CLAUDE_TOOL_OUTPUT` |
| SessionStart hook 超时（>60s） | CC kill hook 进程 | session 未注册，context 未注入 | hook 无机会写日志（被 kill） | 确保 daemon 在 5s 内启动 |

**Function-Level Steps**（按实现顺序）:

```python
# scripts/hook.py — 同步实现（httpx.Client）

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["session-start", "turn-user", "turn-tool", "session-end"])
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--message", default=None)
    parser.add_argument("--tool", default=None)
    parser.add_argument("--input", default=None)    # CLAUDE_TOOL_INPUT JSON string
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    config = load_config()
    if args.action == "session-start":
        _handle_session_start(config, args)
    elif args.action == "turn-user":
        _handle_turn_user(config, args)
    elif args.action == "turn-tool":
        _handle_turn_tool(config, args)
    elif args.action == "session-end":
        _handle_session_end(config, args)

# Step 1: session-start（扩展已有实现）
def _handle_session_start(config: AppConfig, args) -> None:
    """三步流程：ensure daemon → register session → inject context → stdout。
    1. ensure_daemon_started(config.daemon.port)  # 改用 daemon_launcher.py（见 2a.3）
    2. POST /session/start {session_id: args.session_id}
    3. 如果 is_new=false 且 recovery 存在 → 记录日志
    4. POST /context/inject {session_id: args.session_id, user_message: args.message or ""}
    5. 如果 context 非空 → print(context)  # stdout → CC inject（依赖 hooks.json inject:true）
    """

# Step 2: turn-user
def _handle_turn_user(config: AppConfig, args) -> None:
    """POST /turn/user {session_id, message}。
    1. 验证 args.session_id 非空（空则 stderr WARN → exit 0）
    2. POST /turn/user
    3. 连接失败 → stderr WARN → exit 0（graceful degradation）
    """

# Step 3: turn-tool
def _handle_turn_tool(config: AppConfig, args) -> None:
    """POST /turn/tool {session_id, tool_name, arguments, output}。
    1. 验证 args.session_id + args.tool 非空
    2. args.input 是 CLAUDE_TOOL_INPUT 的 JSON 字符串 → json.loads() → arguments dict
       （json.loads 异常 → fallback {}，工具无参数时 input 可能为空或 None）
    3. POST /turn/tool（完整 tool_output 传入，不截断）
    4. 连接失败 → stderr WARN → exit 0
    """

# Step 4: session-end
def _handle_session_end(config: AppConfig, args) -> None:
    """POST /session/end {session_id}。
    1. 验证 args.session_id 非空
    2. POST /session/end
    3. 连接失败 → stderr WARN → exit 0
    """

# Step 5: stderr 追踪日志（每个 action 写入）
# [hook:session-start] daemon health check... OK
# [hook:session-start] POST /session/start... OK (is_new=true, recovery=null)
# [hook:session-start] POST /context/inject... OK (branch=empty)
# [hook:session-start] DONE (total=340ms)
# [hook:turn-user] session=abc123, message_len=340 → OK (turn_id=7)
# [hook:turn-tool] session=abc123, tool=read_file, output_len=2800 → OK
# [hook:session-end] POST /session/end... OK (status=completed)
```

**交付标准**:

- [ ] `hooks.json` 四个 hook command 全部传入对应 CC 环境变量（与 feasibility report 一致）
- [ ] `hooks.json` UserPromptSubmit 和 PostToolUse timeout 从 10s 缩至 3000ms（turn 写入 <50ms）
- [ ] SessionStart hook 含 `inject: true`（`/context/inject` 的 stdout 注入 system prompt）
- [ ] hook.py argparse 接受 `--session-id`、`--message`、`--tool`、`--input`、`--output` 参数
- [ ] `session-start` action：完整三步流程（ensure daemon → register session → inject context）
- [ ] `turn-user` action：POST /turn/user + stderr 追踪日志
- [ ] `turn-tool` action：POST /turn/tool（含 `$CLAUDE_TOOL_OUTPUT`）+ stderr 追踪日志
- [ ] `session-end` action：POST /session/end
- [ ] 所有 action daemon 不可达时 exit code 0（graceful degradation）
- [ ] 每个 action 输出 stderr 追踪日志（action + endpoint + status + duration）
- [ ] 每个 action 失败时 stderr 输出详细错误原因
- [ ] PostToolUse hook 使用正确的 CC 环境变量（`CLAUDE_TOOL_NAME`、`CLAUDE_TOOL_INPUT`、`CLAUDE_TOOL_OUTPUT`）

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_session_start_calls_session_start_endpoint` — mock httpx → 验证 POST /session/start 被调用
- [ ] `[Unit] [Pre]` `test_session_start_calls_context_inject_endpoint` — mock httpx → 验证 POST /context/inject 被调用
- [ ] `[Unit] [Pre]` `test_turn_user_calls_turn_endpoint` — mock httpx → 验证 POST /turn/user 被调用 + body 含 session_id/message
- [ ] `[Unit] [Pre]` `test_turn_tool_sends_full_output` — mock httpx → 验证 tool_output 在 body 中完整传递
- [ ] `[Unit] [Pre]` `test_turn_user_graceful_skip_when_daemon_unreachable` — mock ConnectError → exit code 0
- [ ] `[Unit] [Pre]` `test_turn_tool_graceful_skip_when_daemon_unreachable` — 同上
- [ ] `[Unit] [Pre]` `test_session_end_graceful_skip_when_daemon_unreachable` — 同上
- [ ] `[Contract] [Post]` `test_hook_session_start_creates_session` — 启动 daemon → 调 hook session-start → curl GET /daemon/sessions 确认 session 存在
- [ ] `[Contract] [Post]` `test_hook_turn_user_buffers_turn` — session-start 后 → hook turn-user → curl debug/turns 确认 turn 存在

*意图测试*:
- [ ] `[Unit] [Pre]` `test_turn_tool_uses_correct_cc_env_vars` — **意图: 环境变量正确性**。验证 hook.py 中 turn-tool action 从 `CLAUDE_TOOL_NAME`、`CLAUDE_TOOL_INPUT`、`CLAUDE_TOOL_OUTPUT` 读取 CC 标准环境变量。
- [ ] `[Unit] [Pre]` `test_hook_failure_never_blocks_claude_code` — **意图: 永不阻塞 Claude Code**。所有 hook action 的 exit code 必须为 0。验证所有 except 分支最终 `sys.exit(0)`。
- [ ] `[Contract] [Post]` `test_session_start_self_contained` — **意图: self-contained**。SessionStart hook 不依赖 daemon 预先启动。daemon 不在时自动启动 → register session → inject context。

---

### Feature 2a.2: Config capture + detection 域调整

**Scenario**: `CaptureConfig` 和 `DetectionConfig` 在 `config.py` 中已存在。2a 做三项调整使其与 SW design 一致。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 2b 的 detection 依赖这些配置字段。当前代码与 SW design 有三处差异需要修正。 |
| **优先级** | P0 — Phase 2b 前置依赖 |
| **依赖** | Phase 0 config.py（已有 AppConfig + load_config）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `04-config/schema.md` | §2.3 capture | `capture.mode` 可选 `key_moments` 或 `all`；`hint_format` 四种值 |
| `04-config/schema.md` | §2.4 detection | `detection.model` default `claude-haiku-4-5`（SW design 原始值） |
| `04-config.md` | §2 全局约束 | env var override 优先级规则 |

**三项调整**:

| # | 当前代码 | 目标 | 原因 |
|---|---------|------|------|
| 1 | `detection.model = "claude-sonnet-4-5"` | `"deepseek-v4-flash"` | 用户指定，计划与代码都不对 |
| 2 | `capture.mode: str = "key_moments"` | `Literal["key_moments", "all"]` | `all` 是合法用户选项，需类型约束 |
| 3 | `load_config()` Tier 3 无 capture/detection env override | 补 `BIBLE_CC_CAPTURE_ENABLED`、`BIBLE_CC_DETECTION_MODEL` 等 | 与 Phase 0 已有的三层加载模式保持一致 |

**Function-Level Steps**（按实现顺序）:

```python
# config.py — 局部调整

class CaptureConfig(BaseModel):
    enabled: bool = True
    mode: Literal["key_moments", "all"] = "key_moments"  # 调整 2
    commit_threshold_turns: int = 8
    commit_threshold_chars: int = 16000
    mid_session_detection: bool = True
    mid_session_upload: bool = False
    hint_format: str = "quote_with_command"  # 保持 str + validator（已有）
    tool_result_max_chars: int = 250

class DetectionConfig(BaseModel):
    model: str = "deepseek-v4-flash"  # 调整 1
    max_tokens: int = 512
    temperature: float = 0.0

# load_config() Tier 3 — 新增 env var overrides（调整 3）
if v := os.getenv("BIBLE_CC_CAPTURE_ENABLED"):
    config.capture.enabled = v.lower() in ("1", "true", "yes")
if v := os.getenv("BIBLE_CC_DETECTION_MODEL"):
    config.detection.model = v
# ... 其余字段同理
```

**交付标准**:

- [ ] `detection.model` 默认值改为 `"deepseek-v4-flash"`
- [ ] `capture.mode` 类型收紧为 `Literal["key_moments", "all"]`
- [ ] `load_config()` Tier 3 支持 capture/detection 字段的 env override
- [ ] 所有已有 config 测试仍然 green

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_detection_model_default` — 无 config 文件 → `"deepseek-v4-flash"`
- [ ] `[Unit] [Pre]` `test_capture_mode_invalid_fallback` — mode="invalid" → Pydantic ValidationError
- [ ] `[Unit] [Pre]` `test_capture_enabled_env_override` — `BIBLE_CC_CAPTURE_ENABLED=false` → `capture.enabled == false`

*意图测试*:
- [ ] `[Unit] [Pre]` `test_capture_disabled_skips_all_detection` — **意图: 紧急关闭开关**。`capture.enabled=false` 时跳过所有 detection 逻辑。

---

### Feature 2a.3: 消除 subprocess spawning 重复

**Scenario**: Phase 0 中 `hook.py:_ensure_daemon()` 和 `daemon.py:_do_start()` 各自实现了 uvicorn subprocess 启动、health check 轮询、超时处理。两处代码约 80% 相同，Phase 0 教训 #2 明确要求消除此重复。提取到 `daemon/daemon_launcher.py`。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 0 教训 #2（Don't duplicate subprocess spawning）。两个调用方需要相同行为：health check → spawn uvicorn → poll → timeout tail log。合并后 bug fix 只需改一处。 |
| **优先级** | P1 — 技术债清理 |
| **依赖** | Phase 0 hook.py、Phase 0 daemon.py |

**Function-Level Steps**（按实现顺序）:

```python
# daemon/daemon_launcher.py — 新建模块

def ensure_daemon_started(port: int, log_path: Path, poll_timeout: float = 5.0) -> bool:
    """幂等：确保 daemon 在指定端口运行。
    1. GET /daemon/health → 200? → return True（已在运行）
    2. 否则 spawn uvicorn（stdout/stderr → log_path）
    3. 轮询 health check（最多 poll_timeout 秒）
    4. 成功 → return True
    5. 超时 → tail log → stderr → return False
    """
    # 合并 hook.py:_ensure_daemon() 和 daemon.py:_do_start() 的逻辑

# scripts/hook.py — 改用共享函数
from bible_cc_plugin.daemon.daemon_launcher import ensure_daemon_started

# scripts/daemon.py — 改用共享函数
from bible_cc_plugin.daemon.daemon_launcher import ensure_daemon_started
```

**交付标准**:

- [ ] `daemon_launcher.py` 中的 `ensure_daemon_started()` 被 hook.py 和 daemon.py 同时使用
- [ ] 两处调用方不再有各自的 uvicorn spawn 代码
- [ ] 行为不变：现有 daemon 启动/health check 测试全部 green

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_ensure_daemon_started_already_running` — health check 200 → 不 spawn，return True
- [ ] `[Unit] [Pre]` `test_ensure_daemon_started_spawns_and_waits` — ConnectError 后恢复 → spawn + poll → True
- [ ] `[Unit] [Pre]` `test_ensure_daemon_started_timeout_tails_log` — 持续失败到超时 → tail log → False

*意图测试*:
- [ ] `[Unit] [Pre]` `test_launcher_is_single_source_of_truth` — **意图: DRY**。grep 确认项目中只有 `daemon_launcher.py` 包含 `uvicorn` subprocess spawn 代码。

---

### Feature 2a.4: GET /daemon/moments 端点 + Command 文件落地 + 契约测试

**Scenario**: 新增 `GET /daemon/moments?session_id=X` 端点（从原 2c.3 提前至此）。一个薄的只读查询——`SELECT * FROM moments WHERE session_id=? ORDER BY detected_at DESC`。Hook 在 2b 阶段通过此端点读取 detection 产生的 moments → 格式化为 hint → stdout。

同时将 `commands/sessions.md` 和 `commands/context.md` 落地为可工作的 slash command。契约测试验证 hook↔daemon HTTP 交互协议。

| 属性 | 说明 |
|------|------|
| **理由** | `GET /daemon/moments` 必须在 2b 之前就绪——2b detection 产生 moment 后，hook 通过此端点读取并输出 hint。只读查询极其简单，不依赖任何 Phase 2 逻辑。编辑和删除端点（PUT/DELETE）仍留在 2c.3。 |
| **优先级** | P0 |
| **依赖** | Phase 1d（server.py 端点模式）、1a.2（get_moments_by_session CRUD）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `02-interfaces.md` | §1.5 | `GET /daemon/moments?session_id=X` 返回 moments 列表 |
| `03-daemon/http-api.md` | §6 | `/daemon/moments` 端点 spec |
| `07-commands/specs.md` | — | sessions/context command 定义 |

**Function-Level Steps**（按实现顺序）:

```python
# server.py — 新增端点

@app.get("/daemon/moments")
async def list_moments(session_id: str):
    """GET /daemon/moments?session_id=X → 返回该 session 所有 moments（按 detected_at 倒序）。
    包含 flushed=0 和 flushed=1 的 moments。
    """
    conn = _get_db()
    moments = get_moments_by_session(conn, session_id)
    return {"moments": moments}
```

```markdown
# commands/sessions.md — 调用 GET /daemon/sessions（已有端点）
/bible-cc:sessions — 显示当前活跃和已完成的 session 列表

# commands/context.md — 调用 POST /context/inject（已有端点）
/bible-cc:context — 查看当前注入的上下文内容（branch + turns 摘要 + moments）

# session_id 获取策略：
#   1. 优先：CC 在 command 执行环境中提供 $CLAUDE_SESSION_ID
#   2. Fallback：daemon 从 sessions 表取最近 active session 推断
#       SELECT session_id FROM sessions WHERE status='active' ORDER BY created_at DESC LIMIT 1
```

```python
# tests/contract/test_hook_daemon.py

async def test_contract_hook_session_start_creates_session():
    """session-start hook → daemon → session 出现在 GET /daemon/sessions 中"""

async def test_contract_hook_turn_user_buffers_turn():
    """turn-user hook → daemon → turn 出现在 debug/turns 中"""

async def test_contract_hook_turn_tool_buffers_tool_call():
    """turn-tool hook（含 $CLAUDE_TOOL_OUTPUT）→ daemon → tool_output 完整存储"""

async def test_contract_hook_session_end_marks_completed():
    """session-end hook → daemon → session status='completed'"""

async def test_contract_hook_graceful_skip_daemon_unreachable():
    """daemon 不在时 → hook 四个 action 全部 exit code 0 + stderr 有 WARN 标记"""

async def test_contract_get_moments_schema():
    """GET /daemon/moments?session_id=X → 验证 response schema: {moments: [{type, title, ...}]}"""
```

**交付标准**:

- [ ] `GET /daemon/moments?session_id=X` 返回 moments 列表（含 type, title, narrative, detected_at, flushed）
- [ ] `commands/sessions.md` 和 `commands/context.md` 文件存在且内容正确
- [ ] `tests/contract/test_hook_daemon.py` 覆盖所有四个 action + graceful skip
- [ ] CI 中运行契约测试（`./scripts/dev.sh ci`）

**测试用例**（实现后编写——Contract tests 需要 daemon HTTP 进程）:

*功能测试*:
- [ ] `[Integration] [Post]` `test_get_moments_returns_list` — 插入 2 moments → GET → 返回 2 条
- [ ] `[Contract] [Post]` `test_hook_session_start_full_flow` — 启动 daemon → hook session-start → session 存在 + 后续 turn 可写入
- [ ] `[Contract] [Post]` `test_hook_turn_tool_full_output_roundtrip` — 5000 char tool output → 完整保存
- [ ] `[Contract] [Post]` `test_hook_graceful_degradation_all_actions` — daemon 不在 → 四个 action 全部 exit 0

*意图测试*:
- [ ] `[Contract] [Post]` `test_contract_tests_do_not_import_buffer_directly` — **意图: 契约测试是黑盒**。只通过 HTTP 调用，不 import 内部模块。

---

## 2a 验收标准

- [ ] `./scripts/dev.sh ci` 通过（lint + unit test + contract test）
- [ ] `hooks.json` 四个 hook command 传入 CC 环境变量 + SessionStart 含 `inject: true`
- [ ] hook.py argparse 接受 `--session-id`/`--message`/`--tool`/`--input`/`--output` 参数
- [ ] Hook 四个 action 全部正确调用 daemon 端点
- [ ] `tests/contract/test_hook_daemon.py` 通过：每 action 1 sunny + 1 graceful skip
- [ ] Hook 每步执行输出 stderr 追踪日志（action + endpoint + status + duration）
- [ ] Hook 失败时 stderr 输出详细错误原因 + graceful degradation 标记
- [ ] SessionStart hook self-contained（daemon 不在时先 start 再 register + inject）
- [ ] UserPromptSubmit/PostToolUse hook daemon 不可达时静默跳过（exit code 0）
- [ ] `detection.model` 默认值 `"deepseek-v4-flash"`，`capture.mode` 为 `Literal["key_moments", "all"]`
- [ ] `load_config()` Tier 3 支持 capture/detection 的 env override
- [ ] `daemon_launcher.py` 成为 daemon 进程管理的唯一入口
- [ ] `GET /daemon/moments?session_id=X` 端点可用
- [ ] `commands/sessions.md` 和 `commands/context.md` 落地为可工作 slash command
