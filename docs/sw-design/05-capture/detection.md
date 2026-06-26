# 05-capture/detection.md — Moment Detection（L3）

> Phase 1/2 检测的完整设计：prompt 模板、LLM 调用参数、阈值触发、两层去重、结构化输出。

---

## 1. Phase 1: Mid-Session Detection

### 1.1 触发条件

用户消息阈值到达（≥ `commit_threshold_turns` 或 ≥ `commit_threshold_chars`，先到达者为准），且 `mid_session_detection=true`。

`turn-tool.output` 和 `turn-tool.arguments` 默认不触发 Phase 1 detection，也不进入 detection prompt。工具输出仍完整存储，供人工 review、未来配置白名单或其他非默认流程使用。

### 1.2 流程

```
1. 从 turns 表取最近 2-3 turns（覆盖上下文）
2. 构建 prompt（见 §3.1）
3. LLM call（model from detection config, max_tokens=512, temperature=0.0）
4. 解析结构化输出
5. 如果 result != "none":
     → content-hash = SHA-256(session_id + title + narrative)
     → INSERT OR IGNORE INTO moments
     → 如果 mid_session_upload: flush (flushed=1)
     → 输出 moment hint
6. 重置阈值计数器
```

### 1.2.1 数据完整性：Assistant Response

当前 hook 覆盖范围：

| 数据 | Hook | 状态 |
|------|------|------|
| 用户消息 | UserPromptSubmit → `prompt` 字段 | ✅ 已捕获 |
| 工具调用 + 输出 | PostToolUse → `tool_name` + `tool_arguments` + `tool_output` | ✅ 已捕获；arguments/output 默认不进 detection |
| Assistant 文本回复 | Stop → `last_assistant_message` 字段 | ✅ 已捕获 |

Claude Code `Stop` hook stdin 提供 `last_assistant_message`，由 hook bridge 写入 `/turn/assistant`。
Detection prompt 因此默认基于 user 消息 + assistant 最终文本 + tool name（不含 tool arguments/output）。

### 1.3 异步模型

```
POST /turn/user → insert turn → inc counter → if threshold: push task to queue → return {queued: true}
                                                              ↓
                                                async worker picks up → runs 1.2 → saves moment → prints hint
```

daemon 内部内存队列（`asyncio.Queue`），非 Celery。

### 1.4 重叠窗口去重

滑动窗口有重叠（last 2-3 turns）。`DECISION` / `ACCOMPLISHMENT`
使用 content-hash 去重（Layer 2）覆盖此场景。

`SESSION_START` 使用 user-turn anchor 去重：Phase 1 窗口中第一个 user turn
的 `seq` 写入 `moments.turn_range_start`，代表发起工作意图的用户输入。
如果后续 user-triggered detection 仍识别出同一 anchor 的
`SESSION_START`，则更新已有 pending moment 的 `title` / `narrative` /
`turn_range_end`，不插入新 moment。若该 anchored moment 已经 flushed，
后续检测跳过，避免修改已发送到 BiBLE 的内容。

---

## 2. Phase 2: Session End Retrospective

### 2.1 触发条件

`POST /session/end`（Stop hook）。

### 2.2 流程

```
1. 取 session 的所有 turns（tool arguments/output 默认排除，只保留 tool name）
2. 取 Phase 1 已检测 moments
3. 构建 prompt（§3.2）——含已知 moments 列表
4. LLM call（max_tokens=1024, temperature=0.0）
5. 解析输出：compact assessment + NEW key moments
   - 若输出不是合法 JSON，使用更严格的 compact JSON prompt 重试一次
6. 对 new moment: content-hash dedup → INSERT OR IGNORE
7. 打包所有 unflushed moments + retrospective → POST BiBLE Atlas
```

### 2.3 与 Phase 1 的关系

- synthesis + gap-fill，不是重新检测
- prompt 含 Phase 1 已检测 moments，LLM 不重复报告
- 可能发现 Phase 1 遗漏的 moments（全局上下文）
- Phase 2 prompt 不含 `SESSION_START`：session 范围定义只在 mid-session 检测，retrospective 聚焦决策和成果

---

## 3. Prompt 模板

### 3.1 Phase 1 Prompt

```
You are analyzing a conversation between a user and an AI agent.
Identify if any KEY MOMENTS occurred in these recent turns.

Key moment types:
- SESSION_START: the user defines the topic/scope of work
- DECISION: the user confirms a choice, approach, or design direction
- ACCOMPLISHMENT: something was completed, verified, and accepted

Do NOT flag:
- Intermediate bug fixes or error corrections
- Exploratory discoveries (unless user explicitly confirms importance)

Recent conversation:
{turns_text}

For each key moment found, provide:
- type: one of the above
- title: one-line summary
- narrative: 2-4 sentences describing what happened and why it matters

If no key moment occurred, output: {"result": "none"}
```

### 3.2 Phase 2 Prompt

```
You are reviewing a COMPLETE conversation between a user and an AI agent.
The session has ended. Provide a synthesis.

The following key moments were ALREADY detected during the session.
Do NOT re-report them. Only report NEW moments not covered below:

{already_detected_moments_list}

Full session transcript:
{all_turns_text}

Key moment types (subset of mid-session; SESSION_START excluded):
- DECISION: the user confirms a choice, approach, or design direction
- ACCOMPLISHMENT: something was completed, verified, and accepted

Do NOT flag:
- Intermediate bug fixes or error corrections
- Exploratory discoveries (unless user explicitly confirms importance)

Now identify:
1. Any ADDITIONAL key moments missed by mid-session detection
2. A compact assessment of what was accomplished

Output constraints:
- Return at most 3 moments.
- Use one short sentence per narrative (<=160 chars).
- Keep assessment <=200 chars.
- Output only valid compact JSON.
```

---

## 4. 结构化输出

```json
{
  "result": "moment" | "none",
  "moments": [
    {
      "type": "decision",
      "title": "PostgreSQL for auth storage",
      "narrative": "Team decided to use PostgreSQL for auth. SQLite considered but PostgreSQL chosen for concurrent write support.",
      "tool_summary": "migration executed successfully"
    }
  ],
  "assessment": "The session focused on implementing auth module. Key decisions: PostgreSQL, JWT tokens. Rate limiting implemented."
}
```

> Prompt 中 moment type 用大写（`DECISION`）指引 LLM；结构化输出和 `moments` 表存储用小写（`"decision"`）。解析器做大小写归一化。

---

## 5. LLM 调用参数

| 参数 | Phase 1 | Phase 2 |
|------|---------|---------|
| model | detection.model (default deepseek-v4-flash) | same |
| max_tokens | detection.max_tokens (default 512) | detection.max_tokens × 2（自动推导，不单独配置） |
| temperature | 0.0 | 0.0 |
| API key | ANTHROPIC_API_KEY (from env) | same |

---

## 6. 参考文档

- [`hook-flow.md`](hook-flow.md) — 阈值触发逻辑
- [`flush.md`](flush.md) — moment flush 到 BiBLE
- [`../../03-daemon.md`](../03-daemon.md) — content-hash dedup、SQLite schema
- [`../../../CLAUDE.md`](../../../CLAUDE.md) — Moment Detection Design、Dedup Strategy
