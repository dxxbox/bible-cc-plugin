# 04-config/schema.md — Config Schema（L3）

> 每一项的完整 Pydantic model：type、default、range、env override key、validation 规则。实现时直接照此定义 Pydantic 类。

---

## 1. 顶层结构

```python
from pydantic import BaseModel, Field, field_validator
from pathlib import Path

class BibleConfig(BaseModel):
    base_url: str = "http://localhost:5555"
    token: str | None = None
    kb_index: str = "bible-cc"

class DaemonConfig(BaseModel):
    port: int = 9777
    port_auto_fallback: bool = False
    db_path: str = "~/.bible-cc/daemon.db"

class InjectionConfig(BaseModel):
    enabled: bool = True
    token_budget: int = 1200
    include_turns_summary: bool = True
    include_moments: bool = True
    crash_recovery_moments: bool = True
    inject_fallback: str = "skip"

class SearchConfig(BaseModel):
    default_top_k: int = 8
    default_min_score: float = 0.35
    default_knowledge_tag: str = "design"

class CaptureConfig(BaseModel):
    enabled: bool = True
    mode: str = "key_moments"
    commit_threshold_turns: int = 4
    commit_threshold_chars: int = 2000
    mid_session_detection: bool = True
    mid_session_upload: bool = False
    hint_format: str = "quote_with_command"
    stop_hint_wait_seconds: float = 3.5
    tool_result_max_chars: int = 250

class DetectionConfig(BaseModel):
    model: str = "deepseek-v4-flash"
    max_tokens: int = 1024
    temperature: float = 0.0

class BypassConfig(BaseModel):
    session_patterns: list[str] = []

class AppConfig(BaseModel):
    bible: BibleConfig = BibleConfig()
    daemon: DaemonConfig = DaemonConfig()
    injection: InjectionConfig = InjectionConfig()
    search: SearchConfig = SearchConfig()
    capture: CaptureConfig = CaptureConfig()
    detection: DetectionConfig = DetectionConfig()
    bypass: BypassConfig = BypassConfig()
```

---

## 2. 逐项 Spec

### 2.1 `bible`

| 键 | Type | Default | Range | Env Override | Validation |
|----|------|---------|-------|-------------|------------|
| `base_url` | `str` | `"http://localhost:5555"` | 有效 HTTP(S) URL | `BIBLE_ATLAS_BASE_URL` | 必须是 `http://` 或 `https://` 开头；不得以 `/` 结尾。非法时回退到 default。 |
| `token` | `str \| None` | `None` | — | `BIBLE_ATLAS_TOKEN` | 空字符串视为 `None`。 |
| `kb_index` | `str` | `"bible-cc"` | — | — | import/memory 时使用的知识库索引。不配则默认 `"bible-cc"`，同 base_url 共享索引。 |

Pydantic:

```python
@field_validator("base_url")
@classmethod
def validate_url(cls, v: str) -> str:
    if not v.startswith(("http://", "https://")) or v.endswith("/"):
        return "http://localhost:5555"  # silent fallback to default
    return v
```

### 2.2 `daemon`

| 键 | Type | Default | Range | Env Override | Validation |
|----|------|---------|-------|-------------|------------|
| `port` | `int` | `9777` | 1024–65535 | `BIBLE_CC_DAEMON_PORT` | 非法值回退到 9777。 |
| `port_auto_fallback` | `bool` | `False` | — | — | 无。 |
| `db_path` | `str` | `"~/.bible-cc/daemon.db"` | — | `BIBLE_CC_DB_PATH` | `~` 展开为 `Path.home()`。父目录不存在时 daemon 启动时自动创建。 |

```python
@field_validator("port")
@classmethod
def validate_port(cls, v: int) -> int:
    if not (1024 <= v <= 65535):
        return 9777  # silent fallback
    return v
```

### 2.3 `injection`

| 键 | Type | Default | Range | Env Override | Validation |
|----|------|---------|-------|-------------|------------|
| `enabled` | `bool` | `True` | — | — | 无。 |
| `token_budget` | `int` | `1200` | ≥ 0 | — | 0 表示不注入任何内容。 |
| `include_turns_summary` | `bool` | `True` | — | — | 无。 |
| `include_moments` | `bool` | `True` | — | — | 无。 |
| `crash_recovery_moments` | `bool` | `True` | — | — | 无。 |
| `inject_fallback` | `str` | `"skip"` | `"skip"` \| `"empty"` | — | 非法值回退到 `"skip"`。 |

```python
@field_validator("inject_fallback")
@classmethod
def validate_inject_fallback(cls, v: str) -> str:
    if v not in ("skip", "empty"):
        return "skip"  # silent fallback
    return v
```

`token_budget` 是软上限——超过时由注入逻辑截断内容。

### 2.4 `search`

| 键 | Type | Default | Range | Env Override | Validation |
|----|------|---------|-------|-------------|------------|
| `default_top_k` | `int` | `8` | 1–100 | — | 非法值回退到 8。 |
| `default_min_score` | `float` | `0.35` | 0.0–1.0 | — | 非法值回退到 0.35。 |
| `default_knowledge_tag` | `str` | `"design"` | — | — | `POST /api/search/knowledge-base` 的默认 tag。 |

### 2.5 `capture`

| 键 | Type | Default | Range | Env Override | Validation |
|----|------|---------|-------|-------------|------------|
| `enabled` | `bool` | `True` | — | — | `False` 时 daemon 仍接受 turn 写入，但不触发 Phase 1/2 检测。 |
| `mode` | `str` | `"key_moments"` | `"key_moments"` | — | 目前仅支持此模式。 |
| `commit_threshold_turns` | `int` | `4` | ≥ 1 | — | 与 `commit_threshold_chars` 先到达者触发。 |
| `commit_threshold_chars` | `int` | `2000` | ≥ 1 | — | 同上。 |
| `mid_session_detection` | `bool` | `True` | — | — | `False` 时只在 session end 做 Phase 2。 |
| `mid_session_upload` | `bool` | `False` | — | — | `True` 时 Phase 1 moment 立即 flush。 |
| `hint_format` | `str` | `"quote_with_command"` | `"quote_with_command"` \| `"quote_only"` \| `"command_only"` \| `"narrative"` | — | 非法值回退到 `"command_only"`。 |
| `stop_hint_wait_seconds` | `float` | `3.5` | 0.0–30.0 | — | Stop hook 中 detection 入队后的 hint poll 等待窗口（秒）。超时后写入 hint_watch 兜底。 |
| `tool_result_max_chars` | `int` | `250` | 0–4000 | — | 保留给未来可配置 tool output 检测；默认策略不使用。 |

```python
HINT_FORMATS = {"quote_with_command", "quote_only", "command_only", "narrative"}

@field_validator("hint_format")
@classmethod
def validate_hint_format(cls, v: str) -> str:
    if v not in HINT_FORMATS:
        return "command_only"  # silent fallback
    return v
```

### 2.6 `detection`

| 键 | Type | Default | Range | Env Override | Validation |
|----|------|---------|-------|-------------|------------|
| `model` | `str` | `"deepseek-v4-flash"` | Anthropic model ID | — | 实现时优先取 `ANTHROPIC_SMALL_FAST_MODEL` → `ANTHROPIC_MODEL` → `BIBLE_CC_DETECTION_MODEL` → config default。 |
| `max_tokens` | `int` | `1024` | 1–4096 | — | detection 输出 budget（thinking 已禁用，纯 JSON 输出）。非法值回退到 1024。 |
| `temperature` | `float` | `0.0` | 0.0–1.0 | — | 建议保持 0.0。 |

模型选择逻辑（实现参考）：

```python
def resolve_detection_model(config_model: str) -> str:
    """Resolution order: ANTHROPIC_SMALL_FAST_MODEL > ANTHROPIC_MODEL > BIBLE_CC_DETECTION_MODEL > config_model > default.
    
    Note: BIBLE_CC_DETECTION_MODEL is applied first in load_config(),
    then ANTHROPIC_SMALL_FAST_MODEL/ANTHROPIC_MODEL override it.
    """
    import os
    return (
        os.getenv("ANTHROPIC_SMALL_FAST_MODEL") or
        os.getenv("ANTHROPIC_MODEL") or
        os.getenv("BIBLE_CC_DETECTION_MODEL") or
        config_model or
        "deepseek-v4-flash"
    )
```

### 2.7 `bypass`

| 键 | Type | Default | Range | Env Override | Validation |
|----|------|---------|-------|-------------|------------|
| `session_patterns` | `list[str]` | `[]` | — | — | 每个元素必须是合法 regex。 |

```python
import re

def is_bypassed(session_id: str, patterns: list[str]) -> bool:
    for p in patterns:
        if re.fullmatch(p, session_id):
            return True
    return False
```

---

## 3. 完整示例

```json
{
  "bible": {"base_url": "http://bible-atlas.internal:5555", "token": null, "kb_index": "bible-cc"},
  "daemon": {"port": 9777, "port_auto_fallback": false, "db_path": "~/.bible-cc/daemon.db"},
  "injection": {"enabled": true, "token_budget": 1200, "include_turns_summary": true, "include_moments": true, "crash_recovery_moments": true, "inject_fallback": "skip"},
  "search": {"default_top_k": 8, "default_min_score": 0.35},
  "capture": {"enabled": true, "mode": "key_moments", "commit_threshold_turns": 4, "commit_threshold_chars": 2000, "mid_session_detection": true, "mid_session_upload": false, "hint_format": "quote_with_command", "stop_hint_wait_seconds": 3.5, "tool_result_max_chars": 250},
  "detection": {"model": "deepseek-v4-flash", "max_tokens": 1024, "temperature": 0.0},
  "bypass": {"session_patterns": []}
}
```

---

## 4. 加载器伪代码

```python
import json, os
from pathlib import Path

def load_config() -> AppConfig:
    # Step 1: built-in defaults
    config = AppConfig()

    # Step 2: file overlay
    # Pydantic 自动填充缺失字段的默认值——JSON 只需包含用户想覆盖的项
    config_path = Path.home() / ".bible-cc" / "config.json"
    if config_path.exists():
        file_data = json.loads(config_path.read_text())
        config = AppConfig(**file_data)

    # Step 3: env var overlay (highest priority)
    if v := os.getenv("BIBLE_ATLAS_BASE_URL"):
        config.bible.base_url = v
    if v := os.getenv("BIBLE_ATLAS_TOKEN"):
        config.bible.token = v
    if v := os.getenv("BIBLE_CC_DAEMON_PORT"):
        config.daemon.port = int(v)
    if v := os.getenv("BIBLE_CC_DB_PATH"):
        config.daemon.db_path = v

    return config
```

---

## 5. 参考文档

- [`../04-config.md`](../04-config.md) — L2 配置总览、全局约束
- [`../../02-interfaces.md`](../02-interfaces.md) — env var 约定
