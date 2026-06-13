# Phase 2: Capture Pipeline（Moment Detection）

> **For agentic workers:** Phase 2 是 plugin 的核心价值——moment detection。完成后的 plugin 可以实时检测 key moments，去重，并通过 hint 通知用户。

**Goal:** Hook bridge 脚本可用、Phase 1 mid-session detection（LLM 异步）、Phase 2 retrospective detection（session end LLM 同步）、两层去重、hint system。

**Architecture:** Hook shell scripts → daemon HTTP API → async LLM worker → SQLite moments table → hook stdout hints。

**Tech Stack:** Anthropic SDK, asyncio, FastAPI background tasks

**预估: 5-7 天**

---

## Feature 逐个讨论

### F2.1 — Hook Bridge（scripts/hook.py）

| 属性 | 说明 |
|------|------|
| **理由** | Hook 脚本是 Claude Code 生命周期事件到 daemon 的唯一桥梁。CLAUDE.md 定义了 4 个 action（session-start, turn-user, turn-tool, session-end），每个 action 调用 daemon 的一个或多个 HTTP 端点。SessionStart hook 必须 self-contained——daemon 不在时自动 start，再 register session，再 inject context。UserPromptSubmit 和 PostToolUse hook 必须在 daemon 不可达时静默跳过（graceful degradation 硬性约束）。 |
| **优先级** | P0 — 数据入口 |
| **依赖** | Phase 1 daemon HTTP API（全部端点就绪）、config.py（读取 daemon port） |

四个 action：
- `session-start`: idempotent `POST /daemon/start` → `POST /session/start` → `POST /context/inject` → 输出 `<relevant-memories>` 到 stdout → Claude Code injects 到 system prompt（通过 hook `inject: true`）
- `turn-user`: `POST /turn/user`。Daemon 不可达 → 静默跳过（exit code 0）。
- `turn-tool`: `POST /turn/tool`。Daemon 不可达 → 静默跳过（exit code 0）。
- `session-end`: `POST /session/end`（此阶段不触发 LLM，仅标记 session completed）

### F2.2 — Anthropic Client Wrapper（detector.py 基础层）

| 属性 | 说明 |
|------|------|
| **理由** | Phase 1 和 Phase 2 detection 都调用 Anthropic API。封装成一个薄层避免 prompt construction、API call、structured output parsing 的重复代码。Error handling 是硬性要求——LLM 调用失败时返回空列表而非 crash。 |
| **优先级** | P0 |
| **依赖** | config.py（capture 配置域）、pyproject.toml（anthropic 依赖） |

封装函数：
- `detect_moments(turns: list[Turn], known_moments: list[Moment] | None, phase: Literal[1,2]) -> list[MomentCandidate]`
- 内置 structured output schema（moment type + title + narrative）
- Error handling: API 调用失败时返回空列表（不 crash，不阻塞 turn）
- Model 从 config 读取，默认 `claude-haiku-4-5`（低延迟、低成本，适合分类任务）

### F2.3 — Phase 1 Mid-Session Detection

| 属性 | 说明 |
|------|------|
| **理由** | 在对话中实时检测 key moments（decision, accomplishment），让用户感知到 plugin 在工作。检测是 async 的——在后续 turn 才收到 hint，因为 LLM 调用需要时间。阈值触发（turn count / char count 先到达者为准）避免每 turn 都调 LLM，节省 API 成本。Content-hash dedup 是第一层去重（第二层是 Phase 2 prompt injection）。 |
| **优先级** | P0 — capture pipeline 核心 |
| **依赖** | buffer.py（turns 读，取最近 2-3 turns）、detector.py（Anthropic client wrapper）、config.py（commit_threshold_turns=8, commit_threshold_chars=16000） |

流程：
1. `/turn/user` 或 `/turn/tool` 收到请求 → 立即返回（不等待检测）
2. 后台 queue 触发检查：累计 turns >= 8 或累计 chars >= 16000？
3. 是 → 构建 prompt（最近 2-3 turns 完整内容 → "classify: moment(type+title+narrative) or none"）
4. 调 Anthropic API（low max_tokens，结构化输出）
5. 结果：moment → content-hash dedup → `INSERT OR IGNORE` into moments table
6. `mid_session_upload=false` → 不 flush（留给 session end 统一处理）

### F2.4 — Phase 2 Retrospective Detection

| 属性 | 说明 |
|------|------|
| **理由** | Session end 时用全 session 上下文做 synthesis + gap-fill。Phase 1 只看 2-3 turns 的滑动窗口，可能遗漏跨越多 turns 的 long-arc decision 或者需要整体视角才能识别的 accomplishment。Phase 2 的 prompt 与 Phase 1 不同——它要求 LLM 做 synthesis 而非 spot-check。Prompt 中注入 Phase 1 已知 moments 列表（"Do NOT re-report these"），配合 content-hash UNIQUE 约束形成两层去重。 |
| **优先级** | P0 |
| **依赖** | Phase 1 detection（已知 moments 列表）、buffer.py（全 session turns）、detector.py |

流程：
1. `POST /session/end` 触发
2. 读当前 session 的全部 turns + Phase 1 已知 moments
3. 构建 prompt："Here is a complete session. What was accomplished? What decisions shaped the outcome? What should be remembered for future sessions? Known moments: [...]. Do NOT re-report these."
4. 调 Anthropic API（higher max_tokens，结构化输出）
5. 结果：session assessment + list of NEW moments
6. 新 moments → content-hash dedup → INSERT OR IGNORE
7. Phase 3 接入 flush → bundle + POST to BiBLE

### F2.5 — Hint System

| 属性 | 说明 |
|------|------|
| **理由** | 用户感知 plugin 状态的唯一通道。CLAUDE.md 硬定义：hook stdout 是唯一通知通道，显示位置是 conversation transcript inline。Phase 1 detection 是 async 的——hint 在后续 turn 才到达，携带自包含上下文让用户不需要回看原始 turn。 |
| **优先级** | P1 — 用户体验 |
| **依赖** | Phase 1 detection（moment result）、config.py（hint_format） |

四种 hint_format：
- `quote_with_command`（默认）: `⎿ ⏳ Captured: "PostgreSQL for auth storage" — Decision. /bible-cc:review`
- `quote_only`: 无命令提示
- `command_only`: `⎿ ⏳ Key moment captured (turn 5). /bible-cc:review`
- `narrative`: `⎿ ⏳ Captured decision: PostgreSQL for auth storage. Postgres chosen over SQLite...`

Error hints 模板：端口冲突、BiBLE 断连、LLM 调用失败。

### F2.6 — Unit Tests

| 属性 | 说明 |
|------|------|
| **优先级** | P0 — TDD |
| **依赖** | detector.py、Phase 1 测试骨架 |

- `test_detector.py`: prompt construction（验证 Phase 1 prompt 包含最近 2-3 turns 内容）、阈值触发（turn count / char count）、Phase 2 prompt 包含已知 moments 列表、content-hash 计算、structured output schema 校验、LLM stub（deterministic response 替代真实 API）、hint format 四种模式正确

### F2.7 — CI Pipeline 扩展：Detector Unit Test + 确定性验证

| 属性 | 说明 |
|------|------|
| **理由** | Phase 2 引入 LLM 调用——非确定性组件。CI 必须用 stub 替代真实 LLM（TDD 原则第 4 条），验证 prompt construction、threshold 逻辑、dedup 行为。CI 不调真实 Anthropic API。 |
| **优先级** | P0 — CD 持续集成 |
| **依赖** | Phase 1 CI、F2.6（test_detector.py） |

实现：`dev.sh ci` 现在包含 `uv run pytest tests/unit/ tests/contract/`。Detector 测试使用 `DETECTOR_TEST_MODE=true` env var 强制 stub LLM。

### F2.8 — Contract Tests：Hook ↔ Daemon 接口契约

| 属性 | 说明 |
|------|------|
| **理由** | Hook 脚本首次引入——外部进程通过 HTTP 调 daemon。这是最容易出集成问题的边界：shell 调用格式错误、JSON 序列化问题、daemon 端口不对。契约测试验证 hook.py 的四个 action 与 daemon 端点的交互协议——不验证 detection 是否正确（那是 detector 单元测试的职责），只验证 hook 调 daemon 的 HTTP 交互正确。 |
| **优先级** | P0 — 接口契约 |
| **依赖** | F2.1（hook bridge）、daemon HTTP API |

实现：
- `tests/contract/test_hook_daemon.py`：启动 daemon → 调 hook.py 四个 action → 验证 daemon 收到正确请求
  - `session-start` → 验证 daemon 创建了 session + 返回 context
  - `turn-user` → 验证 daemon buffer 了 turn
  - `turn-tool` → 验证 daemon buffer 了 tool call
  - `session-end` → 验证 daemon 标记 session completed
  - Rainy path: daemon 不在时 → hook exit code 0（graceful skip）

### F2.9 — Debuggability：Detection 追踪 + Hook 执行日志

| 属性 | 说明 |
|------|------|
| **理由** | Moment detection 是整个 plugin 最复杂的逻辑——LLM 调用、阈值触发、dedup、hint 通知。任何一个环节出问题都难以排查。Phase 2 也是首次引入 hook 脚本——shell 调用 daemon HTTP，出问题时需要知道哪个 hook 被调用、传了什么参数、返回了什么。没有 execution tracing，调试 hook 问题靠猜。 |
| **优先级** | P0 — 核心链路调试 |
| **依赖** | detector.py、hook.py、Phase 1 请求追踪 |

实现：

**Detection 追踪日志**（输出到 daemon stderr）：
```
[detect:phase1] session=abc123, turns=8, chars=16200 → triggered (chars threshold)
[detect:phase1] prompt_tokens=420, turns_range=[5-7]
[detect:phase1] API call → latency=1.2s, model=claude-haiku-4-5, tokens=180
[detect:phase1] result: moment.type=decision, title="PostgreSQL for auth", dedup=INSERTED
[detect:phase2] session=abc123, full_turns=25, known_moments=2
[detect:phase2] API call → latency=3.4s, model=claude-haiku-4-5, tokens=890
[detect:phase2] result: NEW moments=1, DUPLICATE=0, total_session_moments=3
```

**Detection prompt logging**（仅在 `log_level=DEBUG` 时开启，prompt 可能包含敏感内容）:
- `GET /daemon/debug/detections?session_id=X` → 返回该 session 所有 detection 记录：触发时间、phase、prompt_snapshot（前 500 chars）、result、dedup 结果、API latency

**Hook 执行追踪**（hook.py 输出到 stderr）：
```
[hook:session-start] daemon not running → POST /daemon/start... OK (pid=12345)
[hook:session-start] POST /session/start... OK (is_new=true, recovery=0)
[hook:session-start] POST /context/inject... OK (turns=0, moments=0)
[hook:session-start] DONE (total=120ms)
[hook:turn-user] session=abc123, message_len=340 → POST /turn/user... OK (turn_id=7)
[hook:turn-tool] session=abc123, tool=read_file, output_len=2800 → POST /turn/tool... OK
[hook:session-end] POST /session/end... OK (moments_flushed=3)
```

**Hook 失败详细诊断**：hook 执行失败时打印到 stderr：
```
[hook:turn-user] ERROR: daemon unreachable (http://127.0.0.1:9777) → skipping (graceful degradation)
[hook:session-end] WARN: POST /session/end returned 500 → response body: {"error": "LLM timeout"}
```

**Detection metrics debug endpoint**: `GET /daemon/debug/detections/stats` → 返回累计统计：total_detections, phase1_count, phase2_count, dedup_hits, avg_latency_ms, model 分布。

---

## Phase 2 验收标准

- [ ] `./scripts/dev.sh ci` 通过（lint + unit test + contract test，detector 使用 stub LLM）
- [ ] Hook 脚本四个 action 均可正确调用 daemon 端点
- [ ] `tests/contract/test_hook_daemon.py` 通过：每个 action 验证 HTTP 交互 + graceful skip
- [ ] Hook 每步执行输出 stderr 追踪日志（action + endpoint + status + duration）
- [ ] Hook 失败时 stderr 输出详细错误原因 + graceful degradation 标记
- [ ] SessionStart hook self-contained（daemon 不在时先 start 再 register + inject）
- [ ] UserPromptSubmit/PostToolUse hook daemon 不可达时静默跳过（exit code 0，stderr 可见 WARN）
- [ ] Phase 1 detection 异步执行，不阻塞 `/turn/*` 端点（return immediately）
- [ ] Detection 每步输出 stderr 追踪日志（trigger reason, prompt stats, API latency, result, dedup）
- [ ] `GET /daemon/debug/detections?session_id=X` 返回 detection 历史
- [ ] `GET /daemon/debug/detections/stats` 返回累计统计
- [ ] 阈值触发正确（turn count 和 char count 先到达者触发）
- [ ] Content-hash dedup 生效（同一 moment 重复 INSERT 不报错也不产生重复行）
- [ ] Phase 2 prompt 包含 Phase 1 已知 moments，LLM 不重复报告
- [ ] Hint 四种 format 均输出到 hook stdout，内容正确
- [ ] 单元测试全部通过（stub LLM）

---

## Phase 2 产出文件

```
src/bible_cc_plugin/
├── daemon/
│   ├── detector.py             ← F2.2, F2.7 (Anthropic client wrapper + detection logging)
│   └── server.py               ← (修改: Phase 1/2 detection + debug endpoints)
scripts/
├── hook.py                     ← F2.1, F2.7 (hook bridge + execution tracing)
├── daemon.py                   ← (Phase 0 延续，功能完善)
hooks/
├── hooks.json                  ← (Phase 0 延续，更新 hook commands)
tests/unit/
├── test_detector.py            ← F2.6
```
