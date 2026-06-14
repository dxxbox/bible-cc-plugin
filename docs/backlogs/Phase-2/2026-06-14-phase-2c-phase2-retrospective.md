# Phase 2c: Phase 2 Retrospective Detection

> **依赖**: Phase 2b（Phase 1 detection 产生已知 moments 列表 + Anthropic client wrapper）
> **被依赖**: Phase 2d（/review command 需要 /moments CRUD 端点）
> **父文档**: [Phase 2 总览](../plans/2026-06-13-phase-2-capture-pipeline.md)

**交付 Command**: `/bible-cc:review`（端点层——GET 在 2a、PUT/DELETE 在 2c 就绪，command markdown 在 2d 落地）

**预估: 1.5 天**

### 测试标注

2c.1（`/session/end` 增强）默认 `[Unit] [Pre]`（mock Anthropic）。2c.2（Phase 2 prompt）默认 `[Unit] [Pre]`。2c.3（/moments 端点）需要 FastAPI TestClient → `[Integration] [Post]`。

---

## 3. Sub-Phase 2c: Phase 2 Retrospective（1.5d）

### Scenario

> 用户完成了一轮对话，Claude Code session 结束。Stop hook 触发 → `POST /session/end`。Phase 1 只是标记 status='completed'——Phase 2c 增强为立即返回 + 后台 queue Phase 2 detection task。
>
> Worker 取到 Phase 2 task → 从 SQLite 取该 session 的全部 turns（可能 20-50 turns）→ 取 Phase 1 已知 moments 列表 → 构建 Phase 2 prompt："Here is a complete session…Known moments: [...]. Do NOT re-report these. What was accomplished? Any NEW key moments?" → 调 Anthropic API（max_tokens × 2）→ 解析：overall session assessment + NEW moments → 对每个 new moment 算 content-hash → INSERT OR IGNORE。
>
> 用户稍后可以通过 `/bible-cc:review` 查看 pending moments（含 Phase 1 和 Phase 2 检测到的），编辑或删除。

### 实现顺序

```
2c.1 (session/end) → 2c.2 (Phase 2 prompt) → 2c.3 (PUT/DELETE)
```

| 顺序 | Feature | 理由 | 可并行 |
|------|---------|------|--------|
| **1st** | 2c.1 `/session/end` 增强 | 独立——给现有端点加 queue.put。仅依赖 2b.2（worker） | — |
| **2nd** | 2c.2 Phase 2 Detection | 依赖 2b.1（client）+ 2b.2（worker）。扩展 2b.3 已有链路 | — |
| **3rd** | 2c.3 PUT/DELETE 端点 | 独立——纯 CRUD 端点，不依赖 2c.1/2c.2。可与 2c.1/2c.2 并行 | ✅ |

---

### Feature 2c.1: `/session/end` 异步增强

**Scenario**: Stop hook → `POST /session/end`。Phase 1 逻辑保留（验证 + 标记 completed）。Phase 2c 新增：标记完成后 → `await _detection_queue.put({"session_id": session_id, "phase": 2})` → 立即返回 `{status: "completed", detection: "queued"}`。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 2 retrospective 是 LLM 调用——可能耗时 3-15 秒。如果在 HTTP 请求线程中同步等待，Stop hook timeout（30s）可能触发。异步方案解耦：session 标记优先完成，retrospective 在后台 worker 中进行。即使 worker 崩溃，session 已标记 completed——下次 SessionStart crash recovery 可补偿。 |
| **优先级** | P0 |
| **依赖** | Phase 1b `/session/end`（已有标记逻辑）、2b.2（asyncio.Queue worker）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `05-capture/detection.md` | §2.1 | `POST /session/end` 触发 Phase 2 detection |
| `05-capture/detection.md` | §2.2 | 7 步：取 turns → 已知 moments → prompt → LLM → hash → INSERT |
| `03-daemon/http-api.md` | §3.2 | 响应: `{status: "completed", detection: "queued"}` |
| `03-daemon/http-api.md` | §7 时序约束 | Phase 2c 异步：立即返回 <100ms |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Session 已 completed 但再次 end | `status != 'active'` → `"already_completed"` | 无害 | `daemon.log` 搜索 `already_completed` | 不需恢复 |
| Worker 在 retrospective 完成前崩溃 | Worker restart → task 丢失（已从 queue 取出） | Session 标记 completed 但无 retrospective moments | `daemon.log` 搜索 `detection worker crashed` | Next SessionStart crash recovery 重试 |

**Function-Level Steps**（按实现顺序）:

```python
# server.py

async def session_end(request: SessionEndRequest) -> SessionEndResponse:
    # Phase 1 逻辑（不变）
    conn = _get_db()
    session = get_session(conn, request.session_id)
    if not session:
        raise HTTPException(404)
    if session["status"] != "active":
        return SessionEndResponse(status="already_completed", detection=None)
    
    mark_session_completed(conn, request.session_id)
    
    # Phase 2c: queue retrospective（异步）
    if _app_config.capture.enabled:
        await _detection_queue.put({"session_id": request.session_id, "phase": 2})
        return SessionEndResponse(status="completed", detection="queued")
    
    return SessionEndResponse(status="completed", detection=None)
```

**交付标准**:

- [ ] `/session/end` 标记 completed + queue Phase 2 detection task
- [ ] 立即返回 <100ms（不等待 LLM）
- [ ] `capture.enabled=false` 时仅标记 completed
- [ ] Session 已 completed → `"already_completed"`

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_session_end_queues_phase2_detection` — active → end → queue +1
- [ ] `[Unit] [Pre]` `test_session_end_capture_disabled_no_queue` — enabled=false → 仅标记
- [ ] `[Unit] [Pre]` `test_session_end_already_completed_no_queue` — already completed → detection=null

*意图测试*:
- [ ] `[Unit] [Pre]` `test_session_end_returns_before_detection_completes` — **意图: 异步解耦**。即使 retrospective 耗时 10s，`/session/end` <100ms 返回。
- [ ] `[Unit] [Pre]` `test_session_end_resets_threshold_counter` — **意图: 资源清理**。`del _threshold_state[session_id]`。

---

### Feature 2c.2: Phase 2 Detection Prompt + Logic

**Scenario**: Worker 取到 Phase 2 task → 取全部 turns → 取 Phase 1 已知 moments → 构建 Phase 2 prompt（已知 moments 列表 + "Do NOT re-report these"）→ 调 `detect_moments(phase=2)` → 解析 NEW moments → content-hash → INSERT OR IGNORE。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 1 只看 2-3 turns，遗漏跨 turns 的 long-arc decision。Phase 2 全 session synthesis + gap-fill。Prompt injection（已知 moments）第一层去重 + content-hash UNIQUE 第二层。 |
| **优先级** | P0 |
| **依赖** | 2b.1（Anthropic client）、2b.2（worker 调用 `_process_detection_task`）、1a.2（CRUD）、1a.4（content-hash）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `05-capture/detection.md` | §2.2-2.3 | Phase 2 流程 + 与 Phase 1 关系 |
| `05-capture/detection.md` | §3.2 Prompt | 含 "Do NOT re-report these" + known moments 列表 |
| `05-capture/detection.md` | §5 参数 | max_tokens × 2（自动推导） |
| `CLAUDE.md` | Dedup Strategy | 两层去重 |

**Troubleshooting 设计**: Phase 2 retrospective 错误处理沿用 2b.1 的 Anthropic client 错误处理——API 失败 → log WARNING → return []，不影响 session completed 状态。

**Function-Level Steps**（按实现顺序）:

```python
# daemon/detector.py — 新增 Phase 2 prompt 构建函数

def build_phase2_prompt(all_turns: list[Turn], known_moments: list[Moment]) -> str:
    """构建 Phase 2 retrospective prompt（§3.2）。
    1. "ALREADY detected. Do NOT re-report:" + known moments 摘要
    2. "Full session transcript:" + turns text
    3. "Identify: overall assessment + NEW moments + what to remember"
    """

# server.py — 扩展 2b.3 的 _process_detection_task() 中 phase==2 分支
# 已有的 phase==2 分支取全 session turns 和已知 moments。
# 2c 新增：prompt 使用 build_phase2_prompt（detect_moments 内部调用）。
```

**交付标准**:

- [ ] Phase 2 prompt 含已知 moments + "Do NOT re-report these"
- [ ] Phase 2 detection 成功产生 NEW moments + assessment
- [ ] NEW moments → content-hash dedup → INSERT OR IGNORE
- [ ] LLM 调用失败不影响 session completed 状态

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_build_phase2_prompt_contains_known_moments` — known moments 出现在 "ALREADY detected" 部分
- [ ] `[Unit] [Pre]` `test_build_phase2_prompt_contains_dont_re_report`
- [ ] `[Unit] [Pre]` `test_build_phase2_prompt_excludes_session_start` — Phase 2 不含 SESSION_START
- [ ] `[Unit] [Pre]` `test_phase2_detection_inserts_new_moments` — stub → moments +2
- [ ] `[Unit] [Pre]` `test_phase2_detection_dedup_known_moments` — 相同 hash → INSERT OR IGNORE

*意图测试*:
- [ ] `[Unit] [Pre]` `test_phase2_known_moments_injection_is_first_dedup_layer` — **意图: 两层去重协作**。Prompt injection 让 LLM 不重复报告（省钱），content-hash UNIQUE 兜底（安全）。删掉 prompt injection → LLM 重新产生已知 moments → hash 拦截 → 浪费 API tokens。
- [ ] `[Unit] [Pre]` `test_phase2_failure_does_not_lose_session_completed_status` — **意图: session 标记优先**。Phase 2 detection 失败时 session 已标记 completed。

---

### Feature 2c.3: Moment 编辑/删除端点（PUT/DELETE /daemon/moments）

**Scenario**: `/bible-cc:review` command 需要编辑/删除 pending moments。GET 端点在 2a.4 已实现。

| 属性 | 说明 |
|------|------|
| **理由** | 用户需要对 pending moments 有控制权。GET 已在 2a 实现（因为 2b hook 需要通过它读取 moments）。2c 补充编辑和删除能力。 |
| **优先级** | P1 — review command 前置 |
| **依赖** | 1a.2（moment CRUD）、2a.4（GET /daemon/moments）|

**Function-Level Steps**:

```python
@app.put("/daemon/moments/{moment_id}")
async def update_moment(moment_id: int, body: UpdateMomentRequest):
    """PUT /daemon/moments/{id} → 编辑 title/narrative。仅 flushed=0。"""

@app.delete("/daemon/moments/{moment_id}")
async def delete_moment(moment_id: int):
    """DELETE /daemon/moments/{id} → 删除。仅 flushed=0。"""
```

**交付标准**:

- [ ] `PUT /daemon/moments/{id}` 编辑 title/narrative
- [ ] `DELETE /daemon/moments/{id}` 删除 moment
- [ ] Flushed moment 不可编辑/删除（409 MOMENT_ALREADY_FLUSHED）
- [ ] 不存在的 moment → 404

**测试用例**（实现后编写——Integration tests）:

- [ ] `[Integration] [Post]` `test_put_moment_updates_title`
- [ ] `[Integration] [Post]` `test_delete_moment_removes`
- [ ] `[Integration] [Post]` `test_edit_flushed_moment_returns_409`

---

## 2c 验收标准

- [ ] `/session/end` 异步 mark completed + queue Phase 2 detection（<100ms）
- [ ] Phase 2 prompt 包含已知 moments + "Do NOT re-report these"
- [ ] Phase 2 detection 产生 NEW moments（不重复 Phase 1 已知）
- [ ] Content-hash 两层去重完整生效
- [ ] Turns 截断策略（>100 turns）防止 token 超限
- [ ] PUT/DELETE /daemon/moments 端点可用（GET 在 2a.4 已实现）
- [ ] Flushed moment 不可编辑/删除
