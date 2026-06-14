# Phase 2b: Phase 1 Mid-Session Detection

> **依赖**: Phase 2a（hook bridge 喂数据 + config capture 域可用）
> **被依赖**: Phase 2c（Phase 2 retrospective 需要 Phase 1 已知 moments 列表）
> **父文档**: [Phase 2 总览](../plans/2026-06-13-phase-2-capture-pipeline.md)

**交付 Command**: 无新 command——detection 对用户透明，通过 hint 感知（Phase 2d 端到端）。

**预估: 2 天**

### 测试标注

2b.1（Anthropic client wrapper）和 2b.2（asyncio.Queue worker）默认 `[Unit] [Pre]`（stub LLM）。2b.3（Phase 1 detection logic）默认 `[Unit] [Pre]`。2b.4（`/turn/*` 端点增强）需要 FastAPI TestClient → `[Integration] [Post]`。Debug 日志职责嵌入各 feature 交付标准（Phase 0 教训 #1）。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## 2. Sub-Phase 2b: Phase 1 Mid-Session Detection（2d）

### Scenario

> 用户在 Claude Code 中与模型对话。每一条用户消息通过 UserPromptSubmit hook → `POST /turn/user` 写入 SQLite，每个 tool call 通过 PostToolUse hook → `POST /turn/tool` 写入。Daemon 后台 worker 监控阈值——当累计 8 个 turns 或 16000 字符时，worker 从 SQLite 取最近 2-3 turns 的完整内容 → 构建 Phase 1 prompt → 调 Anthropic API → 解析结果。
>
> 如果 LLM 返回了 key moment（如 "PostgreSQL for auth storage" — Decision），worker 计算 content-hash → `INSERT OR IGNORE INTO moments`。Hook 在下一次 turn 时通过 `GET /daemon/moments?session_id=X` 读取新 moments → 格式化为 hint → stdout 输出。
>
> 如果 LLM 返回 "none"，worker 仅记录日志——不产生 moment。Threshold 计数器重置，下一轮 turns 重新累积。
>
> 如果 `capture.enabled=false`，整个检测链路跳过——threshold 计数器不递增，worker 不 queue。

### 实现顺序

```
2b.1 (Anthropic client) ──┐
                          ├──► 2b.3 (detection logic) ──► 2b.4 (turn endpoint 集成)
2b.2 (asyncio worker)  ──┘     │
                                └── debug endpoints（GET /daemon/debug/detections*）
```

| 顺序 | Feature | 理由 | 可并行 |
|------|---------|------|--------|
| **1st** | 2b.1 Anthropic client | 独立——只依赖 config + anthropic SDK | ✅ 可与 2b.2 并行 |
| **2nd** | 2b.2 asyncio Worker | 独立——只依赖 FastAPI lifespan + config | ✅ 可与 2b.1 并行 |
| **3rd** | 2b.3 Detection Logic | 依赖 2b.1 + 2b.2 的 client 和 worker | — |
| **4th** | 2b.4 Turn 端点增强 | 仅依赖 2b.2（`check_threshold` + queue）。可与 2b.3 并行 | ✅ 可与 2b.3 并行 |

Debug 职责嵌入各 feature（非独立 feature）：
- 2b.1：API call 日志（model, latency, tokens）
- 2b.2：Worker 状态日志（启动/停止/崩溃/queue 告警）
- 2b.3：Detection 日志（trigger reason, dedup）+ Debug endpoints

---

### Feature 2b.1: Anthropic Client Wrapper（detector.py 基础层）

**Scenario**: Phase 1/2 detection 都需要调 Anthropic API——prompt 构建、API call、structured output parsing。封装为薄层避免重复代码。Error handling 是硬性要求——LLM 调用失败时不 crash，返回空列表让调用方继续。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 1 和 Phase 2 detection 共享 Anthropic API 调用模式——差异只在 prompt 内容和 max_tokens。封装成统一接口避免两个地方各自实现 API call。Error handling 必须防御性——Anthropic API 可能 rate limit、network error、model not found，任何失败都不应导致 daemon crash 或 turn 丢失。 |
| **优先级** | P0 — detection 基础 |
| **依赖** | config.py（detection.model, detection.max_tokens）、pyproject.toml（anthropic>=0.40.0 已安装）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `05-capture/detection.md` | §1.2 Phase 1 流程 | prompt 构建 → LLM call → 解析 → 算 hash → INSERT OR IGNORE |
| `05-capture/detection.md` | §3.1 Phase 1 Prompt | 完整 prompt 模板："classify: moment(type+title+narrative) or none" |
| `05-capture/detection.md` | §4 结构化输出 | `{result: "moment"\|"none", moments: [{type, title, narrative, tool_summary}]}` |
| `05-capture/detection.md` | §5 LLM 参数 | model from config, max_tokens=512, temperature=0.0 |
| `CLAUDE.md` | Moment Detection Design | Phase 1: last 2-3 turns, low max_tokens |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Anthropic API key 未设置 | `Anthropic(api_key=...)` → KeyError | detection 失败，daemon 正常运行 | `daemon.log` 搜索 `ANTHROPIC_API_KEY not set` | 设置 env var → 重启 daemon |
| API rate limit (429) | SDK 抛 `RateLimitError` → catch → log WARNING | Detection 本次跳过，下次阈值触发时重试 | `daemon.log` 搜索 `Anthropic.*429` | 等待 rate limit 窗口过期 |
| API 返回非结构化输出 | `result` 不是 `"moment"` 或 `"none"` | 此 turn 的 detection 结果丢弃 | `daemon.log` 搜索 `unexpected result` | 检查 prompt 是否被修改 |
| API timeout（>30s） | SDK timeout → catch → log WARNING | Detection 本次跳过 | `daemon.log` 搜索 `Anthropic.*timeout` | 检查网络 |

**Function-Level Steps**（按实现顺序）:

```python
# daemon/detector.py

import anthropic
from bible_cc_plugin.config import DetectionConfig

def _create_client() -> anthropic.Anthropic:
    """从环境变量 ANTHROPIC_API_KEY 创建 client。"""

def build_phase1_prompt(turns: list[Turn]) -> str:
    """构建 Phase 1 detection prompt（§3.1 模板）。
    包含 turns 文本 + moment type 定义 + "output: moment or none"。
    """

async def call_detection_llm(
    prompt: str, config: DetectionConfig, phase: int = 1,
) -> list[MomentCandidate]:
    """调 Anthropic API → 解析结构化输出 → 返回 MomentCandidate 列表。
    Phase 1: max_tokens from config（default 512）。
    Phase 2: max_tokens × 2（自动推导）。
    API 失败 → log WARNING → return []。
    """

async def detect_moments(
    turns: list[Turn],
    known_moments: list[Moment] | None,
    phase: Literal[1, 2],
    config: DetectionConfig,
) -> list[MomentCandidate]:
    """Phase 1/2 detection 统一入口。
    
    turns: 由调用方（_process_detection_task）准备——Phase 1 传最近 2-3 turns，
           Phase 2 传全 session turns。本函数不负责取数据。
    known_moments: Phase 1 传 None，Phase 2 传 Phase 1 已知 moments 列表。
    """
```

**Stub LLM 注入（CI 必需）**:

```python
# detector.py — 函数内部分支（非条件重定义）

async def call_detection_llm(
    prompt: str, config: DetectionConfig, phase: int = 1,
) -> list[MomentCandidate]:
    """调 Anthropic API → 解析结构化输出 → 返回 MomentCandidate 列表。"""
    # CI stub：检测 DETECTOR_TEST_MODE env var → 短路到确定性响应
    if os.getenv("DETECTOR_TEST_MODE", "") in ("1", "true", "yes"):
        return _stub_detection(prompt)
    
    # 真实 Anthropic API 调用...
    # API 失败 → log WARNING → return []

def _stub_detection(prompt: str) -> list[MomentCandidate]:
    """Stub: 根据 prompt 内容确定性返回 moment 或 none。"""
    if "NO_MOMENT" in prompt:
        return []
    return [MomentCandidate(type="decision", title="Stub Decision", narrative="Stub narrative")]
```

**交付标准**:

- [ ] `build_phase1_prompt()` 生成符合模板的 prompt（含 turns 内容 + moment types）
- [ ] `call_detection_llm()` 成功时返回 MomentCandidate 列表
- [ ] `call_detection_llm()` API 失败时返回 `[]`（不抛异常）
- [ ] `DETECTOR_TEST_MODE=true` 时使用 stub（不调真实 API）
- [ ] Stub 覆盖 "moment found" 和 "none" 两种情况
- [ ] API call 输出日志到 `daemon.log`（model, latency, tokens），API 失败时输出 WARNING

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_build_phase1_prompt_contains_turns` — turns 内容出现在 prompt 中
- [ ] `[Unit] [Pre]` `test_build_phase1_prompt_includes_moment_types` — prompt 含 DECISION/ACCOMPLISHMENT/SESSION_START
- [ ] `[Unit] [Pre]` `test_call_detection_llm_returns_candidates` — stub → 验证返回格式
- [ ] `[Unit] [Pre]` `test_call_detection_llm_api_failure_returns_empty` — mock API error → return []
- [ ] `[Unit] [Pre]` `test_stub_detector_deterministic` — 相同 prompt → 相同结果

*意图测试*:
- [ ] `[Unit] [Pre]` `test_detector_failure_never_crashes_daemon` — **意图: 防御性**。即使 Anthropic SDK 抛未预期异常，`detect_moments()` 必须 catch 所有 → return []。mock SDK 抛 `RuntimeError` → catch → return []。
- [ ] `[Unit] [Pre]` `test_no_real_api_call_in_ci` — **意图: CI 零成本**。CI 中 `DETECTOR_TEST_MODE=true` 确保不消耗 API 额度且测试 <10ms。

---

### Feature 2b.2: asyncio.Queue 后台 Worker

**Scenario**: Daemon 启动时（FastAPI lifespan）创建 `asyncio.Queue` + 后台 worker。`/turn/user` 和 `/turn/tool` 在阈值到达时 `await _detection_queue.put(task)`。Worker 从 queue 取 task → 调用 `_process_detection_task()`（2b.3 实现具体检测逻辑）→ `task_done()`。

| 属性 | 说明 |
|------|------|
| **理由** | SW design 明确要求 `/turn/*` 端点立即返回（<100ms），Detection 异步执行。asyncio.Queue 是 Python 标准库中最简单的异步任务队列。Worker 必须处理并发安全：同一 session 的 detection 串行化（避免 Phase 1 window race condition）。 |
| **优先级** | P0 — 异步检测的运行时基础 |
| **依赖** | FastAPI lifespan/app events、2a.2（config capture 域）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `05-capture/detection.md` | §1.3 异步模型 | `POST /turn/user → queue task → async worker → runs detection` |
| `03-daemon/http-api.md` | §7 时序约束 | `/turn/user` <10ms，不阻塞 |
| `05-capture/hook-flow.md` | §1.3 阈值计数器 | 内存 per-session 计数器，daemon 重启归零 |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Worker 因异常崩溃 | Worker 退出 → queue 积压 | Detection 停止但 daemon 不挂 | `daemon.log` 搜索 `detection worker crashed` | Worker 必须有 restart loop |
| Queue 积压 | Queue size > 100 → log WARNING | Detection latency 增加 | `daemon.log` 搜索 `detection queue size` | 检查 API rate limit |
| Worker 启动晚于首个 turn | Turn 已写入但 worker 未 ready | 正常——task 已在 queue 等待 | 无感知 | Worker 在 lifespan startup 中创建 |

**Function-Level Steps**（按实现顺序）:

```python
# server.py — FastAPI lifespan

_detection_queue: asyncio.Queue = asyncio.Queue()
_threshold_state: dict[str, dict] = {}  # session_id → {turns, chars}
_app_config = None  # 模块级，lifespan 启动时赋值

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _app_config
    _app_config = load_config()  # 启动时加载配置
    worker_task = asyncio.create_task(_detection_worker())
    yield
    await _detection_queue.put(None)  # sentinel
    await worker_task

async def _detection_worker():
    """从 queue 取 task → 调用 _process_detection_task() → task_done()。"""
    while True:
        try:
            task = await _detection_queue.get()
            if task is None:
                break
            await _process_detection_task(task)
        except Exception:
            _logger.error("detection worker crashed, restarting...", exc_info=True)
            await asyncio.sleep(1)
        finally:
            if task is not None:
                _detection_queue.task_done()

def check_threshold(session_id: str, turns: int = 1, chars: int = 0) -> bool:
    """阈值到达？turns 和 chars 以先到达者为准。"""
    state = _threshold_state.setdefault(session_id, {"turns": 0, "chars": 0})
    state["turns"] += turns
    state["chars"] += chars
    cfg = _app_config
    if (state["turns"] >= cfg.detection.commit_threshold_turns or
        state["chars"] >= cfg.detection.commit_threshold_chars):
        state["turns"] = 0
        state["chars"] = 0
        return True
    return False

def reset_threshold(session_id: str) -> None:
    """重置计数器。/clear 和 /compact 时调用。"""
    _threshold_state.pop(session_id, None)
```

**交付标准**:

- [ ] Daemon 启动时创建 `_detection_worker`（lifespan startup）
- [ ] Worker 从 queue 取 task → 调用 `_process_detection_task()` → `task_done()`（具体检测逻辑在 2b.3）
- [ ] Worker 崩溃后自动重启（外层 try/except + loop）
- [ ] `check_threshold()` turns 和 chars 先到达者触发
- [ ] 阈值触发后计数器归零
- [ ] `reset_threshold()` 支持 `/clear` 和 `/compact`
- [ ] Daemon shutdown 时 worker 优雅退出（sentinel）
- [ ] Worker 状态日志输出到 `daemon.log`（启动/停止/崩溃重启/queue 积压 >100 告警）

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_check_threshold_turns_first` — 7 turns → False, 8th turn → True
- [ ] `[Unit] [Pre]` `test_check_threshold_chars_first` — 15999 chars → False, 16000th → True
- [ ] `[Unit] [Pre]` `test_check_threshold_resets_after_trigger` — 触发后计数器归零
- [ ] `[Unit] [Pre]` `test_reset_threshold_clears_state` — reset → 后续从零开始
- [ ] `[Unit] [Pre]` `test_worker_restarts_after_crash` — mock 抛异常 → worker 继续处理后续 task

*意图测试*:
- [ ] `[Unit] [Pre]` `test_worker_same_session_serialized` — **意图: 并发安全**。同一 session 的连续 detection tasks 串行执行（避免 Phase 1 sliding window race condition）。
- [ ] `[Unit] [Pre]` `test_turn_endpoints_return_before_detection` — **意图: 端点立即返回**。即使 worker sleep(5s)，`/turn/user` latency <100ms。

---

### Feature 2b.3: Phase 1 Detection Logic（完整链路）

**Scenario**: Worker 取到 task → 从 SQLite 取最近 2-3 turns → 调 `detect_moments(phase=1)` → 如果返回 moment → 从 tool_output 提取 ≤`tool_result_max_chars` 摘要 → 算 content-hash → `INSERT OR IGNORE INTO moments` → 记录结构化日志。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 2 核心价值。完整链路：SQLite turns → LLM 分类 → hash dedup → 存储 + hint。Content-hash 两层去重的第二层。Tool summary LLM 提取精华（非机械截断）。 |
| **优先级** | P0 — capture pipeline 核心 |
| **依赖** | 2b.1（Anthropic client）、2b.2（worker）、1a.2（CRUD）、1a.4（content-hash）。前置 step：buffer.py 新增 `get_recent_turns()` 和 `get_all_session_turns()`——Phase 1a CRUD 层遗漏的 READ 函数。 |

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `05-capture/detection.md` | §1.2 流程 | 6 步：取 turns → prompt → LLM → 解析 → hash → INSERT |
| `05-capture/detection.md` | §1.4 重叠窗口 | 滑动窗口重叠 → content-hash 覆盖 |
| `05-capture/detection.md` | §3.1 Prompt | Phase 1 prompt + SESSION_START/DECISION/ACCOMPLISHMENT |
| `05-capture/hook-flow.md` | §2.3 Tool Output | LLM 提取 ≤tool_result_max_chars 精华 |

**Function-Level Steps**（按实现顺序）:

```python
# server.py — worker 内部

async def _process_detection_task(task: dict):
    session_id = task["session_id"]
    phase = task["phase"]
    conn = _get_db()
    
    # 取 turns
    if phase == 1:
        turns = get_recent_turns(conn, session_id, limit=3)
    else:
        turns = get_all_session_turns(conn, session_id)
    
    if not turns:
        return
    
    # 调 detector
    config = _app_config.detection
    known = get_moments_by_session(conn, session_id) if phase == 2 else None
    candidates = await detect_moments(turns, known, phase, config)
    
    # 存储 moment + dedup + hint
    for c in candidates:
        if c.type not in ("session_start", "decision", "accomplishment"):
            continue  # 过滤非 key moment type
        
        h = compute_content_hash(session_id, c.title, c.narrative)
        mid = insert_moment(conn, session_id, c.type, c.title, c.narrative, h, phase=str(phase))
        
        if mid is None:
            _logger.debug("detect: dedup skipped — %s", c.title)
            continue
        
        _logger.info(
            "detect:phase%d session=%s type=%s title=%s dedup=INSERTED",
            phase, session_id[:8], c.type, c.title
        )
        # Hint 输出由 hook 侧负责：hook 调 GET /daemon/moments 获取 moments
        # → format_hint()（2d 实现）→ stdout。daemon 不做 hint 格式化。
```

**交付标准**:

- [ ] Phase 1 detection 完整链路：turns → LLM → hash → INSERT
- [ ] `capture.enabled=false` 时跳过所有 detection
- [ ] Content-hash dedup 生效
- [ ] Non-key moment type 被过滤
- [ ] Worker 每个 task 后 `task_done()`
- [ ] Detection 日志输出到 `daemon.log`（trigger reason, prompt tokens, API latency, dedup result）
- [ ] `GET /daemon/debug/detections?session_id=X` 返回 detection 历史
- [ ] `GET /daemon/debug/detections/stats` 返回累计统计（内存计数器：total, phase1, dedup_hits, avg_latency_ms）
- [ ] Debug 端点仅在 `BIBLE_CC_DEBUG=true` 时启用

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_process_detection_stores_moment` — mock detector → moment 写入
- [ ] `[Unit] [Pre]` `test_process_detection_dedup_same_hash` — 重复 hash → 1 行
- [ ] `[Unit] [Pre]` `test_process_detection_none_skips` — detector 返回 [] → 无写入
- [ ] `[Unit] [Pre]` `test_capture_disabled_skips` — enabled=false → 不 queue task

*意图测试*:
- [ ] `[Unit] [Pre]` `test_detection_does_not_block_turn_write` — **意图: 写入不受影响**。Worker LLM 调用耗时 3s 时，新 `/turn/user` 仍 <100ms。
- [ ] `[Unit] [Pre]` `test_tool_output_preserved_verbatim` — **意图: 原始数据不丢失**。Detection 只读 tool_output——turns 表完整保留。
- [ ] `[Unit] [Pre]` `test_non_key_moment_types_filtered` — **意图: 采集分类严格**。只采集 SESSION_START/DECISION/ACCOMPLISHMENT。

---

### Feature 2b.4: `/turn/*` 端点增强

**Scenario**: Phase 1 的 `/turn/user` 和 `/turn/tool` 仅做 insert。Phase 2b 增强为：insert → inc threshold → if threshold: queue task → return `{turn_id, queued}`。

| 属性 | 说明 |
|------|------|
| **理由** | 打通 hook → turn endpoint → threshold → detection queue 完整链路。极简扩展——3 行逻辑。 |
| **优先级** | P0 |
| **依赖** | 2b.2（check_threshold + queue）。注：仅依赖 queue 机制和阈值函数，不依赖 2b.3。可与 2b.3 并行开发。 |

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `05-capture/hook-flow.md` | §1.2 | Step 4: 检查阈值 → queue → 返回 `{queued: true/false}` |
| `03-daemon/http-api.md` | §4.1-4.2 | `/turn/user` 和 `/turn/tool` 响应 schema |

**Function-Level Steps**（按实现顺序）:

```python
# server.py — /turn/user 增强

async def turn_user(request: TurnUserRequest) -> TurnResponse:
    # ... Phase 1 验证 + insert（不变）
    
    queued = False
    if _app_config.capture.enabled:
        if check_threshold(session_id, turns=1, chars=len(request.message)):
            await _detection_queue.put({"session_id": session_id, "phase": 1})
            queued = True
    
    return TurnResponse(turn_id=turn_id, queued=queued)
```

**交付标准**:

- [ ] `capture.enabled=true` + 阈值到达 → `queued=true`
- [ ] `capture.enabled=false` → `queued=false`（不 queue task，不递增计数器）
- [ ] 响应时间仍 <100ms

---

---

## 2b 验收标准

- [ ] `DETECTOR_TEST_MODE=true ./scripts/dev.sh ci` 通过
- [ ] Anthropic client wrapper：prompt 构建 + API call + structured output parsing
- [ ] Stub LLM 覆盖 "moment found" 和 "none" 两种情况
- [ ] API 失败时返回空列表（不 crash）
- [ ] asyncio.Queue worker：崩溃自动重启
- [ ] 阈值触发正确（turns 和 chars 先到达者）
- [ ] Phase 1 detection 完整链路正常
- [ ] Content-hash dedup 生效
- [ ] `capture.enabled=false` 跳过所有 detection
- [ ] Detection 每步输出结构化日志到 `daemon.log`
- [ ] Debug detection endpoints 可用
- [ ] Hook 可通过 `GET /daemon/moments?session_id=X`（2a.4 端点）读取 detection 产生的 moments
- [ ] 验证路径：daemon.log + debug stats endpoint + curl GET /daemon/moments（无需 2d）
