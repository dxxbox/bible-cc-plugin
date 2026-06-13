# 11-testing / Unit Test Plan

> L3 | 单元测试 | 本文定义 bible-cc-plugin 的确定性和半确定性逻辑测试。跨进程、HTTP、真实 BiBLE server、真实 hook 执行属于 [`integration.md`](integration.md)。

---

## 1. 测试边界

Unit tests 只验证进程内逻辑：

- 不启动 uvicorn daemon。
- 不连接真实 BiBLE server。
- 不调用真实 Anthropic API。
- 不读写真实 `~/.bible-cc/config.json` 或 `~/.bible-cc/daemon.db`。
- 不执行 Claude Code 本体，只测试 hook/command helper 生成的请求、stdout payload 和错误处理。

允许依赖：

- `tmp_path` SQLite database。
- `httpx.MockTransport` 或等价 fake client。
- deterministic detector stub。
- fixed conversation fixtures。

---

## 2. Test Harness

推荐测试夹具：

| Fixture | 作用 |
|---------|------|
| `tmp_home` | 临时 HOME，验证 config path、db path、setup 写入行为 |
| `tmp_db_path` | 单测独立 SQLite 文件 |
| `config_factory` | 生成默认 config，并支持字段级 override |
| `conversation_factory` | 生成 user/tool/assistant turn 序列 |
| `moment_factory` | 生成 session_start/decision/accomplishment moments |
| `fake_detector` | 返回固定 `none`、单 moment、多 moment、invalid JSON、exception |
| `mock_bible_transport` | 捕获 BiBLE client 请求并返回固定响应 |
| `freezer_clock` | 固定 timestamp，避免 snapshot 不稳定 |

---

## 3. Module Matrix

### 3.1 Config

Files: `src/bible_cc_plugin/config.py`, `src/bible_cc_plugin/types.py`

Sunny tests:

- Loads built-in defaults when config file is absent.
- Reads `~/.bible-cc/config.json` and expands `~` in `daemon.db_path`.
- Env vars override file values: `BIBLE_ATLAS_BASE_URL`, `BIBLE_ATLAS_TOKEN`, `BIBLE_CC_DAEMON_PORT`, `BIBLE_CC_DB_PATH`.
- `bible.base_url` normalizes by removing trailing slash.
- `bible.token=null` causes client config to omit `Authorization`.

Rainy tests:

- Invalid JSON returns structured config error and safe defaults where allowed.
- Invalid port below 1024 or above 65535 falls back to `9777`.
- Invalid `capture.mode` rejects anything except `key_moments`.
- Invalid `injection.inject_fallback` rejects anything except `skip` or `empty`.
- Sensitive token is never included in `repr`, status output, or error string.

### 3.2 SQLite Buffer

Files: `src/bible_cc_plugin/daemon/buffer.py`

Sunny tests:

- Startup executes `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` before first write.
- Migration creates `sessions`, `turns`, `moments`, and monitoring tables idempotently.
- `start_session(session_id)` creates a new active row and is idempotent for existing session.
- User/tool turns get per-session increasing `seq`.
- Full tool output is stored exactly; summary length is not applied at buffer write time.
- Moment insert computes `SHA-256(session_id + title + narrative)` and stores `content_hash`.
- Duplicate `content_hash` uses `INSERT OR IGNORE` and does not create a second moment.

Rainy tests:

- Inserting a turn for an unknown session returns `SESSION_NOT_FOUND` rather than creating an implicit session.
- Editing a flushed moment returns `MOMENT_ALREADY_FLUSHED`.
- Deleting an unknown moment returns `MOMENT_NOT_FOUND`.
- SQLite integrity check failure is surfaced in health state without crashing the process.
- Crash recovery scan finds sessions with `status='active'` and leaves completed sessions untouched.

### 3.3 Moment Detector

Files: `src/bible_cc_plugin/daemon/detector.py`

Sunny tests:

- Phase 1 prompt contains only the configured recent window plus session context.
- Phase 1 recognizes allowed moment types: `session_start`, `decision`, `accomplishment`.
- Phase 2 prompt contains all turns and the already-detected moments list.
- Phase 2 parser accepts an overall assessment plus new moments.
- Tool output summary is produced by the detector path and respects `tool_result_max_chars`.
- `hint_format` renders all supported formats: `quote_with_command`, `quote_only`, `command_only`, `narrative`.

Rainy tests:

- Invalid model JSON output returns no moments and records a detector error.
- Unsupported moment type is discarded.
- LLM exception in Phase 1 does not drop buffered turns.
- LLM exception in Phase 2 returns existing Phase 1 moments for flush.
- Phase 2 does not re-report moments listed in the already-detected section when the stub returns duplicates.

### 3.4 Context Injector

Files: `src/bible_cc_plugin/daemon/injector.py`

Sunny tests:

- Empty local buffer with `inject_fallback="skip"` returns empty context.
- Empty local buffer with `inject_fallback="empty"` returns an empty `<relevant-memories>` block.
- Same-session recovery includes turns summary and unflushed moments.
- Crash recovery includes prior unclosed session moments when enabled.
- Token budget truncates long context while preserving valid XML wrapper.

Rainy tests:

- `/context/inject` path has no dependency on BiBLE client.
- Disabled injection returns empty context even when buffer has data.
- `include_turns_summary=false` omits turn summary but keeps moments.
- `include_moments=false` omits moments but keeps turn summary.
- Malformed stored turn JSON is skipped with a diagnostic note, not a thrown exception.

### 3.5 BiBLE HTTP Client

Files: `src/bible_cc_plugin/daemon/client.py`

Sunny tests:

- Memory, knowledge-base, and skill search call the correct V4 routes.
- Search requests include `query`, `tag`, `top_k`, `search_type`, and optional vector fields.
- Cross-domain consult helper calls all three search routes and merges by descending score.
- Memory import serializes moments as multipart `files[]`, `kb_index`, and `tag="memory"`.
- Download helpers use async task flow for memory and skill get operations.
- Bearer token is sent only when configured.

Rainy tests:

- Connection timeout maps to `BibleUnreachableError`.
- Non-2xx response maps to a structured client error that preserves status code and safe message.
- Missing domain result key, for example `domain="MEMORY"` but no `results.memory`, returns empty results with warning metadata.
- Import failure leaves caller enough information to keep moments `flushed=0`.
- Client never logs token or uploaded file body.

### 3.6 MCP Server

Files: `src/bible_cc_plugin/mcp/server.py`

Sunny tests:

- Registers six active tools: memory search/save/get, knowledge search, skill search/get.
- Registers postponed tools as explicit "not yet available" responses if placeholders are implemented.
- Each tool validates required parameters before calling the client.
- MCP tool errors are returned to the model as structured content, not raised to terminate the server.

Rainy tests:

- Missing `BIBLE_ATLAS_BASE_URL` returns a clear configuration error.
- BiBLE unreachable returns model-visible error and keeps stdio server alive.
- `.mcp.json` env literals are treated as raw strings in config parsing tests.

### 3.7 Hook and Command Helpers

Files: `src/bible_cc_plugin/scripts/hook.py`, `src/bible_cc_plugin/scripts/daemon.py`, command markdown wrappers

Sunny tests:

- SessionStart command sequence is `daemon/start → session/start → context/inject`.
- UserPromptSubmit posts `USER_PROMPT` to `/turn/user`.
- PostToolUse posts `TOOL_NAME`, `TOOL_OUTPUT`, and arguments to `/turn/tool`.
- Stop posts `/session/end`.
- Commands target documented daemon endpoints.

Rainy tests:

- UserPromptSubmit/PostToolUse daemon connection failure: first failure per session outputs hint via cooldown marker file; subsequent failures suppress output. Always exits success.
- SessionStart daemon startup failure emits error hint through stdout and includes injectable context.
- Stop daemon connection failure exits success so Claude Code shutdown is not blocked.
- PostToolUse tests fail if implementation reads `$TOOL_RESULT` instead of `$TOOL_OUTPUT`.

### 3.8 Monitoring Helpers

Files: `src/bible_cc_plugin/daemon/metrics.py` if split out, otherwise daemon module owning metrics

Sunny tests:

- API latency metrics record route, status, and duration bucket.
- Token usage records session totals and injection cost when provided by hook payloads.
- Flush payload includes monitoring section when metrics exist.

Rainy tests:

- Missing token usage fields do not block capture.
- Metrics retention filter keeps 30 days and marks older rows for `/bible-cc:gc`.
- Metrics serialization excludes raw prompt text and token values that look like secrets.

---

## 4. Regression Targets From Design Review

| Finding | Unit regression |
|---------|-----------------|
| Python + `uv` final architecture | command/hook strings never use `bun`, `npm`, or venv activation |
| SessionStart self-contained startup | SessionStart helper always starts daemon before session/register/inject |
| Phase 1/2 dedup | content-hash and Phase 2 known-moments prompt both tested |
| SQLite write conflict | WAL PRAGMA unit test plus integration concurrency test |
| Local-only injection | injector tests fail if BiBLE client is invoked |
| `.mcp.json` mismatch | MCP command is `uv run python -m bible_cc_plugin.mcp.server` |
| Hook timeout semantics | hook config tests assert 3s turn hooks and 60s SessionStart |
| Full tool output | buffer stores full output; detector summarizes later |
| Review endpoints | moment list/edit/delete rules tested |
| Port conflict | startup error maps to hint payload |

---

## 5. Commands

Run unit tests:

```bash
uv run pytest tests/unit
```

Run a focused module:

```bash
uv run pytest tests/unit/test_buffer.py
```

Run lint and format checks:

```bash
uv run ruff check
uv run ruff format --check
```

---

## 6. Pass Criteria

Unit test suite passes when:

1. Deterministic logic has direct assertions.
2. LLM-dependent logic uses deterministic stubs and structure assertions.
3. No unit test opens a real network socket.
4. No unit test reads or writes real user config or database paths.
5. Design review regressions in §4 are covered by explicit tests.
