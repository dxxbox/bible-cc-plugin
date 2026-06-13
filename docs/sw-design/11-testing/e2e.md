# 11-testing / E2E Test Plan

> L3 | 端到端测试 | 本文定义 bible-cc-plugin 的用户旅程级测试。E2E 以用户可观察行为为准，覆盖 hook、daemon、SQLite、commands、MCP、真实 BiBLE server 的完整链路。

---

## 1. 测试边界

E2E tests 只覆盖关键旅程，数量保持少而稳定。

自动化 E2E 默认不驱动真实 Claude Code UI。它通过 plugin packaging、hook scripts、command markdown、daemon process、MCP stdio server、真实 BiBLE server 测试实例模拟 Claude Code 生命周期事件。

允许两类 E2E：

| 类型 | 用途 | 是否进 CI |
|------|------|----------|
| Automated E2E | 稳定验证核心用户旅程，使用真实 BiBLE server 测试实例 + stub LLM | 是 |
| Manual Smoke | 真实 Claude Code plugin 安装、真实 transcript hint、真实 command 触发 | 否，release 前运行 |

自动化 E2E 禁止：

- 调用真实 Anthropic API。
- 连接共享生产数据空间。所有 BiBLE 写入必须使用测试 `kb_index`、tag、title/run id。
- 写真实 `~/.bible-cc`。
- 依赖真实用户 Claude Code settings。

---

## 2. E2E Harness

自动化 harness 需要提供：

1. Build/install step: `uv sync` in repo.
2. Temp plugin runtime: isolated `HOME`, isolated `.claude` test directory if command discovery is tested.
   > ⚠️ Plugin discovery 的模拟方案待实现时细化。当前 E2E 不驱动真实 Claude Code UI，通过直接执行 hook/command entrypoint 绕过 discovery 步骤。
3. BiBLE server: configured test instance URL + unique test namespace.
4. Daemon process: dynamic port + temp DB.
5. Hook runner: executes `uv run python -m bible_cc_plugin.scripts.hook <event>` with synthetic Claude Code env vars.
6. Command runner: executes the command body or backing Python module with test session id.
7. MCP runner: starts `uv run python -m bible_cc_plugin.mcp.server` and calls tools through a test MCP client.
8. Observability: captures stdout, stderr, daemon health, SQLite rows, BiBLE client calls, and seeded test run ids.
9. Async polling helper: polls real BiBLE import/download tasks with bounded attempts and prints task id, final status, and last response on failure.

Test session naming:

```
e2e-<scenario>-<short-random>
```

This makes bypass tests and SQLite inspection deterministic without reusing real sessions.

---

## 3. User Journeys

### Journey 1: First-Time User Checks Plugin Health

User scenario: "I installed bible-cc-plugin and want to know whether capture and BiBLE connectivity work."

Sunny path:

1. Run setup in isolated HOME with `BIBLE_ATLAS_BASE_URL` pointing to the real BiBLE test server.
2. SessionStart hook starts daemon.
3. Run `/bible-cc:status`.
4. Run `/bible-cc:check-bible`.
5. Run `/bible-cc:help` and `/bible-cc:version`.

Expected user-visible result:

- Status reports daemon ok, SQLite ok, schema version, active session count, pending moments count, and BiBLE reachable.
- Check-bible reports the real BiBLE test server reachable.
- Help lists MVP commands from command priority table.
- Version returns plugin version without requiring daemon business state.

Rainy paths:

- `BIBLE_ATLAS_BASE_URL` points to an unused port before status: status still succeeds and reports BiBLE unreachable.
- Config is missing: setup creates it or status reports a config diagnostic.
- Token is configured: status output redacts it.

Pass criteria:

- No command crashes Claude Code equivalent runner.
- No real user config path is touched.
- Diagnostics are visible without reading daemon logs.

### Journey 2: Fresh Session Starts Without Speculative BiBLE Search

User scenario: "I open Claude Code in a new project and have not said anything yet."

Sunny path:

1. Run SessionStart hook with a new session id.
2. Inspect hook stdout and daemon request log.
3. Inspect BiBLE client call capture or use an unreachable BiBLE URL for this journey.

Expected user-visible result:

- Hook returns success.
- Injected context is empty or empty `<relevant-memories>` depending on `inject_fallback`.
- No BiBLE search request occurred.

Rainy paths:

- Daemon is already running: hook remains idempotent.
- BiBLE is unreachable: hook still succeeds.
- `injection.enabled=false`: hook creates session but injects nothing.

Pass criteria:

- `/context/inject` is proven local-only because it succeeds even when BiBLE URL is unreachable, or because the BiBLE client spy records no calls.
- Session row exists after hook completes.

### Journey 3: Long Session Recovers After `/clear` Or Compaction

User scenario: "I have been working for a while, clear context, and expect bible-cc to restore local task context."

Sunny path:

1. SessionStart starts session.
2. UserPromptSubmit hook records the task request.
3. PostToolUse hook records at least one tool result.
4. Detector stub inserts a pending decision moment.
5. Run SessionStart again with same session id to simulate `/clear` or compaction.

Expected user-visible result:

- The second SessionStart injects a context block containing recent turns summary and the pending decision.
- The injected context is bounded by `injection.token_budget`.
- No BiBLE request is made during injection.

Rainy paths:

- Tool output is larger than budget: injected context summarizes/truncates safely.
- Pending moment list is empty: injection still includes turn summary.
- Buffer has malformed optional metadata: injection skips bad metadata and still returns usable context.

Pass criteria:

- User can continue the session without a remote search.
- Stored full tool output remains in SQLite even if injected context is summarized.

### Journey 4: Decision Capture, Review, Edit, And Flush

User scenario: "I made an important decision and want to review it before it is saved to BiBLE."

Sunny path:

1. Start session.
2. Submit turns representing a user-confirmed decision.
3. Detector stub returns one decision moment.
4. Next hook-visible output contains a captured-moment hint.
5. Run `/bible-cc:review`.
6. Edit the moment title or narrative.
7. Run `/bible-cc:push`.
8. Verify the real BiBLE memory import API accepted the moment payload in the test namespace.

Expected user-visible result:

- Hint includes enough context to know what was captured.
- Review lists the pending moment.
- Edit output shows updated moment.
- Push reports flushed count and leaves no pending moments.

Rainy paths:

- Detector returns duplicate decision from overlapping windows: review shows one moment.
- User discards the moment: push imports zero moments.
- BiBLE import fails: push reports failure, moment remains pending.
- User tries to edit a flushed moment: command reports conflict instead of silently changing history.

Pass criteria:

- User data ownership is preserved before flush.
- `content_hash` dedup prevents duplicate memories.
- Failed flush is recoverable.

### Journey 5: Session End Saves Accomplishments

User scenario: "I finish work and expect accepted decisions/accomplishments to be stored."

Sunny path:

1. Start session and record multiple turns.
2. Seed one Phase 1 pending moment.
3. Stop hook calls `/session/end`.
4. Phase 2 detector stub adds one accomplishment.
5. Daemon flushes both moments to the real BiBLE memory import API.
6. Session status becomes completed.

Expected user-visible result:

- Stop hook exits successfully.
- Status shows no pending moments for completed session.
- Captured BiBLE client payload or imported test memory contains both moments and monitoring metadata if present.

Rainy paths:

- Phase 2 detector fails: existing Phase 1 moment still flushes.
- BiBLE is unreachable: Stop hook exits successfully, moments remain pending for retry.
- Stop hook cannot reach daemon: hook exits success and recovery handles the session next time.

Pass criteria:

- Session close never blocks or crashes Claude Code equivalent runner.
- No buffered turns are deleted before successful local persistence.

### Journey 6: Crash Recovery Restores Prior Work

User scenario: "My machine or Claude Code crashed yesterday; today I reopen the project."

Sunny path:

1. Create prior session with `status='active'`, buffered turns, and unflushed moments.
2. Start a new session through SessionStart hook.
3. Daemon detects unclosed prior session.
4. Fast path injects prior turns summary and moments.
5. Slow path queues retrospective detection and flush.

Expected user-visible result:

- New session receives crash recovery context.
- User can continue immediately.
- Later status/hint reports recovery or flush result.

Rainy paths:

- Slow path detector fails: existing moments remain usable and prior session is marked according to recovery policy.
- BiBLE is unreachable: recovery still injects local context, flush deferred.
- Multiple unclosed sessions exist: recovery reports count and injects bounded context.

Pass criteria:

- Recovery does not depend on BiBLE.
- Startup is not blocked by retrospective LLM work.
- Prior data is not silently lost.

### Journey 7: User Manually Consults BiBLE

User scenario: "I think the model missed relevant memory, so I manually ask BiBLE."

Sunny path:

1. Seed the real BiBLE test server with one memory, one knowledge document, and one skill result under the test run namespace.
2. Poll import tasks until completed with the harness polling budget.
3. Run `/bible-cc:consult project context`.
4. Verify daemon calls three search routes.
5. Verify returned context includes merged hits.

Expected user-visible result:

- Consult reports query used and injectable recall context.
- Results are grouped or labeled by domain.

Rainy paths:

- Run `/bible-cc:consult` with no query: LLM query summarizer stub produces query.
- One domain search fails: output includes partial results and diagnostic, or returns structured failure according to implementation policy.
- All domains return empty: output is an empty recall block, not a crash.
- BiBLE is unreachable: command reports unreachable and leaves local session unaffected.

Pass criteria:

- Consult is clearly user-initiated pull.
- SessionStart remains free of speculative remote search.

### Journey 8: Model Uses MCP Tools Mid-Session

User scenario: "During a conversation, the model searches BiBLE memory and knowledge through MCP tools."

Sunny path:

1. Start MCP server with the real BiBLE test server URL.
2. Invoke `bible_memory_search`.
3. Invoke `bible_knowledge_search`.
4. Invoke `bible_skill_search`.
5. Invoke `bible_memory_save`.

Expected model-visible result:

- Each tool returns structured results or import task response.
- Results preserve domain labels and scores.
- MCP server remains alive across calls.

Rainy paths:

- Daemon is stopped: MCP tools still work.
- MCP server uses an unused localhost BiBLE URL: tools return structured unreachable errors.
- Postponed tool is invoked: model receives "not yet available" with reason.
- Memory/skill get download task fails: tool returns structured error.

Pass criteria:

- MCP server never calls daemon endpoints.
- Tool failure does not terminate stdio server.

### Journey 9: Offline Graceful Degradation

User scenario: "BiBLE Atlas is down, but I still want Claude Code to work and local capture to continue."

Sunny path:

1. Start daemon with BiBLE URL unavailable.
2. SessionStart hook succeeds.
3. UserPromptSubmit and PostToolUse hooks buffer turns locally.
4. Detector stub captures pending moments.
5. Status reports BiBLE unreachable and pending moments.

Expected user-visible result:

- Claude Code equivalent runner continues normally.
- User sees status/check-bible diagnostics.
- Pending moments remain local.

Rainy paths:

- MCP search is attempted: returns model-visible unreachable error.
- Push is attempted: returns failure and leaves moments pending.
- BiBLE later returns: retry-push or push flushes pending moments.

Pass criteria:

- No local data is discarded because BiBLE is down.
- Hook paths never block on remote BiBLE.

### Journey 10: Daemon Startup Failure Is Actionable

User scenario: "The daemon cannot start because its port is occupied."

Sunny path:

1. Occupy configured daemon port.
2. Set `daemon.port_auto_fallback=true`.
3. SessionStart starts daemon on fallback port.
4. Status reports actual port.

Expected user-visible result:

- User does not need manual intervention when fallback is enabled.

Rainy paths:

1. Occupy configured port.
2. Set fallback disabled.
3. Run SessionStart.

Expected user-visible result:

- Hook stdout contains an error hint naming the port.
- Hint tells user to run `/bible-cc:status` or resolve the conflict.
- Later turn hooks silently skip.

Pass criteria:

- Startup failure is visible at SessionStart.
- Claude Code equivalent runner is not crashed by the plugin.

### Journey 11: Private Session Does Not Capture

User scenario: "I am about to discuss private content and do not want it captured."

Sunny path:

1. Run capture pause command or set matching bypass pattern.
2. Submit user and tool turns.
3. Resume capture.
4. Submit non-private turn.

Expected user-visible result:

- Private turns are not in `turns`.
- No moment is created from private turns.
- Later non-private turn is captured after resume.

Rainy paths:

- Pause is run twice: second call is idempotent.
- Resume is run twice: second call is idempotent.
- Invalid bypass regex in config is rejected with diagnostic and safe default.

Pass criteria:

- Privacy controls are observable through status/context/debug commands.
- No private content appears in logs or hints.

### Journey 12: Operations Lifecycle

User scenario: "I install, restart, reload, upgrade, and uninstall the plugin while keeping clear control over local data."

Sunny path:

1. Install dependencies with `uv sync` in the plugin repo.
2. Run setup in isolated HOME with the real BiBLE test server URL.
3. Start a session and create turns plus one pending moment.
4. Stop daemon through `/daemon/stop`.
5. Run SessionStart again and verify daemon restarts automatically.
6. Write a stale `daemon.pid` for a nonexistent process and verify SessionStart replaces it.
7. Simulate plugin reload or upgrade by restarting daemon and rerunning migration.
8. Verify pending local data remains available.
9. Run uninstall in the isolated HOME.

Expected user-visible result:

- Setup is idempotent and does not overwrite existing user config.
- Restart is automatic on next SessionStart.
- Status reports schema version.
- Pending moments remain available.
- Recovery works after reload/upgrade.
- Uninstall stops daemon and removes scoped local config/database.
- Uninstall displays explicit guidance for Claude Code plugin registry/cache cleanup, or performs that cleanup only inside the disposable test plugin profile.

Rainy paths:

- Setup is run twice: second run preserves existing config.
- Stop is run when daemon is already stopped: operation is idempotent or returns actionable no-op.
- Stale PID points to a nonexistent process: SessionStart starts a fresh daemon.
- Stale PID points to a different live process: operation refuses to kill it and reports a diagnostic.
- Config file has older schema: migration/defaulting preserves known fields.
- New optional column is missing: migration adds it without deleting rows.
- Daemon dies during reload: next SessionStart starts it again.
- Uninstall cannot reach daemon: command reports daemon stop problem and still protects unrelated files.
- Uninstall is run twice: second run is a no-op or reports already removed.

Pass criteria:

- Existing SQLite data is preserved.
- No migration requires manual user action for additive schema changes.
- Uninstall only removes the isolated `~/.bible-cc` test state and never calls BiBLE delete APIs.
- Uninstall covers Claude Code plugin registry/cache handling as explicit user guidance or sandbox-scoped cleanup.
- No operation requires venv activation.

---

## 4. Manual Smoke Checklist

Manual smoke is run before release or marketplace packaging.

1. Install plugin in a disposable Claude Code profile.
2. Run setup with the real BiBLE test server URL.
3. Open a fresh Claude Code session and verify no speculative BiBLE search.
4. Make a clear decision and verify captured-moment hint appears inline.
5. Run `/bible-cc:review`, edit the moment, then `/bible-cc:push`.
6. Reconfigure BiBLE URL to an unused localhost port and verify `/bible-cc:status` reports unreachable without breaking Claude Code.
7. Restart Claude Code with an unclosed session and verify crash recovery injection.
8. Start MCP server through Claude Code and ask the model to search memory; verify model-visible tool result.

Manual smoke pass criteria:

- Hints appear in transcript where users can see them.
- Commands are discoverable and readable.
- Failure messages are actionable without daemon log inspection.
- No command asks the user to activate a venv.

---

## 5. Automated Commands

Run all E2E tests:

```bash
uv run pytest tests/e2e
```

Run a focused journey:

```bash
uv run pytest tests/e2e/test_session_capture_flow.py
```

Run slow/manual-tagged tests only when explicitly requested:

```bash
uv run pytest tests/e2e -m manual
```

---

## 6. Pass Criteria

E2E suite passes when:

1. Each automated journey has one sunny path and at least one rainy path.
2. User-visible behavior matches the architecture docs: local recovery, pull-based BiBLE search, review before flush, graceful degradation.
3. BiBLE client call capture and unreachable-URL journeys prove when BiBLE is and is not called.
4. Hook stdout proves hints and injected context behavior.
5. SQLite inspection proves no local data loss across crash, offline mode, and reload.
6. Real BiBLE async imports/downloads use bounded polling with clear timeout diagnostics.
7. The suite is deterministic in CI without real Claude Code UI, real Anthropic API, or shared production BiBLE data.
