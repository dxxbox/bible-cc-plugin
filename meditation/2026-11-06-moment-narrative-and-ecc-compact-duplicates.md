# Moment Narrative 错误与 ECC 嵌套摘要会话重复问题

> 日期：2026-11-06
> 范围：Phase 3d 收尾后的观测数据（v0.28.0），不涉及 Phase 4 设计裁决
> 状态：W1+P1 已实施；#102 已修正；#109/#116 等重复问题 **暂不修改**，Phase 4 末尾重提

---

## 1. 事件一：Moment #102 的 narrative 与事实不符

### 1.1 现象

`moments` 表 #102：

| 字段 | 值 |
|---|---|
| type | `decision` |
| title | Phase 4 硬冲突裁决: A1/A2/A3 方案确定 |
| narrative（错误） | 用户选择方案A，确认三项硬冲突裁决并同步到文档，为Phase 4实施定下设计方向。 |
| tool_summary（正确） | A1: MCP写~/.bible-cc/mcp-calls.jsonl, daemon debug端点读取; A2: asyncio.gather(return_exceptions=True); A3: L1用BiBLE原生字段, 去重键(domain,doc_id,section_id) |

事实：A1 用户选的是**方案 B**（共享 JSONL），A2 是技术裁决，只有 A3 选了方案 A。

### 1.2 证据链

- 触发：`turn-assistant 7de894f6 chars=966`（Stop hook），排队 `max_seq=865`，无 `include_previous_user`（`server.py:1759`）。
- 检测：`16:31:06 detection LLM ... input_tokens=746 output_tokens=142`。
- 窗口复现（`get_phase1_detection_window(limit=8, max_seq=865)`）：
  - 锚点 = 最新 user turn ≤ 865 → seq 855，内容仅为 `"A"`；
  - 其后 7 条 = seq 859..865，其中 6 条是 `[Used tool: X]`，最后一条是助手总结表。
  - **窗口外**：seq 850 用户答 `"B"`（A1 裁决）、seq 854 助手 "A3 分析：三方案 A/B/C，建议 A"。
- 因此模型看到的唯一用户发言是裸字母 `"A"`；技术细节来自总结表（所以 tool_summary 正确），但"用户选择方案A"是外推。

### 1.3 根因分层

1. 窗口锚定策略假设用户消息自成语义（`buffer.py:294` docstring）；裸字母回答打破假设。
2. 工具 turn 内容为空，8 个槽位中 6 个是 `[Used tool: X]`，挤压上下文。
3. Stop 触发的检测同样以 user turn 为锚，没有"向前包含 assistant 文本"的选项。
4. Prompt 把 DECISION 定义为 "the user confirms a choice"，促使模型给可见的 `"A"` 找归属；narrative ≤160 字进一步压缩成错误归因。

### 1.4 修复（已实施，W1 + P1）

**W1 — 简答回复补入前一条 assistant 文本**（`src/bible_cc_plugin/daemon/buffer.py`）：

- 新增 `_TERSE_REPLY_MAX_CHARS = 10`。
- `get_phase1_detection_window()`：记录触发窗口的 `reply_user`；若其内容 ≤10 字符，查询其之前最近一条 assistant 文本 turn（`tool_name IS NULL/''`、非空内容），插入窗口头部。
- 窗口总数仍受 `limit` 约束（`after_limit` 相应减 1）。
- 仅在窗口锚点就是该简答消息时补入；`include_previous_user` 路径不重复插入。

**P1 — Prompt 防归因**（`src/bible_cc_plugin/daemon/detector.py`）：

- `_PHASE1_SYSTEM_PROMPT` 与 `build_phase1_prompt()` 普通分支加入：

  > Terse replies: if the user's message is a short answer (a single letter or number, "OK", "yes") and the question it answers is NOT in the transcript, do NOT guess what was chosen. Narratives must state only facts visible in the transcript — never claim the user chose something the transcript does not show.

**文档同步**：`docs/sw-design/05-capture/detection.md` 更新触发条件、窗口描述、重叠措辞与 prompt 示例。

**测试**：

- 新增 4 个 buffer 测试（简答补入问题、长消息不补入、`include_previous_user` 不重复、工具 turn 不作为上下文）。
- 新增 1 个 detector prompt 测试。
- 结果：`test_buffer.py + test_detector.py` 111 passed；`tests/unit` 505 passed；`tests/contract` 48 passed；`tests/integration` 24 passed；ruff check/format 通过。

### 1.5 数据修正

#102 narrative 已改为：

> 裁决完成：A1 选方案B（MCP 写共享 JSONL、daemon 读取），A2 用 asyncio.gather，A3 选方案A（BiBLE 原生字段）。

`content_hash` 已按 `SHA-256(session_id\0title\0narrative)` 重算（前缀 `3dc966c6…`）。  
说明：`PUT /daemon/moments/102` 被权限拒绝，改用 SQLite 直接更新，保留 `flushed=0` 守卫。

---

## 2. 事件二：#109/#116 等重复 moment（ECC 嵌套摘要会话）

### 2.1 初始假设与修正

- 初始假设：Claude Code 的 `/compact` 创建了一个摘要子会话，bible-cc 将其当真实会话捕获。
- **修正后的结论**：Claude Code 的 `/compact` 不创建新 session；这些嵌套会话是 **ECC 2.2.3** 启动的独立 `claude -p` 进程。

### 2.2 直接证据（已逐条核实）

| 证据 | 位置 |
|---|---|
| ECC 在 PreCompact 时生成 LLM 摘要 | `~/.claude/plugins/cache/ecc/ecc/2.2.3/scripts/hooks/pre-compact.js:156` |
| ECC 也在 Stop 时触发（上下文 <20% 或每 50 条消息） | `scripts/hooks/session-end.js:226-248` |
| 摘要 prompt 是 ECC 硬编码模板 | `scripts/lib/llm-summary.js:137-169` |
| 启动方式：`spawnSync('claude', ['--model', 'haiku', '-p'], …)` | `scripts/lib/llm-summary.js:172-180` |
| 子进程注入 `ECC_SKIP_LLM_SUMMARY=1`、`ECC_LLM_SUMMARY_SUBPROCESS=1` | `scripts/lib/llm-summary.js:175-180` |
| ECC 自己的 SessionEnd 用该标记跳过嵌套进程 | `scripts/hooks/session-end.js:314` |

Claude Code 文档确认：SessionStart `source` 只有 `startup|resume|clear|compact|fork`；hook payload 没有 `is_sidechain`/`session_type`/`parent_session_id` 等字段；嵌套进程是全新顶层会话，`source=startup` 合法，无法从 payload 区分。

### 2.3 数据库证据

10 个会话的首条 user 消息全部是 ECC 模板：

> "Below is a conversation log from a Claude Code coding session. Create a summary to help the next session quickly understand the context."

| session | 创建时间 | moments | 状态 |
|---|---|---|---|
| bb78fd5b | 2026-11-02 14:51 | 8 | 待推送 |
| 89ee83a2 | 2026-11-06 08:12 | 8 | 待推送 |
| 81a342b6 | 2026-11-06 08:20 | 9 | 待推送 |
| 6f0f1a9e | 2026-11-06 08:22 | 7 | 待推送 |
| 29137035 | 2026-11-06 08:23 | 10 | 待推送 |
| 9a9b4335 | 2026-11-06 08:26 | 9 | 待推送 |
| 55e8a1d2 | 2026-11-06 08:27 | 11 | 待推送 |
| e89f5550 | 2026-11-06 08:30 | 9 | 待推送 |
| eebaa353 | 2026-11-06 08:31:05 | 7 | 待推送 |
| d4dcb3e5 | 2026-11-06 08:31:37 | 8 | 待推送 |

合计 **86 个待推送 moment**；主会话 `7de894f6` 仅 **13 个真实 moment**（87% 为重复）。

每个嵌套会话尝试 flush 7–11 条（日志 `[flush] session=… moments=… bundling…`），只因 BiBLE 当时不可达而未入库。最早一条在 2026-11-02，问题已存在多日。

### 2.4 生命周期

```
ECC PreCompact / Stop 条件触发
  → spawnSync('claude', ['--model', 'haiku', '-p'])  （全新顶层会话，新 session_id）
  → bible-cc SessionStart：source=startup → 当作普通新会话，创建 session 行
      → crash recovery 把主会话 7de894f6（长期 active）当成"未关闭"，注入其 recovery 数据
  → UserPromptSubmit：6KB 摘要 prompt 存为 user turn → 字符阈值 2000 触发 Path A + Path B 检测
  → Stop：3.5KB 摘要存为 assistant turn → 再次触发 Phase 1
  → SessionEnd：对 2-turn 合成会话做 Phase 2 → 再产出 moment → flush 7-11 条
```

### 2.5 根因

1. **hook 契约无法区分用户会话与 ECC 嵌套摘要会话**：嵌套进程 `source=startup`，payload 无专用字段。
2. **摘要文本被当作真实对话再做检测**：摘要本身包含全部决策，检测器自然重新提取。
3. **字符阈值 2000 让 6KB prompt 必然触发检测**：每个嵌套会话 2–4 次 LLM 调用。
4. **content_hash 含 session_id，跨会话重复不拦截**：且各次摘要措辞不同（英文/中文标题），即使去掉 session_id 也只能拦住 3 组完全相同的。
5. **crash recovery 把长期 active 会话当"未关闭"**：每个新会话（含嵌套会话）都注入主会话 recovery 数据；日志 `Collect unclosed session: %s` 打印当前 session，误导诊断。
6. **compact 不分段主会话**：主会话已 934 turns / 5.8M chars；SessionEnd 的 Phase 2 仍处理全量历史（纯文本约 154k chars）。

### 2.6 修复方案（待裁决，暂不实施）

| 方案 | 内容 | 优先级 |
|---|---|---|
| **C1′ 识别 ECC 嵌套进程** | 在 `scripts/hook.py` 的 `main()` 分发前检查 `ECC_LLM_SUMMARY_SUBPROCESS=1` 或 `ECC_SKIP_LLM_SUMMARY=1`，命中则读完 stdin 后 `return 0`。所有 hook 共用入口，一次覆盖；不改 daemon、不加 schema 字段 | P0 |
| **C2′ 清理存量** | 备份 `~/.bible-cc/daemon.db`（`sqlite3 .backup`）后删除 10 个嵌套会话的 86 个 moments；可选同时删除假 sessions/turns | P0 |
| **C3 crash recovery 修正** | `sessions.last_active_at` + 空闲阈值，避免把活着的长会话当崩溃；修正误导日志 | P1 |
| **C4 compact 分段** | 记录 `last_compact_seq`，Phase 2 只处理 compact 之后的 turns | P1 |
| **上游（可选）** | 建议 ECC 在 `spawnSync` 加 `--settings '{"disableAllHooks": true}'`，从根上避免所有插件 hook 被嵌套会话触发；不能直接改 ECC 缓存（更新会覆盖） | 可选 |

残余风险：C1′ 依赖 ECC 内部环境变量约定；若 ECC 未来改名，问题会回归。第二层防御可在 daemon 侧匹配 ECC 模板首行（仅兜底，不推荐做主判据，模板会随版本变化）。

### 2.7 本次决定

- **暂不修改 #109/#116 等重复问题**，不实施 C1′/C2′。
- 在 **Phase 4 末尾**重新提出并裁决。
- 期间避免触发全局 flush，防止 86 条重复写入 BiBLE（当前 BiBLE 不可达，数据仍在本地）。

---

## 3. Phase 4 末尾重提清单

1. 裁决并实施 **C1′**：`hook.py` 环境变量跳过 ECC 嵌套会话；补 pytest（设置两个环境变量之一 → 不发起任何 HTTP 调用、返回 0；正常环境仍走原逻辑）。
2. 裁决并实施 **C2′**：先 `sqlite3 ~/.bible-cc/daemon.db ".backup '<backup>'"`，再删除 86 个重复 moments；确认是否连同 10 个假 sessions/turns 删除。
3. 验证：下次 ECC 触发摘要时，daemon.log 应出现 skip 日志，且不再新增 session。
4. 单独裁决 **C3**（crash recovery 误判）与 **C4**（compact 分段 / Phase 2 全量历史）。
5. 可选：向上游 ECC 提议 `--settings '{"disableAllHooks": true}'`。

---

## 4. 关键文件与查询

- 检测窗口：`src/bible_cc_plugin/daemon/buffer.py` `get_phase1_detection_window()`
- Prompt：`src/bible_cc_plugin/daemon/detector.py` `_PHASE1_SYSTEM_PROMPT` / `build_phase1_prompt()`
- Hook 入口：`src/bible_cc_plugin/scripts/hook.py` `main()`
- 会话/检测：`src/bible_cc_plugin/daemon/server.py` `/turn/user`、`/turn/assistant`、`/session/end`
- ECC 源码：`~/.claude/plugins/cache/ecc/ecc/2.2.3/scripts/lib/llm-summary.js`

查询嵌套会话：

```sql
SELECT substr(t.session_id,1,8), s.created_at, COUNT(m.id)
FROM turns t
JOIN sessions s ON s.session_id=t.session_id
LEFT JOIN moments m ON m.session_id=t.session_id
WHERE t.seq=1 AND t.role='user'
  AND t.content LIKE 'Below is a conversation log from a Claude Code coding session.%'
GROUP BY t.session_id;
```
