# 05-capture/hook-flow.md — Hook → Buffer 数据流（L3）

> `/turn/user`、`/turn/assistant` 和 `/turn/tool` 的数据接收、存储、阈值检测触发逻辑。

---

## 1. `/turn/user` — 用户消息

### 1.1 请求

```json
{
  "session_id": "abc-123",
  "message": "Let's use PostgreSQL for auth"
}
```

### 1.2 处理

```
1. 验证 session_id 存在于 sessions 表（不存在则 404）
2. INSERT INTO turns (session_id, seq, role, content)
   → seq = session_seq[session_id] += 1（内存计数器，见 §1.3）
   → role = "user"
3. 更新 sessions 表: turn_count += 1, buffered_chars += LEN(message)
4. 检查阈值: if turn_count >= commit_threshold_turns OR buffered_chars >= commit_threshold_chars
   → queue Phase 1 detection task
   → 重置计数器
5. 返回 {turn_id, queued: true/false}（立即返回）
```

### 1.3 阈值计数器（内存变量）

阈值计数器是 daemon 内存中的 per-session 变量（非 DB 列），daemon 重启后归零。

```
# per-session memory state, keyed by session_id
_threshold_state: dict[str, dict] = {}  # session_id → {turns, chars}

def check_threshold(session_id: str, turns: int, chars: int) -> bool:
    state = _threshold_state.setdefault(session_id, {"turns": 0, "chars": 0})
    state["turns"] += turns
    state["chars"] += chars
    if state["turns"] >= commit_threshold_turns or state["chars"] >= commit_threshold_chars:
        state["turns"] = 0
        state["chars"] = 0
        return True
    return False
```

daemon 重启后 dict 清空，所有 session 的计数器归零。

### 1.4 Session 生命周期对计数器的影响

| 事件 | 行为 |
|------|------|
| `/clear` | SessionStart 触发 → 重置该 session 的阈值计数器（turns=0, chars=0）。buffer 中已有 turns 保留，Phase 2 不受影响。 |
| `/compact` | 同 `/clear`——重置计数器，保留 turns 数据。 |
| `/exit` | Stop hook → `/session/end` → Phase 2 检测 + flush → 清理该 session 的计数器（`del _threshold_state[session_id]`）。 |

sessions 表中的 `turn_count` / `buffered_chars` 是全生命周期计数器（用于 status 展示），与阈值检测无关。

---

## 2. `/turn/assistant` — Assistant 最终文本

### 2.1 请求

```json
{
  "session_id": "abc-123",
  "message": "I checked the API contract and found last_assistant_message."
}
```

### 2.2 处理

```
1. 验证 session_id
2. INSERT INTO turns (session_id, seq, role, content)
   → role = "assistant"
3. 更新 sessions 表: turn_count += 1, buffered_chars += LEN(message)
4. 检查阈值: if turn_count >= commit_threshold_turns OR buffered_chars >= commit_threshold_chars
   → queue Phase 1 detection task
5. 返回 {turn_id, queued: true/false}（立即返回）
```

来源：Claude Code `Stop` hook stdin 的 `last_assistant_message` 字段。该字段是纯 assistant text，不包含 UI 装饰字符。

---

## 3. `/turn/tool` — 工具调用

### 3.1 请求

```json
{
  "session_id": "abc-123",
  "tool_name": "Bash",
  "arguments": {"command": "pytest tests/"},
  "output": "<full tool output, potentially very long>"
}
```

### 3.2 处理

```
1. 验证 session_id
2. INSERT INTO turns (session_id, seq, role, content, tool_name, tool_arguments, tool_output)
   → role = "assistant"
   → 完整 tool output 存入 turns 表
3. 更新 sessions 表: turn_count += 1, buffered_chars += LEN(output)
4. 不触发 Phase 1 detection；`queued` 默认 false
5. 返回 {turn_id, queued: false}（立即返回）
```

### 3.3 Tool Output 与 Detection

```
完整 output → turns 表存储
Phase 1/2 detection prompt → 默认排除 arguments/output，只保留 tool_name
未来配置白名单 → 可选择允许特定工具 output 进入 detection
```

**不机械截断。** 完整 output 保留供 review、诊断和未来可配置 detection 策略使用；默认 detection 不读取 tool output。

---

## 4. 边界条件

| 场景 | 行为 |
|------|------|
| session 不存在 | 返回 404 SESSION_NOT_FOUND |
| capture.enabled = false | 正常写入 turn，不触发检测 |
| bypass session | 不写入 turn，返回 {queued: false, bypassed: true} |
| 空 message | 写入 turn，不递增阈值计数器 |

---

## 5. 参考文档

- [`../../02-interfaces.md`](../02-interfaces.md) — `/turn/user`, `/turn/assistant`, `/turn/tool` 完整 spec
- [`../../03-daemon.md`](../03-daemon.md) — SQLite turns 表、阈值
- [`detection.md`](detection.md) — Phase 1 检测详细设计
