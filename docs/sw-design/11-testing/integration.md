# 11-testing / Integration Test Plan

> L3 | 集成测试 | 本文定义 daemon、SQLite、hook bridge、commands、MCP server 和真实 BiBLE server 之间的协议测试。完整用户旅程属于 [`e2e.md`](e2e.md)。

---

## 1. 测试边界

Integration tests 验证组件之间的真实协议，但仍保持可复现：

- 启动 bible-cc daemon 或 FastAPI app test client。
- 使用真实 SQLite 文件，但路径必须在 `tmp_path`。
- 使用真实可用的 BiBLE server 测试实例。
- 使用 deterministic detector stub，不调用真实 Anthropic API。
- 可以启动 MCP stdio server 并通过 MCP client 调用工具。
- 可以执行 hook/command Python entrypoint，但不依赖真实 Claude Code UI。

---

## 2. Harness

### 2.1 Real BiBLE Server

Integration tests require a reachable BiBLE server test instance supplied by environment:

```bash
export BIBLE_ATLAS_BASE_URL="http://<bible-server>"
export BIBLE_ATLAS_TOKEN="<optional-token>"
export BIBLE_CC_TEST_KB_INDEX="bible-cc-test-$(date +%s)"
```

The harness must never use a shared production namespace. Every test run uses:

- `kb_index`: `BIBLE_CC_TEST_KB_INDEX` or `bible-cc-test-<run-id>`
- memory tag: `memory`
- knowledge tag: `bible-cc-test-knowledge-<run-id>`
- skill tag: `skill`
- session id prefix: `it-<scenario>-<run-id>`
- imported titles containing `bible-cc-test` and the run id

Real BiBLE server用途：

- `GET /health` for `/bible-cc:check-bible` and daemon health.
- `POST /api/search/memory`, `/api/search/knowledge-base`, `/api/search/skill` for consult and MCP tools.
- `POST /api/import/memory` for flush.
- Download task/artifact routes for memory/skill get tools when the server has seeded artifacts.

Rainy path for BiBLE unreachable uses an intentionally invalid `BIBLE_ATLAS_BASE_URL`, for example `http://127.0.0.1:<unused-port>`, not by stopping the shared server.

### 2.2 Server Data Seeding

Before search/download integration scenarios, seed only the minimum records needed through the real import API:

1. Import one memory file with a unique title and `kb_index`.
2. Import one knowledge document with a unique tag.
3. Import one skill package if skill search/get is in scope for that test.
4. Poll task status until completed or fail fast with the task error.
5. Search by unique title/query to avoid dependence on global corpus state.

If the real server does not support deleting seeded test data, cleanup is logical rather than destructive: tests use unique run ids and never assert on global totals.

### 2.3 Daemon Harness

Each test starts daemon with:

- `HOME=<tmp_home>`
- `BIBLE_CC_DB_PATH=<tmp_path>/daemon.db`
- `BIBLE_CC_DAEMON_PORT=<free_port>`
- `BIBLE_ATLAS_BASE_URL=<real_bible_test_server_url>`
- `BIBLE_ATLAS_TOKEN=<optional_test_token>`
- `BIBLE_CC_TEST_KB_INDEX=<unique_test_kb_index>`
- `ANTHROPIC_API_KEY=test-key` only if code requires env presence; detector calls are stubbed.

The harness must expose:

- `daemon_url`
- `session_id_factory`
- `post_json(path, payload)`
- `sqlite_probe` for asserting persisted rows
- `detector_stub` for returning fixed Phase 1/2 outputs
- `captured_stdout` for hook hint assertions

### 2.4 Failure Injection

Use real server behavior for sunny paths. Use controlled local inputs for rainy paths:

| Failure | Injection method |
|---------|------------------|
| BiBLE unreachable | Point `BIBLE_ATLAS_BASE_URL` to unused localhost port |
| Auth failure | Use an invalid token against the test server if auth is enabled |
| Search empty | Query a unique run id that was not seeded |
| Import failure | Submit invalid multipart payload or invalid tag |
| Download missing | Request a unique nonexistent `storage_path` |
| Partial consult failure | Stub one client method at daemon boundary; real server cannot reliably fail one domain only |

---

## 3. User Scenarios

### Scenario 1: User Installs And Checks Status

User story: A new user configures BiBLE URL and wants to know whether bible-cc is ready.

Sunny path:

1. Setup writes config under temporary `~/.bible-cc/config.json`.
2. Daemon starts idempotently.
3. `GET /daemon/health` returns `status="ok"`.
4. `/bible-cc:status` shows daemon uptime, session counts, pending moments, SQLite integrity, schema version, and BiBLE connectivity.
5. `/bible-cc:check-bible` returns reachable with latency when the real BiBLE test server is up.

Rainy paths:

- BiBLE base URL points to an unused port: daemon health still returns ok, `bible_connectivity.reachable=false`, and `/bible-cc:check-bible` reports unreachable.
- Config file is absent: defaults are loaded, and setup can create config.
- Config contains invalid port: daemon falls back to `9777` or test override port and reports config warning.

Assertions:

- Status command does not fail because BiBLE is unreachable.
- Output redacts token.
- All commands use `uv run` entrypoints or documented daemon endpoints.

### Scenario 2: Fresh Session Starts With No Prior Buffer

User story: A user opens Claude Code in a project for the first time.

Sunny path:

1. SessionStart hook starts daemon.
2. Hook calls `/session/start`.
3. Hook calls `/context/inject`.
4. Response has `sources.turns=0`, `sources.moments=0`, `sources.crash_recovery=0`.
5. Context is empty or empty XML according to `inject_fallback`.

Rainy paths:

- Daemon already running: `/daemon/start` returns current process and SessionStart continues.
- BiBLE base URL points to an unused port: SessionStart still succeeds because `/context/inject` is local only.
- Config disables injection: context is empty and session row is still created.

Assertions:

- No HTTP request is made to BiBLE during `/context/inject`.
- SessionStart timeout budget is only consumed by daemon startup and local DB work.

### Scenario 3: `/clear` Or Compaction Restores Same-Session Context

User story: A user clears Claude Code context during a long task and expects local context to return.

Sunny path:

1. Create session.
2. Post user turns and tool turns.
3. Insert one unflushed decision moment.
4. Call `/session/start` again with same session id.
5. Call `/context/inject`.
6. Context includes turn summary and unflushed moment.

Rainy paths:

- `include_turns_summary=false`: context includes moments only.
- `include_moments=false`: context includes turn summary only.
- Buffer is empty: response follows `inject_fallback`.
- Stored tool output is very large: context respects token budget and does not inject full output.

Assertions:

- Same-session `/session/start` is idempotent.
- Re-injection never searches BiBLE.

### Scenario 4: Capturing Turns And Detecting Key Moments

User story: A user makes a decision during a session and wants it captured as a pending memory.

Sunny path:

1. Start session.
2. Post turns until `commit_threshold_turns` or `commit_threshold_chars`.
3. Detector stub returns one `decision`.
4. Daemon inserts moment with `flushed=0`.
5. Hook-visible hint is rendered using configured `hint_format`.
6. `/daemon/moments?session_id=X` lists the pending moment.

Rainy paths:

- Detector stub returns `none`: no moment inserted and no hint emitted.
- Detector throws: turn remains buffered; response still returns `{queued: true}` or safe equivalent.
- Detector returns invalid type: invalid moment discarded.
- Overlapping windows return duplicate content: content-hash prevents duplicate row.
- `mid_session_upload=true`: moment is flushed immediately and marked `flushed=1` after successful import.

Assertions:

- `/turn/user` and `/turn/tool` return before detector work completes.
- Full tool output is persisted before detector summary.
- Hook timeout remains compatible with non-blocking behavior.

### Scenario 5: Review, Edit, Discard, And Push Pending Moments

User story: A user wants control over what becomes long-term memory.

Sunny path:

1. Seed two pending moments.
2. `GET /daemon/moments` lists both with type, title, narrative, turn range, timestamp.
3. `PUT /daemon/moments/{id}` updates title or narrative.
4. `DELETE /daemon/moments/{id}` removes a pending moment.
5. `/bible-cc:push` flushes remaining unflushed moments to the real BiBLE memory import API.

Rainy paths:

- Edit unknown moment returns `MOMENT_NOT_FOUND`.
- Delete unknown moment returns `MOMENT_NOT_FOUND`.
- Edit flushed moment returns `MOMENT_ALREADY_FLUSHED` with HTTP 409.
- BiBLE import fails: moment stays `flushed=0`, `retry_count` increments if implemented, and status reports pending flush.

Assertions:

- Review endpoints only affect pending local moments.
- User edits recompute `content_hash` if title/narrative changes.
- Push does not duplicate already flushed moments.

### Scenario 6: Session End Retrospective And Flush

User story: A normal session ends and bible-cc imports important moments into BiBLE.

Sunny path:

1. Session has buffered turns and one Phase 1 pending moment.
2. Stop hook calls `/session/end`.
3. Phase 2 detector receives all turns and already-detected moments.
4. Stub returns one additional accomplishment.
5. Daemon dedups, serializes all unflushed moments, calls `/api/import/memory`.
6. Session status becomes `completed`.

Rainy paths:

- Phase 2 detector fails: daemon flushes existing Phase 1 moments and completes session.
- BiBLE import times out: moments stay `flushed=0`; session close does not crash.
- Import returns `202 queued`: daemon records `import_task_id` and does not wait for task completion.
- Stop hook cannot reach daemon: hook exits success; data remains in SQLite for next SessionStart recovery.

Assertions:

- Phase 2 prompt includes already-detected moments.
- Flush uses memory import route with `tag="memory"` and configured `kb_index`.
- Stop hook respects 30s timeout budget.

### Scenario 7: Consult Cross-Domain Search

User story: The model did not automatically find context, so the user runs `/bible-cc:consult`.

Sunny path:

1. With explicit query, `/daemon/consult` calls memory, knowledge-base, and skill search in parallel.
2. Results are normalized to `{domain, id, title, snippet, score}`.
3. Results are merged by descending score.
4. Response includes `context`, `query_used`, and `hits`.

Rainy paths:

- Query is empty: LLM query summarizer stub produces query, then search runs.
- Query summarizer fails: endpoint returns structured error and does not call BiBLE.
- One domain returns error: response includes successful domains and diagnostic for failed domain if policy allows partial results.
- All domains unreachable: returns `BIBLE_UNREACHABLE`.
- All domains return empty: context is valid empty recall block, not an exception.

Assertions:

- Consult is the only command that calls BiBLE search through daemon.
- MCP search defaults do not affect SessionStart local injection.

### Scenario 8: MCP Tools Query BiBLE Without Daemon

User story: During conversation, the model invokes bible_* tools to pull relevant memory/knowledge/skill.

Sunny path:

1. Start MCP server with `BIBLE_ATLAS_BASE_URL` pointing to the real BiBLE test server.
2. Call `bible_memory_search`, `bible_knowledge_search`, and `bible_skill_search`.
3. Call `bible_memory_save` and receive import task response.
4. Call memory/skill get and complete download task/artifact flow.

Rainy paths:

- Daemon is not running: MCP tools still work because they do not depend on daemon.
- BiBLE is unreachable: tool returns model-visible structured error and server remains alive.
- Postponed tool is called: tool returns "not yet available" with reason.
- Download task fails or artifact missing: tool returns structured failure.

Assertions:

- MCP server never opens daemon SQLite.
- MCP server never calls daemon HTTP endpoints.
- MCP stdio process survives individual tool errors.

### Scenario 9: Port Conflict And Daemon Startup Failure

User story: Port `9777` is already occupied when Claude Code starts.

Sunny path:

1. With `daemon.port_auto_fallback=true`, occupied configured port causes retry to next free port.
2. Health reports actual port.
3. SessionStart continues against actual port.

Rainy paths:

- With fallback disabled, startup returns error.
- SessionStart hook emits transcript-visible error hint and injectable context.
- UserPromptSubmit/PostToolUse later silently skip because daemon is unavailable.

Assertions:

- Error hint includes port and actionable status command.
- Failure is not silent at SessionStart.
- Later turn hooks are silent by design.

### Scenario 10: Concurrent Sessions

User story: A user runs two Claude Code sessions against the same plugin daemon.

Sunny path:

1. Start session A and session B.
2. Concurrently post user/tool turns from both sessions.
3. Each session gets independent per-session `seq`.
4. WAL mode prevents `SQLITE_BUSY` failures under normal contention.
5. Moments are deduped per session.

Rainy paths:

- Long write transaction holds lock under `busy_timeout`: second writer waits and succeeds if released within 5s.
- Lock exceeds busy timeout: daemon returns structured SQLite error and does not crash.
- Same title/narrative in different sessions: content-hash includes session id, so both can exist.

Assertions:

- No lost turns.
- No cross-session context injection.
- Health endpoint reflects active session count accurately.

### Scenario 11: Capture Pause, Resume, And Bypass

User story: A user wants private or scratch sessions excluded from memory capture.

Sunny path:

1. `capture.enabled=false` or pause command causes hook writes to skip buffering.
2. Resume re-enables buffering for later turns.
3. `bypass.session_patterns` fullmatch skips matching session ids.

Rainy paths:

- Invalid regex in config returns config validation error and safe default.
- Resume is idempotent when capture already enabled.
- Pause does not delete previously buffered turns unless a separate explicit discard path exists.

Assertions:

- Skipped turns do not create rows or moments.
- Hooks still exit success.

### Scenario 12: Operation Lifecycle

User story: A user installs, restarts, reloads, upgrades, and uninstalls bible-cc-plugin without losing data unexpectedly or leaving orphaned state.

Sunny path:

1. Setup writes config in isolated `HOME` and does not overwrite existing config on repeated setup.
2. SessionStart auto-starts daemon when no daemon is running.
3. `/daemon/stop` stops daemon gracefully and removes or invalidates runtime PID state.
4. A later SessionStart restarts daemon and reuses the same SQLite database.
5. Schema migration runs idempotently after restart or simulated upgrade.
6. Existing sessions, turns, and pending moments remain readable after restart/reload.
7. A stale `daemon.pid` pointing to a nonexistent process is ignored or replaced during SessionStart bootstrap.
8. `/bible-cc:uninstall` stops daemon and removes only scoped local state under the test `~/.bible-cc`.
9. Uninstall output tells the user how to remove plugin registry/cache state managed by Claude Code, without deleting it implicitly outside the test sandbox.

Rainy paths:

- Setup runs when config already exists: command is idempotent and preserves user-provided `base_url`, token, port, and db path.
- Daemon stop is called when daemon is not running: endpoint or command returns success-like idempotent result.
- Daemon crashes before stop: next SessionStart starts a new daemon and triggers crash recovery.
- `daemon.pid` exists but process is gone: SessionStart treats it as stale and starts a new daemon.
- `daemon.pid` exists and points to a different live process: startup refuses to kill it and reports actionable diagnostic.
- Upgrade adds a new optional SQLite column: migration adds it without deleting existing rows.
- Upgrade sees an invalid config value: validation falls back safely and reports diagnostic.
- Uninstall cannot reach daemon: cleanup still removes local config/database only after reporting daemon stop failure, or exits with actionable error according to implementation policy.
- Uninstall is run twice: second run reports already removed/no-op without touching unrelated files.

Assertions:

- Operation commands never delete data outside the configured temp `HOME` / `BIBLE_CC_DB_PATH`.
- Restart/reload preserves pending moments and unclosed session recovery data.
- Uninstall removes local SQLite/config but does not call BiBLE delete APIs.
- Uninstall test verifies Claude Code plugin registry/cache cleanup is explicit user guidance or sandbox-scoped, never an uncontrolled filesystem delete.
- No operation command uses `source .venv/bin/activate`, `pip`, `bun`, or `npm`.
- Operation diagnostics are visible through command output or status, not only daemon logs.

---

## 4. Integration Commands

Run integration tests:

```bash
uv run pytest tests/integration
```

Run with a real BiBLE test server:

```bash
BIBLE_ATLAS_BASE_URL=http://<bible-server> uv run pytest tests/integration
```

Run one scenario:

```bash
uv run pytest tests/integration/test_capture_flush.py
```

---

## 5. Pass Criteria

Integration suite passes when:

1. Daemon HTTP API matches [`../02-interfaces.md`](../02-interfaces.md).
2. Hook bridge behavior matches timeout and failure rules.
3. Commands remain thin wrappers over documented endpoints.
4. MCP tools remain daemon-independent BiBLE wrappers.
5. Real BiBLE server scenarios cover search, import, download/task where supported, and controlled failure responses.
6. Every scenario in §3 has at least one sunny test and one rainy test.
7. No test uses shared production data, production user config, real Anthropic API, or real Claude Code UI.
