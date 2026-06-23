# Phase 0–2 手动测试计划

> 2026-06-16 | 17 endpoints + 8 MCP tools | 11 Claude Code 实操 + 2 纯 API

---

## 环境准备

```bash
# 确保 daemon 运行
uv run python -m bible_cc_plugin.scripts.daemon status
# 若未运行：uv run python -m bible_cc_plugin.scripts.daemon start

# 确认 health
curl -s http://127.0.0.1:9777/daemon/health | python3 -m json.tool
# expect: {"status":"ok","sqlite":{"integrity":"ok",...}}
```

所有场景在 **Claude Code 会话**中执行，观察 transcript 中的 hook 输出。

---

## S1: 新会话 → SessionStart Hook → 空 Context Injection

**目标**：验证首次 SessionStart 完整的 hook→daemon 链路，确认空 `<relevant-memories>` 块

**操作**：
1. 启动 Claude Code（触发 SessionStart hook）
2. 观察 transcript 中是否出现 `<relevant-memories></relevant-memories>`
3. 或通过 `/ssm` 查看系统消息

**Expect**：
- Hook 日志（daemon.log）含 `POST /session/start... OK (is_new=true)`
- Hook 日志含 `POST /context/inject... OK`
- Context 块为空（`<relevant-memories></relevant-memories>`）——新 session，无历史数据
- Session 已注册到 daemon

**验证**（另开 terminal）：
```bash
curl -s http://127.0.0.1:9777/daemon/sessions | python3 -m json.tool | head -10
# expect: 新 session 存在，status:active
```

---

## S2: 对话中 Detection → Hint 出现在 Transcript

**目标**：验证 Phase 1 mid-session detection 全链路——阈值触发→LLM 检测→moment 写入→hint 输出

**操作**：
1. 启用 stub 模式：`DETECTOR_TEST_MODE=1 uv run python -m bible_cc_plugin.scripts.daemon restart`
2. 在 Claude Code 中输入 8 条有实质内容的用户消息：
   - "Let's design the authentication module."
   - "I think JWT tokens are better than session cookies."
   - "Let's implement the login endpoint first."
   - （连续 8 条）...
3. 观察第 9 条消息后是否出现 hint

**时序说明**（关键——理解 async detection 的核心）：
```
Turn 8 → UserPromptSubmit hook → POST /turn/user
  → check_threshold: turns=8 >= 8 → trigger → queue.put({phase:1}) → return {queued:true}
  → _print_hints: 此时尚无 moments，不输出 hint
  → 后台 worker picks up task → LLM detect → INSERT moment

Turn 9 → UserPromptSubmit hook → POST /turn/user
  → _print_hints: 读到 Turn 8 产生的 moment → stdout → CC inject
```

**Expect**：
- Turn 8 返回 `queued:true`（阈值在第 8 条消息触发）
- Worker 后台处理 task → stub 返回 moment "Stub Decision" → 写入 SQLite
- **Turn 9 的 hook** 调 `GET /daemon/moments` → 发现新 moment → print hint：
  ```
  ⎿ ⏳ Captured: "Stub Decision" — Decision. /bible-cc:review
  ```
- daemon.log 含 `detect: phase=1 session=... type=decision dedup=INSERTED`

**验证**：
```bash
curl -s "http://127.0.0.1:9777/daemon/moments?session_id=<SESSION_ID>" | python3 -m json.tool
# expect: 至少 1 个 moment, moment_type:decision
```

---

## S3: `/clear` → Context Injection with Turns Summary

**目标**：验证 /clear 后 SessionStart 注入本地 buffer 数据（turns summary + moments）

**操作**：
1. 在 Claude Code 中继续对话，确保已有一些 turns
2. 输入 `/clear`

**Expect**：
- `/clear` 触发 SessionStart hook
- 系统消息中 `<relevant-memories>` **非空**——含 turns summary（之前对话的摘要）
- 如果已有 moments，也出现在注入内容中
- daemon session 仍为同一个，turns 保留
- `_threshold_state` 被 reset（从新开始计数）

**验证**（/ssm 查看系统消息）：
```
SessionStart:clear hook success: <relevant-memories>
[Previous session summary...]
[Turns: ...]
</relevant-memories>
```

---

## S4: `/bible-cc:review` — 查看并管理 Pending Moments

**目标**：验证 review slash command 可用 + moment CRUD

**操作**：
1. 在 Claude Code 中输入 `/bible-cc:review`
2. 观察输出——应列出 pending moments（type, title, detected_at）
3. （若 daemon 端口非 9777，需在 curl 命令中调整）

**Expect**：
- 输出 JSON 格式的 moments 列表（来自 `GET /daemon/moments`）
- 每个 moment 含 id, type, title, narrative, detected_at
- 如果 moment 已被编辑（S2 后手动 PUT），显示新 title

**手动编辑 moment**：
```bash
M_ID=1  # 替换为实际 moment id
curl -s -X PUT "http://127.0.0.1:9777/daemon/moments/$M_ID" \
  -H "Content-Type: application/json" \
  -d '{"title":"JWT selected for auth","narrative":"Team chose JWT over session cookies."}'
```
再次 `/bible-cc:review` → title 已更新

**手动删除 moment**：
```bash
curl -s -X DELETE "http://127.0.0.1:9777/daemon/moments/$M_ID"
```
再次 `/bible-cc:review` → 该 moment 已消失

---

## S5: Session 结束 → Phase 2 Retrospective

**目标**：验证 Stop hook → `/session/end` → Phase 2 detection 全链路

**操作**：
1. 在 Claude Code 中继续工作（保持有实质内容的对话）
2. 结束 Claude Code 会话（`/exit` 或 Ctrl+C）

**Expect**：
- Stop hook → `POST /session/end` 返回 `detection:"queued"`
- daemon.log 含 `session/end ... completed`
- daemon.log 含 Phase 2 detection 日志：
  - `get_all_session_turns: session=... → N turns`
  - `detection LLM: model=... phase=2 latency=...ms`
  - `detection complete: session=... inserted=N dedup=N`
- Phase 2 prompt 含已知 moments + "Do NOT re-report"
- Phase 2 可能产生 NEW moment（Phase 1 遗漏的全局决策）
- Session 状态变为 `completed`

**验证**（另开 terminal）：
```bash
curl -s http://127.0.0.1:9777/daemon/sessions | python3 -c "
import sys,json
for s in json.load(sys.stdin):
    print(f\"{s['session_id'][:12]}... status={s['status']} turns={s['turn_count']}\")
"
# expect: session 状态为 completed
```

---

## S6: Crash Recovery — 模拟异常终止

**目标**：验证 daemon 重启后检测到未关闭 session → 恢复数据

**操作**：
1. 在 Claude Code 中开始对话，写入几条有意义的 turn
2. 不通过 `/exit` 退出——直接 `kill -9` Claude Code 进程（或强制关闭终端）
3. Stop hook **未触发**——session 仍为 `active`
4. 重新启动 Claude Code

**Expect**：
- SessionStart hook → `POST /session/start` → recovery 字段非 null：
  ```json
  {"session_id":"xxx","is_new":true,"recovery":{"unclosed_sessions_found":1,"moments_recovered":0}}
  ```
- 系统消息 `<relevant-memories>` 包含 crash recovery turns：
  ```
  [Recovered from prior session]
  Previous session had N turns.
  [user] work in progress...
  ```
- 注意：未关闭的 session **仍为 active 状态**——不会自动 mark_completed。
  只有手动 `POST /session/end` 才会标记。crash recovery 是**数据读取**（inject turns），
  不是**状态修复**（mark completed）。
- daemon.log 含 `crash recovery scan: N unclosed sessions`

---

## S7: Hint Format 切换

**目标**：验证四种 hint_format 在 transcript 中的不同呈现

**操作**：
1. 修改 config.json 中 `capture.hint_format` 值
2. 重启 daemon：`uv run python -m bible_cc_plugin.scripts.daemon restart`
3. 在 Claude Code 中触发一次 detection（8 turns）
4. 观察 hint 格式变化
5. 依次测试 4 种 mode

**Expect**：

| hint_format | Transcript 中 hint 示例 |
|-------------|------------------------|
| `quote_with_command` | `⎿ ⏳ Captured: "Use JWT" — Decision. /bible-cc:review` |
| `quote_only` | `⎿ ⏳ Captured: "Use JWT" — Decision.` |
| `command_only` | `⎿ ⏳ Key moment captured (Decision). /bible-cc:review` |
| `narrative` | `⎿ ⏳ Captured decision: Use JWT. JWT chosen over session cookies…` |

---

## S8: Graceful Degradation — Daemon 不可达

**目标**：验证 daemon down 时 Claude Code 正常工作

**操作**：
1. 停止 daemon：`uv run python -m bible_cc_plugin.scripts.daemon stop`
2. 在 Claude Code 中输入消息
3. 观察——应无 crash、无阻塞、无错误提示
4. 重新启动 daemon：`uv run python -m bible_cc_plugin.scripts.daemon start`
5. 再次输入消息——hook 恢复正常

**Expect**：
- Hook 日志含 `turn-user daemon unreachable → skipping`
- Claude Code 正常响应，不受 daemon 状态影响
- Daemon 恢复后 hook 自动恢复

---

## S9: Debug / Diagnose（纯 API）

**目标**：debug endpoints 返回正确数据（仅供运维，非 Claude Code 场景）

```bash
BIBLE_CC_DEBUG=1 uv run python -m bible_cc_plugin.scripts.daemon restart

# Schema
curl -s http://127.0.0.1:9777/daemon/debug/schema | python3 -m json.tool | head -20

# Table row counts
curl -s "http://127.0.0.1:9777/daemon/debug/tables/moments?limit=10" | python3 -c "import sys,json;d=json.load(sys.stdin);print(f'total={d[\"total\"]}')"

# Detection stats
curl -s http://127.0.0.1:9777/daemon/debug/detections/stats | python3 -m json.tool
# expect: {"total":N,"phase1":N,"dedup_hits":N,"avg_latency_ms":N}

# Turns by session
curl -s "http://127.0.0.1:9777/daemon/debug/turns?session_id=<ID>&limit=5" | python3 -m json.tool

# 非 debug 模式 404
BIBLE_CC_DEBUG=0 uv run python -m bible_cc_plugin.scripts.daemon restart
curl -s http://127.0.0.1:9777/daemon/debug/schema
# expect: 404
```

---

## S10: 边界 / 异常

| # | 场景 | Expect |
|---|------|--------|
| 10.1 | `capture.enabled=false` + 8 turns | 全部 `queued:false`，不产生 moment |
| 10.2 | `capture.enabled=false` + session end | `detection:null` |
| 10.3 | SessionStart 无 session_id | daemon 启动但 session registration 延迟 |
| 10.4 | Turn 写入不存在的 session | hook 返回 skipping |
| 10.5 | 空 message 的 turn | turn 写入但 `queued:false`，阈值不递增（`req.message` 为空 → 跳过 `check_threshold`） |
| 10.6 | 超大 tool_output（>5000 chars） | 完整存入 turns 表，LLM 提取摘要 |
| 10.7 | 空 tool_output | turn 写入但 `queued:false`（与 10.5 同理） |

---

## S11: MCP Tools — BiBLE Atlas 不可达时降级

**目标**：验证 MCP server 的 6 个 BiBLE tools 在 BiBLE Atlas 不可达时返回错误但 Claude Code 继续工作

**操作**：
1. 确保 daemon 运行（daemon 不依赖 BiBLE）
2. 在 Claude Code 中说："search bible memory for 'authentication'"
3. 触发 `bible_memory_search` MCP tool
4. 验证返回错误（而非 crash）

**Expect**：
- MCP tool 返回 `{"ok":false,"error":{"code":"BIBLE_UNREACHABLE","message":"..."}}`
- Claude Code 继续正常响应——"BiBLE Atlas 似乎不可达，我会继续基于当前上下文回答"
- Hooks 照常工作（hook 不依赖 BiBLE）
- Daemon health 中 `bible_connectivity.reachable:null`

**如果 BiBLE Atlas 可达，则**：
- MCP tools 返回正常搜索结果
- `bible_memory_search/bible_memory_save/bible_memory_get`
- `bible_knowledge_search`
- `bible_skill_search/bible_skill_get`

---

## S12: `/compact` — Context 压缩后恢复

**目标**：验证 `/compact` 与 `/clear` 行为一致——reset threshold + inject context

**操作**：
1. 在 Claude Code 中继续对话，累计 ≥5 turns
2. 输入 `/compact`

**Expect**：
- 触发 SessionStart hook（与 `/clear` 相同的事件）
- `reset_threshold(session_id)` 被调用——阈值计数器从零开始
- `<relevant-memories>` 包含 turns summary（与 S3 一致）
- daemon session 仍存在，turns 保留
- 与 `/clear` 的关键区别：`/compact` 是 Claude Code 内部的 token 压缩，
  对 daemon 而言两者行为完全一致

---

## S12: MCP Tools — BiBLE 降级（Phase 2 skeleton）

**目标**：验证 6 active + 2 postponed MCP tools 在 BiBLE 不可达时返回结构化错误

**操作**（Claude Code 中）：
1. 确保 `.mcp.json` 存在且 `BIBLE_ATLAS_BASE_URL` 指向不可达地址
2. 输入："search bible memory for 'authentication design'"
3. Claude Code 模型自动调用 `bible_memory_search` tool
4. 输入："delete bible memory '/memories/old'"
5. 输入："list all bible knowledge entries"

**Expect**：

| Tool | 返回 |
|------|------|
| `bible_memory_search` | `{"error":"BiBLE Atlas not yet connected (Phase 3)...","suggestion":"Use /bible-cc:review..."}` |
| `bible_knowledge_search` | 同上（Active → degradation） |
| `bible_skill_search` | 同上 |
| `bible_memory_save` | 同上 |
| `bible_memory_get` | 同上 |
| `bible_skill_get` | 同上 |
| `bible_memory_delete` | `{"error":"...not yet available...","detail":"...V4 API endpoint..."}` |
| `bible_knowledge_list` | 同上（Postponed） |

- Claude Code 读取 **6 个 active tool** 的错误后继续正常工作——不 crash、不阻塞
- Postponed tools 明确告知 "not yet available"，引导 `/bible-cc:status`
- `BIBLE_ATLAS_BASE_URL` 未设置 → `"BiBLE Atlas not configured"` + setup 引导

**验证 MCP server 启动日志**（stderr）：
```
[mcp:server] starting on stdio — 8 tools registered
[mcp:server] BiBLE base_url=http://localhost:5555
[mcp:server] tools: bible_memory_search, bible_memory_save, ...
```

---

## 覆盖矩阵

| Scenario | 类型 | 关键验证点 |
|----------|------|---------|
| S1 | Claude Code | SessionStart hook + 空 context |
| S2 | Claude Code | Detection pipeline + hint 出现 |
| S3 | Claude Code | /clear context injection + turns summary |
| S4 | Claude Code | /bible-cc:review + moment CRUD |
| S5 | Claude Code | Stop hook + Phase 2 retrospective |
| S6 | Claude Code | Crash recovery + data 恢复 |
| S7 | Claude Code + API | 4 种 hint format |
| S8 | Claude Code | Daemon down → graceful skip |
| S9 | API | Debug endpoints (5) |
| S10 | Claude Code + API | 边界 / 异常 (7 cases) |
| S11 | Claude Code | /compact context injection + threshold reset |
| S12 | Claude Code + MCP | 8 MCP tools 降级（BiBLE 不可达 + postponed） |
