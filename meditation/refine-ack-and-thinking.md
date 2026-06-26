# Refine: 短回复过滤 + DeepSeek ThinkingBlock 修复

> 日期: 2026-06-26
> 状态: plan
> 父讨论: Phase 3a review → 短回复识别不一致 + detection 空响应

## 问题 1: 短确认回复被浪费地送检

用户说"同意"/"我同意"/"接受建议"等纯确认时，`/turn/user` 立即入队 decision detection。LLM 无法从 3-9 字的确认中提取 meaningful decision title，返回空响应或截断 JSON。日志显示当前 session 中 0/5 次短回复成功识别为 decision。

## 问题 2: DeepSeek ThinkingBlock 耗尽 max_tokens

DeepSeek V4 Flash 默认开启 thinking，`max_tokens=512` 同时限制 thinking + output。模型经常把 512 tokens 全花在 ThinkingBlock 上（日志: `stop_reason=max_tokens content_count=1 block_types=['ThinkingBlock']`），输出空文本或截断 JSON。当前 session 中 7 次调用浪费：5 次空响应 + 2 次截断 JSON，每次 ~5-6s + ~500 tokens。

---

## 方案 1: 纯确认回复跳过 decision detection

### 关键词集合

`detector.py` 顶层定义 `_PURE_ACKNOWLEDGMENT: frozenset[str]`，覆盖以下类别：

| 类别 | 示例 |
|------|------|
| 纯语气词 | 同意, 好的, 可以, 行, 好, 嗯, 对, 是的, 没错, ok, yes, sure, LGTM |
| 含轻量主语 | 我同意, 我接受, 我认可, 我赞成, 我也同意 |
| 建议/方案系 | 接受建议, 同意方案, 采纳, 采纳建议, 接受这个方向 |
| 肯定评价 | 这个可以, 这个方案行, 有道理, 说得对, 确实 |
| 收到/理解 | 收到, 了解, 明白, 懂了, 知道了, got it |
| 认可/赞同 | 认可, 赞成, 赞同, 我认可, 说得没错 |
| 通用确认 | 没问题, 就这样, 就这么办, 按这个来, 听你的 |
| 完成/就绪 | 好了, 搞定了, 完成了 |
| 正面评价 | 不错, 很好, 蛮好, 挺好, 非常好 |
| 语气词变体 | 好呀, 对呀, 行吧, 行啊, 也行, 都行 |
| 英文 | go ahead, go for it, ack, acknowledged |
| 批准型 | 批准, 通过, 准许, 允许, 许可, 不反对, 无异议 |
| 命令式 | 做吧, 开始吧, 动手吧, 执行吧, 就用这个 |

### 匹配函数

```python
def _is_pure_acknowledgment(message: str) -> bool:
    """Strip trailing punctuation then match against PURE_ACKNOWLEDGMENT."""
    stripped = re.sub(r"[。.!！？?～~…,，]+$", "", message.strip()).strip()
    return stripped in _PURE_ACKNOWLEDGMENT
```

### 集成点

`server.py` `/turn/user` 中，decision detection 入队前判断：

```python
from bible_cc_plugin.daemon.detector import _is_pure_acknowledgment

if _is_pure_acknowledgment(req.message):
    # 纯确认——跳过 decision detection，留给 /turn/assistant
    pass
else:
    # 正常入队 decision detection
```

session_start detection 不受影响（始终入队）。

---

## 方案 2: 禁用 ThinkingBlock + 增加 max_tokens

### 改动

`detector.py` `_call_detection_api()`:

```python
# 1. 禁用 thinking
response = client.messages.create(
    ...
    thinking={"type": "disabled"},
    max_tokens=1024,  # 512 → 1024
)
```

### 连带影响

`DetectionConfig` 默认值：
```python
class DetectionConfig(BaseModel):
    max_tokens: int = 1024  # 512 → 1024
```

---

## 涉及文件

| 文件 | 改动 |
|------|------|
| `src/bible_cc_plugin/daemon/detector.py` | 新增 `_PURE_ACKNOWLEDGMENT` frozenset + `_is_pure_acknowledgment()`；`_call_detection_api` 加 `thinking={"type": "disabled"}` |
| `src/bible_cc_plugin/config.py` | `DetectionConfig.max_tokens` 512 → 1024 |
| `src/bible_cc_plugin/daemon/server.py` | `turn_user` 中调用 `_is_pure_acknowledgment` 决定是否跳过 decision detection |
| `tests/unit/test_config.py` | 验证 `max_tokens` 默认值 = 1024 |
| `tests/unit/test_detector.py` | 验证 `_is_pure_acknowledgment` 各分类的匹配；验证 thinking disabled |
| `tests/unit/test_server.py` | 验证纯确认消息 queued=False（decision detection 跳过） |

## 验收

1. `uv run pytest -x -q` 全过
2. 纯确认消息（"同意"/"我同意"/"接受建议"/"批准" 等）`/turn/user` 的 decision detection 不入队
3. 非确认消息（"用PostgreSQL"）正常入队
4. session_start detection 不受影响
5. LLM 调用不再出现 `stop_reason=max_tokens + ThinkingBlock + empty text`
6. detection latency 下降（无 thinking 开销）
