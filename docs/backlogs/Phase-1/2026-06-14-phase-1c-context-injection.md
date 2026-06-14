# Phase 1c: Context Injection + Crash Recovery

> **依赖**: Phase 1b（session/turn 端点 + recovery 数据）
> **被依赖**: Phase 1d（debug 端点、verbose health 需要完整 HTTP API）
> **父文档**: [Phase 1 总览](2026-06-13-phase-1-daemon-core.md)

**交付 Command**: `/bible-cc:context`（新增 `commands/context.md`，调 `POST /context/inject`）

**预估: 1.5 天**

### 测试标注

默认 `[Unit] [Pre]`（注入逻辑、seq 恢复），crash recovery 慢路测试例外 → `[Integration] [Post]`。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## 3. Sub-Phase 1c: Context Injection + Crash Recovery（1.5d）

### Scenario

> 用户上次的 Claude Code session 异常终止（daemon crash / 系统重启）。今天打开 Claude Code，SessionStart hook 触发 → daemon 启动 → Step 5 crash recovery scan 发现一个 unclosed session → 快路从 SQLite 读取该 session 的 3 个 unflushed moments 和 15 个 turns → Step 6 启动 uvicorn。
>
> SessionStart hook 继续 → `POST /session/start` 返回 `recovery.moments_recovered=3` → `POST /context/inject` 检测到 crash recovery 场景 → 从快路读取的数据中构建 `<relevant-memories>`，包含 "上次 session 摘要：15 turns，3 个未 flush 的 moments" → 注入 Claude Code system prompt。模型看到这个上下文，知道上次做什么、有什么待处理。
>
> 在同一 session 中，用户用 `/clear` 清空对话后继续工作。SessionStart hook 再次触发 → `/context/inject` 检测到当前 session 已有 5 个 turns + 1 个 unflushed moment → 注入 turns 摘要 + moments → 模型在新对话树中仍然知道刚才做了什么。
>
> 用户执行 `/bible-cc:context` 查看当前注入的上下文内容。

**交付 Command**: `/bible-cc:context`

用户输入 `/bible-cc:context`，CC 执行 `commands/context.md` → 调 `POST /context/inject`（传入当前 session_id + 空 user_message）→ 转录中展示注入的上下文内容（turns 摘要 + unflushed moments + crash recovery 数据），以及当前所处的注入分支（`branch: crash_recovery` | `clear_or_compact` | `empty`）。

---

### Feature 1c.1: POST /context/inject（三场景分支）

**Scenario**: SessionStart hook 第三步调用 `POST /context/inject {session_id, user_message}` → daemon 根据本地 buffer 状态选择三个分支之一 → 构建并返回 `<relevant-memories>` XML block。纯本地 SQLite 操作——禁止在此端点内调用 BiBLE API。

| 属性 | 说明 |
|------|------|
| **理由** | Pull model 的第一条路径（SessionStart 本地注入）。CLAUDE.md 硬性约束：纯本地 SQLite 操作。三种场景分支是硬性要求：新 session（空 buffer）、/clear 或 compact（当前 session turns + moments）、crash recovery（prior unclosed session data）。 |
| **优先级** | P0 — recall pipeline 基础 |
| **依赖** | 1a.2（session/moment CRUD）、1b.1（/session/start 提供的 recovery 数据）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `01-architecture-overview.md` | §2 Pull Model | 三种 SessionStart 场景表：新 session / /clear 或 compaction / crash recovery |
| `01-architecture-overview.md` | §5 硬性约束 #1 | "本地优先：本地 SQLite buffer 是 primary source" |
| `02-interfaces.md` | §1.4 | `/context/inject` endpoint spec: `{context: string, sources: {turns, moments, crash_recovery}}` |
| `03-daemon/http-api.md` | §5.1 | 三种场景检测条件 + 构建逻辑 + token budget 截断 |
| `03-daemon/http-api.md` | §5.1 禁止 | "此端点内不得调用 BiBLE API。上下文仅来自本地 SQLite。" |
| `06-recall/local-injection.md` | （如已写） | 三场景注入细节、XML block 格式 |
| `04-config/schema.md` | §2.3 injection | `token_budget: 1200`, `inject_fallback: "skip" | "empty"` |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| 场景判断逻辑错误（如 crash recovery 数据存在但走进了 empty 分支） | `/context/inject` 返回空 context | 模型没有获得恢复的上下文——从用户视角看"记忆丢失" | `daemon.log` 搜索 `context/inject.*branch`（debug level）查看选择了哪个分支 + 判断依据 | `/bible-cc:context` 检查 → `curl POST /context/inject` 直接调用确认 |
| turns 表数据量巨大导致 context 构建超时 | SQLite query 耗时 >1s | SessionStart hook 等待超时 | `daemon.log` 搜索 `context/inject.*slow query` | 降低 `token_budget` 或增加 `LIMIT` 在 SQL 查询中 |
| token_budget=0 但 injection.enabled=true | 边缘情况 | 返回空 context——符合预期（0=不注入任何内容） | 无 | 无需恢复 |
| XML block 格式错误（截断导致未闭合标签） | token budget 截断恰好切在标签中间 | 模型收到损坏的 XML | `daemon.log` 搜索 `context.*malformed` | 截断逻辑必须检查 XML 闭合——宁可多截 100 chars 也要保证格式正确 |

**Function-Level Steps**（按实现顺序）:

```python
# server.py

async def context_inject(request: ContextInjectRequest) -> ContextInjectResponse:
    """POST /context/inject route handler。
    1. 验证 session_id
    2. 检查 injection.enabled → false 则直接返回空 context
    3. 调用 determine_injection_scenario() 判断三场景之一
    4. 按场景调用对应的构建函数
    5. 应用 token_budget 截断（保证 XML 闭合）
    6. 返回 {context, sources}
    """

# injector.py（新文件）或 buffer.py

def determine_injection_scenario(conn, session_id: str, recovery_data: dict | None) -> str:
    """判断注入场景: 'empty' | 'clear_or_compact' | 'crash_recovery'。
    依据：recovery_data 是否为 null、当前 session 是否有 turns/moments。
    """

def build_empty_context(fallback_mode: str) -> str:
    """空 buffer 时的输出。skip → ""; empty → "<relevant-memories></relevant-memories>"。
    """

def build_turns_summary(conn, session_id: str, max_turns: int = 20) -> str:
    """从 turns 表构建摘要：最近 N turns 的 role + 前 100 chars 内容。
    返回纯文本（非 XML，由调用方包装）。
    """

def build_moments_context(conn, session_id: str) -> str:
    """从 moments 表读取 unflushed moments → 格式化为 <moment type="..." title="...">narrative</moment>。
    """

def build_crash_recovery_context(recovery_moments: list[dict], recovery_turns: list[dict]) -> str:
    """从 crash recovery 快路数据构建上下文（不查 SQLite——数据已由 /session/start 快路读取）。
    """

def apply_token_budget(context: str, budget: int) -> str:
    """按 token_budget 截断 context（char_count / 3 ≈ token_count）。
    截断时保证 XML 标签闭合，末尾加 [truncated] 标记。
    """
```

**交付标准**:

- [ ] 新 session 无 crash → 返回空 context（`inject_fallback=skip`）或空 block（`inject_fallback=empty`）
- [ ] `/clear` 后同 session → 包含 turns 摘要 + unflushed moments
- [ ] Crash recovery → 包含 prior session data
- [ ] token_budget 截断保证 XML 闭合
- [ ] 不调用 BiBLE API（验证：此函数无 `client.py` import）
- [ ] `injection.enabled=false` 时立即返回空

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_inject_new_session_skip_fallback` — 空 buffer + skip → `context=""`
- [ ] `test_inject_new_session_empty_fallback` — 空 buffer + empty → `<relevant-memories></relevant-memories>`
- [ ] `test_inject_clear_scenario_has_turns_summary` — 当前 session 有 5 turns → context 含 turn 摘要
- [ ] `test_inject_clear_scenario_has_moments` — 当前 session 有 unflushed moments → context 含 moments
- [ ] `test_inject_crash_recovery_scenario` — recovery data 非 null → context 含 crash recovery moments
- [ ] `test_inject_token_budget_truncation` — 长 context → 截断后 XML 标签闭合
- [ ] `test_inject_disabled_returns_empty` — injection.enabled=false → 空

*意图测试*:
- [ ] `test_context_inject_never_calls_bible` — **意图: 本地优先**。`/context/inject` 的调用链中不应该出现任何网络 I/O。验证方法：mock `httpx.AsyncClient` → 断言它从未被调用。如果 Phase 1 就在 `/context/inject` 中调 BiBLE，SessionStart hook 的启动时间会变成 BiBLE 的延迟——违反"永不阻塞 Claude Code"原则。
- [ ] `test_xml_truncation_preserves_valid_structure` — **意图: 不产生损坏内容**。Token budget 截断是必需的，但截断后的内容必须能被模型正确解析。如果截断产生 `<moment` 而未闭合，模型可能误解上下文——比不注入更糟糕。验证方法：构造刚好在标签中间的截断点，断言结果不含未闭合标签。
- [ ] `test_recovery_context_distinct_from_normal_context` — **意图: 信息准确性**。Crash recovery 注入的 moments 必须明确标注来源（如 `[Recovered from prior session]`），让模型知道这是历史数据而非当前 session 产生的。如果恢复的 moment 和当前 session 的 moment 混在一起无标记，模型会误判上下文。

---

### Feature 1c.2: Crash Recovery — 快路 SQLite 读取

**Scenario**: `POST /session/start` 中，crash recovery 快路同步执行——扫描 unclosed sessions → 读取它们的 unflushed moments 和 turns → 暂存内存供后续 `/context/inject` 使用。快路必须在 <100ms 内完成（纯 SQLite 读取）。

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束：crash recovery 快路同步不阻塞启动。快路读 SQLite 是毫秒级操作——读取 moments 和 turns 后立即返回，让 SessionStart hook 的后续 `/context/inject` 使用。慢路（LLM + flush）异步执行，Phase 2 接入。 |
| **优先级** | P0 — 数据可靠性 |
| **依赖** | 1b.1（/session/start 调用此功能）、1a.2（session/moment CRUD）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/startup.md` | §1.1 Step 5 | 快路 SQL：`SELECT * FROM moments WHERE session_id IN (...) AND flushed IN (0, -1)` |
| `03-daemon/startup.md` | §1.1 Step 5 慢路 | `asyncio.create_task(retrospective_and_flush(session))` — Phase 1 占位 |
| `03-daemon/http-api.md` | §3.1 /session/start | Crash recovery 快路取 moments + turns 暂存内存 |
| `08-operability/failure-paths.md` | §F2 | daemon 中途 crash → 数据在 SQLite → 下次自动 recovery |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Unclosed session 数据量巨大（长时间 crash 后累积） | 快路 SQL 查询耗时 >500ms | SessionStart hook 延迟——首次启动等待时间较长 | `daemon.log` 搜索 `crash recovery scan.*N sessions.*large` | 正常现象——每个 session 的 turns 取最近 30 条做摘要，moments 全量读取。如果 <100 sessions 且每 session turns<500，应在 200ms 内完成 |
| 快路读取到损坏的数据（非完整 row） | `sqlite3.Row` 访问缺失字段 → KeyError | session 仍然创建成功，但 recovery=null | `daemon.log` 搜索 `corrupt row in recovery` | `PRAGMA integrity_check` 检查 DB → 必要时重建 |
| 多个 unclosed sessions 中有一个已经 flush 到 BiBLE | moments WHERE flushed IN (0, -1) 筛选掉已 flush 的 | recovery 可能比预期少的 moments | `daemon.log` 搜索 `recovery.*skipped.*already flushed` | 正常——已 flush 的数据不需要恢复 |

**Function-Level Steps**（按实现顺序）:

```python
# buffer.py

def scan_unclosed_sessions(conn: sqlite3.Connection, current_session_id: str) -> list[str]:
    """SELECT session_id FROM sessions WHERE status='active' AND session_id != ?。
    返回 unclosed session_id 列表（不含当前 session）。
    """

def get_recovery_moments(conn: sqlite3.Connection, session_ids: list[str]) -> list[dict]:
    """SELECT * FROM moments WHERE session_id IN (...) AND flushed IN (0, -1)。
    只取未 flush 和 failed 的 moments。已成功 flush 的跳过。
    """

def get_recovery_turns_summary(conn: sqlite3.Connection, session_ids: list[str], limit: int = 30) -> list[dict]:
    """SELECT * FROM turns WHERE session_id IN (...) ORDER BY seq DESC LIMIT ?。
    每个 session 取最近 N turns 用于构建恢复摘要。
    """

# server.py

# 在 start_session() route handler 中:
# recovery_data = {
#     "moments": get_recovery_moments(conn, unclosed),
#     "turns": get_recovery_turns_summary(conn, unclosed)
# }
# → 暂存于内存（dict session_id → recovery_data）
# → 供后续 /context/inject 使用
```

**交付标准**:

- [ ] 快路读取 ≤ 100ms（<10 unclosed sessions, <500 turns total）
- [ ] 只恢复 `flushed IN (0, -1)` 的 moments
- [ ] 排除当前 session_id（不把自己当 recovery 源）
- [ ] 慢路 async task 创建（Phase 1 占位，无实际逻辑）
- [ ] 快路失败不阻止 session 创建（recovery=null, session 正常创建）

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_scan_unclosed_excludes_current_session` — current session 即使 active 也不在结果中
- [ ] `test_scan_unclosed_only_finds_active` — completed session 不出现在结果中
- [ ] `test_get_recovery_moments_filters_flushed` — flushed=1 的 moments 不出现在 recovery 结果中
- [ ] `test_get_recovery_turns_summary_respects_limit` — limit=30 → 最多返回 30 turns

*意图测试*:
- [ ] `test_fast_path_does_not_block_for_large_data` — **意图: 快路不阻塞启动**。即使有 50 个 unclosed sessions，快路也应在 500ms 内完成。如果快路耗时 >1s，SessionStart hook 的 60s 超时可能不够（网络 + daemon 启动 + 快路 + context inject）。验证方法：批量插入 50 sessions × 200 turns → 测量快路扫描耗时。
- [ ] `test_slow_path_failure_does_not_affect_fast_path` — **意图: 隔离**。慢路 async task 的失败不能影响快路数据。验证方法：mock 慢路 task 抛异常 → 断言快路的 recovery_data 仍然完整可读、session 正常创建。

---

### Feature 1c.3: Seq 计数器恢复

**Scenario**: Daemon 启动时（或 `/session/start` 时），从 SQLite 恢复每个活跃 session 的 turn seq 计数器。如果 daemon 重启后不恢复计数器，新 turn 会从 1 开始分配 seq——与已有 turns 的 UNIQUE(session_id, seq) 约束冲突。

| 属性 | 说明 |
|------|------|
| **理由** | turns 表的 `UNIQUE(session_id, seq)` 约束要求每个 session 内 seq 唯一。Daemon 用内存计数器 `session_seq: dict[str, int]` 跟踪当前 seq。如果 daemon 重启，内存丢失——必须从 SQLite 恢复。 |
| **优先级** | P0 — 数据完整性 |
| **依赖** | 1a.2（turn CRUD）、1b.3（turn 端点）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/startup.md` | §1.1 Step 5 恢复 seq | `SELECT session_id, MAX(seq) FROM turns GROUP BY session_id` → `session_seq[session_id] = max_seq` |
| `03-daemon/startup.md` | §1.1 Step 5 边缘情况 | "max_seq is None" 分支 → seq 从 1 开始 |
| `03-daemon/sqlite-schema.md` | §2.3 turns | `seq INTEGER NOT NULL` per-session, `UNIQUE(session_id, seq)` |
| `03-daemon/http-api.md` | §4.1 内部流程 Step 2 | seq 分配：内存计数器 `session_seq[session_id] += 1` |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| seq 计数器未恢复（daemon 重启后） | 第一次 `/turn/*` 调用时 `UNIQUE constraint failed: turns.session_id, turns.seq` | 500 error | `daemon.log` 搜索 `UNIQUE constraint failed.*turns.*seq` | 这是 bug——说明 start_session 或 daemon startup 遗漏了 seq 恢复。手动重启 daemon |
| session 存在但 MAX(seq) 返回 NULL | `COALESCE(MAX(seq), 0)` → 0 | 从 seq=1 开始，正常——session 被创建但尚无任何 turn | 无 | 正常行为 |
| 内存计数器与 DB 不同步（极端竞态） | db 中的 MAX(seq) > 内存计数器 → 下次 insert 冲突 | 500 error + `UNIQUE constraint failed` | `daemon.log` 搜索 `seq counter out of sync` | 重新从 DB 读取 MAX(seq) 恢复 |

**Function-Level Steps**（按实现顺序）:

```python
# buffer.py

# 全局内存计数器
session_seq: dict[str, int] = {}

def recover_seq_counters(conn: sqlite3.Connection) -> None:
    """从 SQLite 恢复所有活跃 session 的 seq 计数器。
    SELECT session_id, COALESCE(MAX(seq), 0) FROM turns GROUP BY session_id。
    同时覆盖 sessions 表中 status='active' 但 turns 表尚无记录的 session。
    """

def get_next_seq(session_id: str) -> int:
    """获取 session_id 的下一个 seq。内存计数器 +1。
    如果 session_id 不在计数器 dict 中 → 从 SQLite 恢复（防御性）。
    """
```

**交付标准**:

- [ ] Daemon 启动时自动恢复所有活跃 session 的 seq 计数器
- [ ] `/session/start` 后新 session 的 seq 从 1 开始
- [ ] Daemon 重启后 turn 写入不产生 UNIQUE 约束冲突
- [ ] 内存计数器丢失时防御性从 DB 恢复

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_recover_seq_counters_restores_from_db` — 先插入 5 turns（seq 1-5） → 清空内存计数器 → recover → get_next_seq 返回 6
- [ ] `test_recover_seq_counters_handles_empty_session` — session 存在但无 turns → MAX(seq)=NULL → 计数器=0
- [ ] `test_get_next_seq_defensive_fallback` — session 不在计数器 dict 中 → 从 SQLite 查询 MAX(seq) → +1

*意图测试*:
- [ ] `test_seq_recovery_prevents_silent_data_corruption` — **意图: 数据完整性**。seq 计数器不恢复会导致 UNIQUE 约束冲突 → 500 error → turn 丢失。更糟的是，如果使用了 `INSERT OR REPLACE` 而非严格的 UNIQUE 约束检查——旧 turn 被静默覆盖，数据损坏无感知。验证方法：mock 重启场景（新建 conn + 清空计数器）→ 插入新 turn → 断言 seq 正确 + 旧数据未被覆盖。
- [ ] `test_seq_counter_in_memory_only_not_in_config` — **意图: 正确的作用域**。Seq 计数器是运行时状态，不应持久化到 config 文件或单独的 state 文件。SQLite 的 MAX(seq) 是唯一的真实来源。验证方法：确认 recover_seq_counters 只读 SQLite——不读任何 JSON/TOML/INI 文件。

---

