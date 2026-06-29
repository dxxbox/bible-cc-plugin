# 04 — Config

> L2 | 领域总览 | 定义了配置系统的结构、加载顺序、env var override 规则、各配置域的设计约束。详细的每项 type/default/range 见 L3 `schema.md`。

---

## 1. 定位

bible-cc-plugin 的配置系统满足三个目标：

1. **单一事实来源**：`~/.bible-cc/config.json` 是默认配置。所有组件（daemon、MCP server、hook scripts）读同一份文件。
2. **环境覆盖**：env var 优先级高于 config file。部署时可零文件修改。
3. **安全默认**：开箱即用。用户只需配 `bible.base_url` 即可跑起来。

---

## 2. 加载顺序

```
1. 内置硬编码默认值（代码级 fallback）
2. ~/.bible-cc/config.json（文件级覆盖）
3. 环境变量（最高优先级）
```

同一配置项存在多级来源时，后加载的覆盖先加载的。

---

## 3. 配置域

### 3.1 `bible` — BiBLE Atlas 连接

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `base_url` | string（URL） | `http://localhost:5555` | BiBLE Atlas 服务地址。必填。 |
| `token` | string 或 null | `null` | Bearer token。若 BiBLE Atlas 需要认证则必填。 |
| `kb_index` | string | `"bible-cc"` | Memory import 的目标知识库索引。不配则使用默认值，同一 base_url 下所有 memory 导入到同一 kb_index。 |

Env override: `BIBLE_ATLAS_BASE_URL`, `BIBLE_ATLAS_TOKEN`

约束：
- `base_url` 不得以 `/` 结尾。
- `token` 为 null 时，`client.py` 不发送 `Authorization` header（非认证模式）。
- `kb_index` 不配则默认 `"bible-cc"`，同一 `base_url` 下所有 memory 导入到同一索引。

### 3.2 `daemon` — Daemon 运行时

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `port` | int（1024-65535） | `9777` | HTTP 监听端口 |
| `port_auto_fallback` | bool | `false` | 端口被占时是否 port+1 自动重试 |
| `db_path` | string（path） | `~/.bible-cc/daemon.db` | SQLite 数据库路径 |

Env override: `BIBLE_CC_DAEMON_PORT`, `BIBLE_CC_DB_PATH`

约束：
- `port` 范围 1024-65535。非法值回退到 9777。
- `db_path` 中的 `~` 展开为用户 home 目录。
- `port_auto_fallback=true` 时最多重试 10 次（port+0 到 port+9），仍未找到则报错。

### 3.3 `injection` — Context Injection（SessionStart 本地注入）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否在 SessionStart 时注入上下文 |
| `token_budget` | int | `1200` | 注入 `<relevant-memories>` 块的 token 上限 |
| `include_turns_summary` | bool | `true` | 是否包含最近 turns 的摘要 |
| `include_moments` | bool | `true` | 是否包含当前 session 的 unflushed moments |
| `crash_recovery_moments` | bool | `true` | 是否包含 crash recovery 的 moments |
| `inject_fallback` | string | `"skip"` | 本地 buffer 为空时的行为：`"skip"` 返回空，`"empty"` 返回空 `<relevant-memories>` 块 |

约束：
- `/context/inject` 只看本地 buffer。本节不涉及 BiBLE Atlas 搜索参数。
- `token_budget` 是软上限——超过时截断，不保证精确。

### 3.4 `search` — MCP Tool 搜索默认值

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `default_top_k` | int | `8` | 各 MCP search tool 的默认返回数量 |
| `default_min_score` | float（0.0-1.0） | `0.35` | 默认最低相关性阈值 |
| `default_knowledge_tag` | string | `"design"` | `POST /api/search/knowledge-base` 的 `tag` 参数默认值 |

约束：
- MCP 工具调用时若未传 `top_k`/`min_score`，使用此默认值。调用时传了则按调用参数。
- `default_knowledge_tag` 同时用于 consult 三域并行搜索中的知识库 tag。
- SessionStart 本地注入不读这些值。

### 3.5 `capture` — 采集控制

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否启用采集（false 时 hooks 静默跳过所有 buffer 操作） |
| `mode` | string | `"key_moments"` | 采集模式：仅 `"key_moments"` |
| `commit_threshold_turns` | int | `4` | 触发 Phase 1 检测的 turn 数阈值 |
| `commit_threshold_chars` | int | `2000` | 触发 Phase 1 检测的字符数阈值 |
| `mid_session_detection` | bool | `true` | 是否启用 Phase 1 mid-session 检测 |
| `mid_session_upload` | bool | `false` | Phase 1 检测到的 moment 是否立即上传 BiBLE |
| `hint_format` | string | `"quote_with_command"` | moment hint 的展示格式：`"quote_with_command"` / `"quote_only"` / `"command_only"` / `"narrative"` |
| `stop_hint_wait_seconds` | float | `3.5` | Stop hook 中 detection 入队后的 hint poll 等待窗口（秒） |
| `tool_result_max_chars` | int | `250` | 保留给未来可配置 tool output 检测的摘要上限；默认策略不使用 |

约束：
- 阈值触发策略：以 `commit_threshold_turns` 和 `commit_threshold_chars` 中**先到达者**为准触发检测。
- 默认 detection 不读取 tool arguments/output；完整 tool output 永远存储在 turns 表中，供 review、诊断和未来配置使用。

### 3.6 `detection` — Moment Detection LLM

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `model` | string | `"deepseek-v4-flash"` | Moment detection 使用的模型 |
| `max_tokens` | int | `1024` | 每次 detection LLM 调用的 max_tokens（thinking 已禁用） |
| `temperature` | float（0.0-1.0） | `0.0` | Temperature（确定性输出） |

约束：
- `model` 必须是 Anthropic API 支持的模型 ID. 选择`ANTHROPIC_MODEL`或者`ANTHROPIC_SMALL_FAST_MODEL` (优先)中的一个。
- daemon 从环境继承 `ANTHROPIC_API_KEY`（由 Claude Code 进程提供）。

### 3.7 `bypass` — 会话绕过

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `session_patterns` | string[] | `[]` | 匹配 session_id 的正则模式列表。命中则跳过该 session 的采集。 |

约束：
- 空数组 = 不绕过任何 session。
- 匹配采用 regex fullmatch。

---

## 4. 全局约束

1. **JSON only**：不使用 YAML/TOML。Python stdlib `json` 解析，零额外依赖。
2. **Schema validation**：daemon 启动时必须用 Pydantic 验证 config。非法值 → 报错 + fallback 到默认值（不 crash）。
3. **Env override 仅覆盖叶子值**：不支持通过 env var 覆盖整个 section。每个可覆盖的键有对应的 env var。
4. **敏感信息不入 config**：`bible.token` 建议走 env var 而非 config file。
5. **Config 变更不热加载**：改 config 后需重启 daemon（或 `reload-plugin --force`）。

---

## 5. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `04-config/schema.md` | 每一项的完整 Pydantic model、type/range/default、env override key、validation 规则 | 已完成 |

---

## 6. 参考文档

- [`02-interfaces.md`](02-interfaces.md) — 配置相关的 env var 约定
- [`../bible-claude-code-plugin-feasibility-report.md`](../bible-claude-code-plugin-feasibility-report.md) — Config System 章节
- [`../../CLAUDE.md`](../../CLAUDE.md) — Config path、env var override 规则
