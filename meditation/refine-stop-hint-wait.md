# Refine: Stop Hook 3.5s Hint Wait

> 日期: 2026-06-26
> 状态: plan
> 父讨论: Phase 3a review → hint timing 优化

## 问题

Detection 是 async 的（1-6s latency），但 Stop hook 只等 0.5s 就离开。detection 完成后，hint 必须等到下一轮 UserPromptSubmit 才能显示，造成感知上的"滞后"。

## 方案

Stop hook 在 `_post_assistant_turn()` 返回 `queued=true` 时，用可配的等待窗口 poll hints。窗口内完成就立即输出，未完成就写 hint_watch 兜底。

**不影响用户体验**：因为 Stop hook 在 assistant 回复之后、用户阅读 assistant 消息的过程中执行——等待发生在阅读时间里，视觉上 hint 和 assistant 回复在同一帧。

## 改动点

### 1. `config.py` — `CaptureConfig` 新增字段

```python
class CaptureConfig(BaseModel):
    ...
    stop_hint_wait_seconds: float = 3.5  # Stop hook hint poll window when detection queued
```

### 2. `hook.py` — Stop hook 逻辑调整

`_STOP_HINT_WAIT_SECONDS` 常量删除，改为从 config 读取 `capture.stop_hint_wait_seconds`。

`_handle_turn_stop` 中 `body.get("queued")` 分支：

```python
body = _post_assistant_turn(session_id, message, base_url)

if body.get("queued"):
    # detection 刚入队，用可配窗口 poll——detection 大概率在窗口内完成
    wait = config.capture.stop_hint_wait_seconds
    printed = _print_hints(
        session_id, base_url, config.capture.hint_format,
        wait_seconds=wait, poll_interval=0.25,
        hook_event_name="Stop",
    )
    if printed > 0:
        return  # hint 成功输出, 不需要 hint_watch
    # 窗口内没等到 → 写 hint_watch 兜底, 下轮 hook 捡起
    _write_hint_watch(session_id)
    return

# 以下为旧逻辑: queued=False 时, 仍有 hint_watch 等待
watch = _read_hint_watch(session_id)
...
```

合并后的完整逻辑：
1. `queued=true` → poll 3.5s → 有 hint 直接输出, 无 hint 写 hint_watch 返回
2. `queued=false, watch 存在且未过期` → poll min(0.5s, remaining_TTL)
3. `queued=false, watch 不存在` → poll once (wait=0)

### 3. `hooks.json` — Stop timeout

```json
"Stop": [{
  "command": "uv run python -m bible_cc_plugin.scripts.hook turn-stop --session-id \"$CLAUDE_SESSION_ID\"",
  "timeout": 10000
}]
```

3000ms → 10000ms，确保 3.5s wait + API 调用不被截断。

### 4. 测试

- `test_config.py`: 验证 `stop_hint_wait_seconds` 默认值 = 3.5
- `test_hook.py`: `_post_assistant_turn` 返回 `queued=true` 时调用 `_print_hints(wait_seconds=3.5, poll_interval=0.25)`
- `test_hook.py`: 窗口超时无 hint → 写入 hint_watch 兜底
- `test_hook.py`: `queued=false` 时行为不变（0 wait）

## 不变的部分

- `/turn/user` 仍然触发 detection（保留 assistant 思考期的 head start）
- hint_watch + cursor 机制不变（兜底, 下轮 hook 捡起）
- UserPromptSubmit / PostToolUse 的 hint poll 逻辑不变

## 验收

1. `uv run pytest tests/unit/test_config.py tests/unit/test_hook.py -v` 通过
2. 实际运行 session，`body.get("queued")` 时 hint 大概率出现在 assistant 回复下方
3. 无 detection queued 时 Stop hook 行为不变（无额外等待）
