# Moment Detection Hint Timing Plan

**Date**: 2026-06-26

## Context

Phase 2b 的 mid-session detection 已经能识别并保存 key moments，且 hint cursor 去重工作正常。但在 Phase 3a plan review 过程中暴露出三个体验问题：

- Decision moment 能被检测到，但 hint 可能滞后到下一个 user turn 才显示。
- 用户短确认语（如“同意”“接受建议”“这是笔误”）有时被识别为 decision，有时漏掉。
- `deepseek-v4-flash` 的 detection response 偶尔只包含 `ThinkingBlock` 或截断 JSON，导致 `output_tokens` 被消耗在 detection 不需要的可见推理内容上。

这些问题不说明 capture pipeline 失效；它们说明当前触发策略、输出约束和 hint polling 时机还不够贴合“逐条 review 并确认决策”的真实工作流。

## Observations

从实际 session log 和 SQLite buffer 看：

- `方案 B 正确...` 被捕获为 decision，但 detection 在 Stop hook poll 之后完成，因此 hint 延迟到下一次 `UserPromptSubmit`。
- `同意.` 被捕获为 `接受边界测试建议`，因为 detection 在下一次 hook collect 前已经完成，所以 hint 显示及时。
- `我同意.`、`接受建议.`、`同意你的建议.` 等短确认没有形成 pending moment；其中一部分检测请求实际触发了，但 LLM 输出 `ThinkingBlock` 或 invalid JSON，另一部分受 threshold gate 影响。
- 当前实际配置仍是 `commit_threshold_turns=8`、`commit_threshold_chars=16000`，不是预期的 `4 / 2000`。

## Goals

1. Decision hint 尽量出现在用户确认后的同一 assistant turn 末尾，而不是下一轮用户输入下方。
2. Review 场景中的短确认语应稳定捕获为 decision，前提是上一条 assistant message 提出了明确建议、选项或变更。
3. Moment detection 只消费最终结构化结果，不依赖、展示或浪费预算在 visible thinking blocks 上。
4. 保持 Claude Code hook 非阻塞：所有等待必须 bounded，失败只能降级为下轮提示或 pending review。

## Non-Goals

- 不要求每个用户确认都同步阻塞等待 LLM。
- 不要求 detector 理解所有自然语言细节；短确认场景可以通过规则 + 上下文窗口辅助。
- 不在 hook stdout 中直接打印调试信息；用户可见输出仍只走 JSON `systemMessage`。

## Proposed Design

### 1. Stop Hook Bounded Wait

当前 Stop hook 会先 POST `/turn/assistant`，再 poll hints。但如果 `/turn/assistant` 刚 queue 了 detection，Stop 不会等待这个新任务完成。

改造：

- 如果 `_post_assistant_turn()` 返回 `queued=true`，Stop hook 使用短等待窗口 collect hints。
- 初始建议：`stop_hint_wait_seconds=3.0`，`poll_interval=0.25`。
- 若等待超时仍无新 hint，写入 watch，保持现有“下轮 hook 提示”兜底。

预期效果：

- 2-3 秒内完成的 decision detection 会显示在当前 assistant turn 末尾。
- 慢检测仍不阻塞过久，继续由下一次 hook 显示。

### 2. Short Confirmation Fast Path

Threshold-based detection 不适合 review 场景，因为用户可能连续输入非常短的确认语。

新增轻量触发条件：

- 当前 user message 去空白后长度较短，例如 `<= 40` 字符。
- 命中确认模式：`同意`、`接受`、`正确`、`确认`、`采用`、`就这样`、`应该改`、`yes`、`agree`、`accepted`。
- 最近一条 assistant text 包含 proposal signal，例如 `建议`、`方案`、`Issue`、`改为`、`应该`、`我的看法`、`推荐`。

命中后立即 queue Phase 1 decision/accomplishment detection，不走累计阈值。

窗口要求：

- 必须包含上一条 assistant text 和当前 user confirmation。
- 不应回退到很旧的 user turn 作为 anchor。
- 对 tool output 仍只保留 tool marker，不把完整工具输出放入 detection prompt。

预期效果：

- “同意”“接受建议”这类确认不再依赖 8-turn 或 4-turn 阈值。
- LLM 可以从上一条 assistant proposal 中生成有意义的 title/narrative，而不是只看到短确认本身。

### 3. Phase 1 Compact Retry

Phase 2 已经有 invalid JSON retry，Phase 1 也需要同类机制。

触发 retry 的条件：

- response text empty。
- response contains no parseable JSON。
- `stop_reason=max_tokens`。
- response content block types 只有 `ThinkingBlock` 或没有 `TextBlock`。

Retry prompt 约束：

```text
RETRY: Return ONLY one minified JSON object.
No reasoning, no analysis, no markdown.
Max 1 moment.
title <= 8 words.
narrative <= 100 chars.
If uncertain, return {"result":"none"}.
```

注意：这里不是否定 model 内部 thinking 的价值，而是要求 detection API 的可见输出只包含最终 JSON。Moment detection 消费方不需要 visible reasoning。

### 4. Visible Thinking Mitigation

调查当前 model route 是否支持禁用 visible reasoning 的参数，例如：

- JSON mode / structured output。
- `include_reasoning=false`。
- `thinking=disabled`。
- `reasoning_effort=none` 或等价 provider 参数。

如果 provider 不支持，优先选择一个 non-thinking JSON extractor model 作为 `detection.model` 默认值。主对话模型可以继续使用 thinking；detector 是独立消费方，应优化为低延迟、稳定 JSON 输出。

### 5. Threshold Defaults

将默认阈值改成预期值：

- `capture.commit_threshold_turns = 4`
- `capture.commit_threshold_chars = 2000`

同时处理已有用户配置：

- setup/upgrade 不应静默覆盖用户显式配置。
- status 或 debug endpoint 应显示当前实际阈值，避免“代码默认已改但本地 config 仍是旧值”的误判。
- 可提供一次性 migration hint：检测到旧默认 `8 / 16000` 时提示用户是否更新为新默认。

## Implementation Order

1. Add config fields for Stop hint wait and short-confirm detection.
2. Implement Stop bounded wait when `/turn/assistant` queues detection.
3. Add short confirmation classifier and queue decision detection immediately.
4. Add Phase 1 compact retry for empty/invalid/thinking-only responses.
5. Update defaults to `4 / 2000` and surface actual loaded thresholds in status/debug output.
6. Add unit tests and one manual Claude Code review scenario.

## Test Plan

- Stop hook with queued detection finishing within wait window prints hint in same assistant turn.
- Stop hook with slow detection times out, writes watch, and next hook prints the pending hint.
- Short user message `同意.` after assistant proposal queues decision detection immediately.
- Short user message `同意.` without a prior proposal does not create a decision.
- Phase 1 invalid JSON triggers compact retry.
- Phase 1 `ThinkingBlock`-only response triggers compact retry.
- Existing cursor behavior remains unchanged: cursor advances only after successful JSON systemMessage emit.

## Open Questions

- Should short confirmation fast path create a deterministic moment without LLM when the previous assistant proposal has a clear Issue title?
- Should session_start refinement continue to run on every user turn, or only until the first session_start moment is inserted?
- Which detection model or provider parameter best guarantees final JSON without visible reasoning blocks?
