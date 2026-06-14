# Phase 1a: SQLite Schema + buffer.py

> **依赖**: Phase 0（daemon lifecycle CLI、health endpoint、config 系统、hook bridge）
> **被依赖**: Phase 1b（session/turn 端点依赖 buffer.py CRUD 和 SQLite schema）
> **父文档**: [Phase 1 总览](2026-06-13-phase-1-daemon-core.md)

**交付 Command**: `/bible-cc:status`（将 Phase 0 的空占位 `commands/status.md` 落地为可工作 slash command）

**预估: 2 天**

### 测试标注

默认 `[Unit] [Pre]`——全部纯函数，`tmp_path` SQLite / pytest mock，无外部进程。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## 1. Sub-Phase 1a: SQLite Schema + buffer.py（2d）

### Scenario

> 用户执行 `./bible-cc start`，daemon 启动。启动序列第 3 步打开 `~/.bible-cc/daemon.db`，设置 WAL mode，确保后续并发写入不会产生 SQLITE_BUSY。第 4 步执行 schema migration，幂等地创建 sessions/turns/moments/metrics 四张表。启动完成后，`/bible-cc:status` 显示 sqlite.integrity="ok" 和 schema_version=1。
>
> 后续任何 hook 触发的 turn 写入都会走 buffer.py 的 CRUD 函数——session 创建、turn 插入（per-session seq 自增）、moment 插入（content-hash 去重）。所有操作记录在 `~/.bible-cc/daemon.log` 中，带 request-id 可追溯。

**交付 Command**: `/bible-cc:status`

Phase 0 的 `commands/status.md` 为空占位。1a 将此文件落地为可工作的 slash command——用户输入 `/bible-cc:status`，CC 执行 `commands/status.md` → 调 `GET /daemon/health` → 转录中展示：

```
sqlite:
  integrity: ok
  schema_version: 1
  size_bytes: 204800
```

用户一眼确认存储层在线、schema 版本正确、DB 文件大小正常。

---

### Feature 1a.1: SQLite Schema 创建 + PRAGMA

**Scenario**: Daemon 启动，执行 `sqlite3.connect()` 打开（或创建）`daemon.db`，立即执行 `PRAGMA journal_mode=WAL` 和 `PRAGMA busy_timeout=5000`，然后运行 `CREATE TABLE IF NOT EXISTS` 创建四张表。这是硬性约束——违反顺序会导致并发写入冲突。

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束：WAL mode 必须第一个 PRAGMA 执行，`busy_timeout=5000` 紧随其后。这两个 PRAGMA 是多 session 并发写入不产生 SQLITE_BUSY 的唯一保证。 |
| **优先级** | P0 — 存储基础，所有数据读写都经过它 |
| **依赖** | `sqlite3`（stdlib），无 |

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/sqlite-schema.md` | §1 PRAGMA | `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;` — WAL 写不阻塞读，busy_timeout 等待最多 5s |
| `03-daemon/sqlite-schema.md` | §2.1-2.5 表结构 | sessions、turns（UNIQUE(session_id, seq)）、moments（content_hash UNIQUE）、metrics 的完整 DDL |
| `03-daemon/sqlite-schema.md` | §3 索引策略 | 5 个索引：idx_sessions_status、idx_turns_session_seq、idx_moments_session、idx_moments_flushed、idx_moments_content_hash |
| `03-daemon/sqlite-schema.md` | §6 并发策略 | 单 session 串行、多 session WAL+busy_timeout、不引入连接池 |
| `03-daemon/startup.md` | §1.1 Step 3 | `conn.row_factory = sqlite3.Row`、`mkdir(parents=True)` 自动创建父目录 |
| `03-daemon/startup.md` | §3 DB 路径 | `~/.bible-cc/daemon.db`，env override `BIBLE_CC_DB_PATH` |
| `01-architecture-overview.md` | §5 硬性约束 #1 | "daemon 启动时必须执行 PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; 无例外" |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| DB 文件无法创建（磁盘满/权限不足） | `sqlite3.connect()` 抛 `OperationalError` | SessionStart hook stdout: `❌ daemon cannot start — cannot open database at ~/.bible-cc/daemon.db` | `~/.bible-cc/daemon.log` 搜索 `FATAL.*cannot open database` | `df -h ~/.bible-cc/` 检查磁盘 → `ls -la ~/.bible-cc/` 检查权限 → `chmod` 或清理空间后重启 daemon |
| WAL PRAGMA 失败 | `PRAGMA journal_mode` 返回值 ≠ "wal" | daemon 不启动，SessionStart hook stdout 输出 error hint | `daemon.log` 搜索 `PRAGMA journal_mode` | 检查 SQLite 版本 ≥ 3.7.0（`python -c "import sqlite3; print(sqlite3.sqlite_version)"`） |
| busy_timeout PRAGMA 失败 | SQLite 返回 error | 同上，daemon 不启动 | `daemon.log` 搜索 `PRAGMA busy_timeout` | 极少发生；SQLite 版本过旧同上处理 |
| DB 文件损坏 | `PRAGMA integrity_check` 返回非 "ok" | `/bible-cc:status` 显示 `sqlite.integrity: "error"` | `daemon.log` 搜索 `integrity_check` | `mv daemon.db daemon.db.bak` → 重启 daemon 自动创建新 DB → 历史数据丢失，但 daemon 恢复可用 |
| 多个 daemon 进程竞争同一 DB | 第二个进程尝试 open 时 WAL 文件被锁 | daemon 不启动 | `daemon.log` 搜索 `database is locked` | `ps aux | grep bible` 找残留进程 → `kill` |


**hint 通知的完整链路说明**:

Troubleshooting 表中提到的 "SessionStart hook stdout: `❌ daemon cannot start...`" 涉及两层实现，daemon 侧和 hook 侧都需要自行开发代码：

| 层 | 文件 | 职责 | 实现方式 |
|----|------|------|---------|
| **Daemon 侧** | `server.py` | 启动失败时返回 structured error response（500 + `{error: {code: "INTERNAL_ERROR", message: "..."}}`） | FastAPI exception handler，catch 致命错误 → 构建 JSON error body |
| **Hook 侧** | `scripts/hook.py` 的 `session-start` 命令 | 调用 `POST /daemon/start`，收到非 200 → **自行**解析 error body → 格式化 hint → `print()` 到 stdout | `httpx` 调 daemon，检测 `response.status_code != 200` → 从 `response.json()["error"]["message"]` 提取 → 包装为 hint 格式 → print |

Hook stdout 被 Claude Code 的 hook 机制捕获，设置 `inject: true` 后同时出现在 transcript 和 system prompt 中。**无需系统 API**——就是普通的 HTTP 调用 + `print()`。

对应的 Function-Level Steps 补充到下方实现步骤中。

**Function-Level Steps**（按实现顺序）:

```python
# Step 1: 打开/创建数据库 + 初始化连接
# buffer.py
def open_database(db_path: str) -> sqlite3.Connection:
    """打开 SQLite 数据库，创建父目录（如需要），设置 row_factory。
    返回已连接但尚未执行 PRAGMA 的 conn 对象。
    不在此函数内执行 PRAGMA——由调用方控制顺序。
    """

# Step 2: 执行 PRAGMA（必须在任何读写之前）
# buffer.py
def apply_pragmas(conn: sqlite3.Connection) -> None:
    """执行 PRAGMA journal_mode=WAL 和 PRAGMA busy_timeout=5000。
    必须在 open_database 之后、任何 CREATE/INSERT 之前调用。
    返回 None。若 PRAGMA 失败，抛出 RuntimeError（触发 daemon 启动失败）。
    """

# Step 3: 创建所有表（幂等）
# buffer.py
def create_tables(conn: sqlite3.Connection) -> None:
    """执行 CREATE TABLE IF NOT EXISTS 创建 sessions/turns/moments/metrics 四表 + 5 个索引。
    幂等——重复执行不报错。
    """

# Step 4: 验证 schema 完整性
# buffer.py
def verify_integrity(conn: sqlite3.Connection) -> str:
    """执行 PRAGMA integrity_check，返回结果字符串。
    "ok" = 正常。非 "ok" = 损坏。
    """

# Step 5: Daemon 侧 — 启动失败 → structured error（server.py）
# ⚠️ 本 Step 依赖 daemon HTTP 端点和 hook bridge——1a 阶段仅定义签名，Phase 1b 连线验证
@app.exception_handler(DaemonStartError)
async def daemon_start_error_handler(request, exc):
    """将致命错误转为 structured JSON error response（500 + error.code + error.message）。
    Hook 侧依赖这个格式解析错误原因。
    """

# Step 6: Hook 侧 — 调 daemon + 解析 error + print hint（scripts/hook.py）
# ⚠️ 同上——1a 阶段仅定义签名，Phase 1b 连线验证
async def handle_session_start(session_id: str) -> None:
    """SessionStart hook 的三步流程入口。
    1. 调 POST /daemon/start
    2. 若 status_code != 200: 解析 response.json()["error"]["message"] → 构建 hint → print() → return
    3. 若成功: 调 POST /session/start → POST /context/inject → print context
    """

def build_error_hint(error_message: str) -> str:
    """包装 daemon error message 为 hint 格式。
    ❌ bible-cc daemon error: {error_message}
    """
```

**交付标准**:

- [ ] `open_database()` 创建 `~/.bible-cc/daemon.db`（含父目录）
- [ ] `apply_pragmas()` 执行后 `PRAGMA journal_mode` 返回 "wal"
- [ ] `create_tables()` 执行后四表 + 5 索引存在
- [ ] `create_tables()` 可重复执行不报错（幂等）
- [ ] `verify_integrity()` 返回 "ok"

**测试用例**:

> **标注说明**: `[Unit]` = 纯函数，`tmp_path` SQLite/pytest mock，无需外部进程；`[Integration]` = 需要 daemon 进程或真实 SQLite WAL 并发；`[Contract]` = 需要 daemon HTTP 进程。`[Pre]` = 先于实现编写（Red-Green）；`[Post]` = 实现后验证接口契约。

*功能测试*:
- [ ] `[Unit] [Pre]` `test_open_database_creates_file_and_parent_dir` — `tmp_path` 下创建 db，验证文件存在 + 父目录自动创建
- [ ] `[Unit] [Pre]` `test_apply_pragmas_sets_wal_mode` — 执行 apply_pragmas 后 `PRAGMA journal_mode` 返回 "wal"
- [ ] `[Unit] [Pre]` `test_apply_pragmas_sets_busy_timeout` — `PRAGMA busy_timeout` 返回 5000
- [ ] `[Unit] [Pre]` `test_create_tables_is_idempotent` — 连续两次 `create_tables()` 不抛异常
- [ ] `[Unit] [Pre]` `test_create_tables_creates_all_four_tables` — `SELECT name FROM sqlite_master WHERE type='table'` 包含 sessions/turns/moments/metrics
- [ ] `[Unit] [Pre]` `test_verify_integrity_returns_ok` — 新建 DB 的 integrity_check 返回 "ok"

*意图测试*:
- [ ] `[Unit] [Pre]` `test_wal_before_any_write` — **意图: 并发安全**。验证 `apply_pragmas()` 先于 `create_tables()` 调用。mock 记录调用顺序——WAL 在任何 CREATE TABLE 之前。违反意图 = WAL 设了但表已在 DELETE 模式创建 = SQLITE_BUSY。
- [ ] `[Unit] [Pre]` `test_db_unavailable_raises_rather_than_silently_creates_wrong_file` — **意图: 失败不静默**。权限不足时 `open_database()` 必须抛异常，不能静默创建临时文件或回退到内存 DB。

---

### Feature 1a.2: buffer.py CRUD 层

**Scenario**: Hook 调用 daemon HTTP API → server.py 的 route handler 调用 buffer.py 的 CRUD 函数 → 数据写入 SQLite。每个函数是薄封装——单次 `conn.execute()` + `conn.commit()`，不做业务逻辑。

| 属性 | 说明 |
|------|------|
| **理由** | CRUD 是数据存取的基础层。每个函数职责单一：一个函数 = 一条 SQL。server.py 的 route handler 编排调用顺序。 |
| **优先级** | P0 — 所有 HTTP 端点依赖 CRUD |
| **依赖** | 1a.1（表已创建，PRAGMA 已执行）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/sqlite-schema.md` | §2.2 sessions 表 | `session_id TEXT PRIMARY KEY`, `status TEXT DEFAULT 'active'`, `turn_count`, `buffered_chars` |
| `03-daemon/sqlite-schema.md` | §2.3 turns 表 | `seq INTEGER NOT NULL` per-session, `UNIQUE(session_id, seq)`, `tool_output` 完整存储无截断 |
| `03-daemon/sqlite-schema.md` | §2.4 moments 表 | `content_hash TEXT UNIQUE NOT NULL`, `flushed INTEGER DEFAULT 0`, `phase TEXT DEFAULT '1'` |
| `03-daemon/http-api.md` | §3.1 /session/start | `INSERT INTO sessions` → `is_new` 判断，SessionStart hook 三步流程的第二步 |
| `03-daemon/http-api.md` | §4.1 /turn/user | `INSERT INTO turns` + `UPDATE sessions SET turn_count = turn_count + 1`，返回 <10ms |
| `03-daemon/http-api.md` | §4.2 /turn/tool | 同上，但 role='assistant'，含 tool_name/tool_arguments/tool_output |
| `03-daemon/http-api.md` | §3.2 /session/end | `UPDATE sessions SET status='completed'` — Phase 1 不跑 LLM/flush |
| `03-daemon/sqlite-schema.md` | §6 并发策略 | 单 session 串行、多 session WAL+busy_timeout、无需连接池——CRUD 函数是并发写入的直接执行者 |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Insert 不存在的 session_id | `INSERT OR IGNORE` 返回 0 行 → 或 FK 约束检查 | HTTP 端点在 insert turn 前先查 session 是否存在，不存在返回 400 `SESSION_NOT_FOUND` | `daemon.log` 搜索 `SESSION_NOT_FOUND` + request-id | 确认 SessionStart hook 已正确触发 `POST /session/start` |
| UNIQUE 约束冲突（seq 重复） | `sqlite3.IntegrityError` on `turns.UNIQUE(session_id, seq)` | 500 error + response body `INTERNAL_ERROR` | `daemon.log` 搜索 `UNIQUE constraint failed: turns.session_id, turns.seq` | **临时缓解**（1a 阶段 1c.3 尚未实现）：重启 daemon 强制从 SQLite `MAX(seq)` 恢复计数器。**根本修复**：见 Feature 1c.3 |
| moment content-hash 碰撞 | `INSERT OR IGNORE` 静默跳过 | 无感知——dedup 是正常操作 | `daemon.log` 搜索 `moment dedup` (debug level) | 不需恢复。`/bible-cc:review` 可显示 "N duplicates suppressed" |

**Function-Level Steps**（按实现顺序）:

```python
# Session CRUD
# buffer.py

def insert_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """INSERT OR IGNORE INTO sessions(session_id) VALUES(?)。
    返回 True 表示新创建（is_new），False 表示已存在。
    """

def mark_session_completed(conn: sqlite3.Connection, session_id: str) -> None:
    """UPDATE sessions SET status='completed', closed_at=datetime('now') WHERE session_id=?。
    Phase 1 不跑 LLM/flush——仅标记。
    """

def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """SELECT * FROM sessions WHERE session_id=?。用于验证 session 存在。"""

def count_active_sessions(conn: sqlite3.Connection) -> int:
    """SELECT COUNT(*) FROM sessions WHERE status='active'。用于 health check。"""

def count_completed_sessions(conn: sqlite3.Connection) -> int:
    """SELECT COUNT(*) FROM sessions WHERE status='completed'。用于 health check。"""

# Turn CRUD
# buffer.py

def get_next_seq(conn: sqlite3.Connection, session_id: str) -> int:
    """从内存计数器获取 session_id 的当前 seq + 1。
    首次调用时从 SQLite 恢复：SELECT MAX(seq) FROM turns WHERE session_id=? → 0 则从 1 开始。
    """

def insert_turn_user(conn: sqlite3.Connection, session_id: str, seq: int, message: str) -> int:
    """INSERT INTO turns(session_id, seq, role, content) VALUES(?,?,'user',?)。
    返回 turn_id (seq)。
    """

def insert_turn_tool(conn: sqlite3.Connection, session_id: str, seq: int,
                     tool_name: str, arguments: dict, output: str) -> int:
    """INSERT INTO turns(session_id, seq, role, tool_name, tool_arguments, tool_output)
    VALUES(?,?,'assistant',?,?,?)。
    tool_arguments 序列化为 JSON string。
    tool_output 完整存储——不截断。
    """

def increment_turn_count(conn: sqlite3.Connection, session_id: str, chars: int) -> None:
    """UPDATE sessions SET turn_count = turn_count + 1, buffered_chars = buffered_chars + ? WHERE session_id=?。"""

# Moment CRUD
# buffer.py

def insert_moment(conn: sqlite3.Connection, session_id: str, moment_type: str,
                  title: str, narrative: str, content_hash: str,
                  phase: str = '1') -> int | None:
    """INSERT INTO moments(...) VALUES(...)。
    使用 try/except IntegrityError 处理 content-hash UNIQUE 冲突 → 返回 None（dedup）。
    成功返回 moment_id。
    """

def get_unflushed_moments(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """SELECT * FROM moments WHERE session_id=? AND flushed=0 ORDER BY detected_at DESC。"""

def get_moments_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """SELECT * FROM moments WHERE session_id=? ORDER BY detected_at。用于 Phase 1 debug/内省。"""

def count_pending_moments(conn: sqlite3.Connection) -> int:
    """SELECT COUNT(*) FROM moments WHERE flushed=0。用于 health check。"""

def count_total_turns(conn: sqlite3.Connection) -> int:
    """SELECT COUNT(*) FROM turns。用于 health check。"""
```

**交付标准**:

- [ ] 所有 CRUD 函数有对应的单元测试（每个函数 ≥ 1 sunny case + ≥ 1 rainy case）
- [ ] `insert_session()` 重复调用返回 `False`（幂等）
- [ ] `insert_turn_user/tool()` 对不存在的 session_id 抛 `SESSION_NOT_FOUND`
- [ ] `insert_moment()` content-hash 冲突时返回 `None`（不抛异常）
- [ ] `get_next_seq()` 首次调用正确从 SQLite 恢复计数器
- [ ] `tool_output` 存储完整无截断（验证 ≥ 10000 字符的 output 原样存入）

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_insert_session_creates_new` — 新 session_id → `True`
- [ ] `test_insert_session_idempotent` — 相同 session_id 再次 insert → `False`
- [ ] `test_insert_turn_user_for_unknown_session_raises` — 不存在 session_id → `SESSION_NOT_FOUND`
- [ ] `test_tool_output_stored_verbatim` — 10000 字符 output 写入后读出完全一致
- [ ] `test_insert_moment_dedup_returns_none` — 相同 content-hash 第二次 insert → `None`, DB 中只有一行
- [ ] `test_get_unflushed_moments_only_returns_flushed_zero` — flushed=1 的 moment 不出现在结果中
- [ ] `test_get_next_seq_starts_at_one_for_new_session` — 新 session 首次调用 → 1
- [ ] `test_get_next_seq_recovers_from_db` — 模拟 daemon 重启：先插入 3 turns → 新 conn 的 get_next_seq → 返回 4
- [ ] `test_increment_turn_count_updates_buffered_chars` — insert turn 后调用 increment_turn_count(session_id, 100) → sessions 表中 buffered_chars 增加了 100

*意图测试*:
- [ ] `test_crud_functions_are_thin_wrappers` — **意图: 职责分离**。每个 CRUD 函数只做一件事：单条 SQL + commit。不在 CRUD 函数内做字段验证（那是 Pydantic 的职责）、阈值检查（那是 route handler 的职责）、LLM 调用。验证方法：检查每个 CRUD 函数的方法体 ≤ 10 行，不含任何除 sqlite3 外的 import。
- [ ] `test_full_tool_output_preserved_for_llm_extraction` — **意图: 数据完整**。存储完整 tool output 不截断，因为 Phase 2 LLM 需要完整上下文做 moment detection。如果 1a.2 就截断到 250 字符，LLM 从截断内容提取的摘要会丢失关键信息。Phase 2 的 LLM 自己按 `tool_result_max_chars` 提取精华——buffer 层不做任何摘要。

---

### Feature 1a.3: Migration 引擎

**Scenario**: Daemon 启动 Step 4，读取当前 `schema_version`，从未执行的 migration 开始依次运行 SQL 脚本。首次启动（db 文件新建）version=0，全部 migration 顺序执行。后续启动检测到 version 已是最新，跳过所有 migration。

| 属性 | 说明 |
|------|------|
| **理由** | Schema 会演进（Phase 3 加 flush 字段、Phase 5 加 metrics 表），需要版本化管理。幂等是硬性要求——每次启动都跑 migration，不能因为"表已存在"而报错。 |
| **优先级** | P0 — 数据可靠性基础 |
| **依赖** | 1a.1（表结构 DDL）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/sqlite-schema.md` | §4 Migration 策略 | `MIGRATIONS` list，version 顺序执行，`INSERT OR REPLACE INTO schema_version` |
| `03-daemon/sqlite-schema.md` | §4.2 执行逻辑 | `conn.executescript(m.sql)` + `conn.commit()`，`current = row[0] or 0` |
| `03-daemon/sqlite-schema.md` | §4.3 添加新 migration | 在 MIGRATIONS 列表末尾追加，不得修改已有条目 |
| `03-daemon/startup.md` | §1.1 Step 4 | `for migration in MIGRATIONS[current_version:]` → `conn.executescript(migration.sql)` |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Migration SQL 执行失败 | `conn.executescript()` 抛 `sqlite3.OperationalError` | daemon 启动失败，SessionStart hook stdout error hint | `daemon.log` 搜索 `Migration v{N} FAILED` | 查看失败 migration 的 SQL → 手动修复 DB 或回滚 migration → 重启 daemon |
| `schema_version` 被外部修改（跳过 migration） | 实际表结构与 version 不匹配—后续操作报错 | 症状延迟出现：某个端点的 SQL 失败 | `daemon.log` 搜索 `no such column` 或 `no such table` | 删除 daemon.db 重建（`mv daemon.db daemon.db.bak`） |
| 并发 migration（两个 daemon 同时启动） | SQLite file-level lock | 第二个 daemon 启动失败（busy_timeout 5s 后） | `daemon.log` 搜索 `database is locked` | 不应发生——daemon 启动幂等检测防止此情况 |

**Function-Level Steps**（按实现顺序）:

```python
# buffer.py

class Migration:
    """Migration definition."""
    version: int
    description: str
    sql: str

MIGRATIONS: list[Migration] = [
    Migration(version=1, description="Initial schema", sql="""
        CREATE TABLE IF NOT EXISTS schema_version (...);
        CREATE TABLE IF NOT EXISTS sessions (...);
        CREATE TABLE IF NOT EXISTS turns (...);
        CREATE TABLE IF NOT EXISTS moments (...);
        CREATE TABLE IF NOT EXISTS metrics (...);
        -- + 5 indexes
    """),
]

def get_schema_version(conn: sqlite3.Connection) -> int:
    """SELECT COALESCE(MAX(version), 0) FROM schema_version。
    首次启动（表不存在）时自动创建 schema_version 表后返回 0。
    """

def run_migrations(conn: sqlite3.Connection) -> None:
    """按 version 顺序执行所有未应用的 migration。
    每个 migration 在一个 executescript 中执行——原子。
    完成后 conn.commit()。
    """
```

**交付标准**:

- [ ] 首次启动执行全部 migration → schema_version=1
- [ ] 重复执行不报错（幂等）
- [ ] Migration 执行后四表 + 5 索引存在
- [ ] Migration 失败不继续——daemon 启动中止
- [ ] 不得修改已有 Migration 条目的 SQL

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_run_migrations_creates_all_tables` — fresh DB → run_migrations → 四表存在
- [ ] `test_run_migrations_is_idempotent` — run_migrations → run_migrations again → 无异常
- [ ] `test_run_migrations_sets_schema_version` — run_migrations → schema_version=1
- [ ] `test_run_migrations_skips_applied` — 已有 version=1 → run_migrations → schema_version 仍为 1（不重复执行）

*意图测试*:
- [ ] `test_existing_migration_sql_never_modified` — **意图: 生产兼容**。修改已有 migration 的 SQL 会破坏已部署实例的 schema 一致性。测试方法：MIGRATIONS[0].sql 的 SHA-256 hash 固定，任何修改都会让此测试 FAIL。如果某天确实需要改，说明 schema 变了——应该新增 migration 而非改旧的。
- [ ] `test_migration_failure_stops_daemon_startup` — **意图: 不静默**。Migration 失败意味着 schema 不完整，继续启动会导致奇怪的运行时错误（"no such column"）。daemon 必须在启动阶段报错并退出，让用户立即发现，而不是在运行时随机崩溃。

---

### Feature 1a.4: Content-Hash Dedup 函数

**Scenario**: Insert moment 前，调用 `compute_content_hash(session_id, title, narrative)` 计算 SHA-256。如果 hash 已存在（另一个 moment 有相同 hash），`INSERT OR IGNORE` 静默跳过——这是正常操作（两层去重的第二层），不是错误。

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md Dedup Strategy 第二层：content-hash UNIQUE 约束。Phase 1 的检测窗口可能重叠（同一 turn 被包含在连续两次检测中），Phase 1→Phase 2 也可能重复。content-hash 是最终的防护。 |
| **优先级** | P0 — 数据质量 |
| **依赖** | 1a.1（moments 表 content_hash UNIQUE 约束）、1a.2（insert_moment CRUD）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/sqlite-schema.md` | §5 Content-Hash Dedup | `SHA-256(session_id + \0 + title + \0 + narrative)`，`\0` 分隔防碰撞，`try/except IntegrityError` |
| `03-daemon/sqlite-schema.md` | §2.4 moments 表 | `content_hash TEXT UNIQUE NOT NULL` |
| `01-architecture-overview.md` | §5 硬性约束 #2 | "所有 moment insert 前计算 SHA-256(session_id + title + narrative)" |
| `CLAUDE.md` | Dedup Strategy | "INSERT OR IGNORE" + Phase 2 prompt injection（两层去重） |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| 意外 hash 碰撞（不应发生但可能） | 相同 hash 但不同内容 → 新 moment 被静默丢弃 | 用户可能发现少了一个 moment | `daemon.log` debug level 搜索 `dedup.*skipped` | 无自动恢复。如果频繁发生，检查 hash 算法是否有 bug |
| null byte 导致 hash 不稳定 | session_id/title/narrative 中出现 `\0` → 实际不应发生 | 同上 | 同上 | 输入 sanitization：在 compute_content_hash 入口 strip null bytes |

**Function-Level Steps**（按实现顺序）:

```python
# buffer.py
import hashlib

def compute_content_hash(session_id: str, title: str, narrative: str) -> str:
    """计算 SHA-256(session_id + \0 + title + \0 + narrative)。
    返回 hex digest string。
    \0 分隔符确保字段边界不会产生意外碰撞。
    """
```

**交付标准**:

- [ ] 相同输入 → 相同 hash
- [ ] 不同 title → 不同 hash
- [ ] 不同 session_id + 相同 title/narrative → 不同 hash
- [ ] `\0` 分隔符在字段中出现时（边缘情况）不破坏 hash 唯一性

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_content_hash_deterministic` — 相同参数两次调用 → 相同 hash
- [ ] `test_content_hash_different_title_produces_different_hash` — 不同 title → 不同 hash
- [ ] `test_content_hash_different_session_id_produces_different_hash` — 跨 session 相同内容 → 仍不同 hash
- [ ] `test_content_hash_integration_with_insert_moment` — compute → insert → 相同 hash 再次 insert → 返回 None（整合验证）

*意图测试*:
- [ ] `test_dedup_is_silent_not_error` — **意图: dedup 是正常行为，非异常**。`INSERT OR IGNORE` 或 `try/except IntegrityError`，不能抛异常或 log warning。如果 dedup 产生 error log，Phase 1 检测窗口重叠时会产生大量噪音，掩盖真正的错误。验证方法：连续 10 次 insert 相同 moment，断言 log 中无 ERROR/WARNING 级别记录。
- [ ] `test_null_byte_delimiter_prevents_field_splicing_attacks` — **意图: hash 安全性**。`\0` 分隔符防止 `("ab", "c", "d")` 和 `("a", "bc", "d")` 产生相同 hash。这是内容寻址的基础安全属性。验证方法：构造两组 session_id+title+narrative 组合，它们在无分隔符拼接时相同但有分隔符后不同。

---

### Feature 1a.5: Config 系统集成

**Scenario**: Daemon 启动 Step 1 加载配置。`config.json` 中的 `daemon.db_path` 读取 `~/.bible-cc/daemon.db`（`~` 展开）和 `daemon.port` 读取 `9777`。Env var `BIBLE_CC_DB_PATH` 和 `BIBLE_CC_DAEMON_PORT` 可覆盖。非法值（port < 1024 或 > 65535）自动回退到 default。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 0 已实现 config 系统基础。Phase 1 需要集成 `daemon.db_path` 和 `daemon.port` 的读取——之前可能未在 daemon 启动路径中使用。 |
| **优先级** | P1 — 基础建设 |
| **依赖** | Phase 0 config.py |

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `04-config/schema.md` | §2.2 daemon | `port: int = 9777`, `port_auto_fallback: bool = False`, `db_path: str = "~/.bible-cc/daemon.db"` |
| `04-config/schema.md` | §4 加载器 | `load_config()` — defaults → file overlay → env var overlay 三层优先级 |
| `03-daemon/startup.md` | §1.1 Step 1 | `config = load_config()` |
| `03-daemon/startup.md` | §3 DB 路径 | `~` 展开为 `Path.home()`，父目录 daemon startup Step 3 自动创建 |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| config.json 损坏（非法 JSON） | `json.JSONDecodeError` → Pydantic 使用 defaults | daemon 使用默认值启动，`/bible-cc:status` 显示 config 来源 | `daemon.log` 搜索 `config file parse error.*falling back` | 修复或删除 `~/.bible-cc/config.json` → 重启 daemon |
| port 值非法（< 1024 或 > 65535） | Pydantic validator → 回退 9777 | daemon 在 9777 启动，不是用户期望的端口 | `daemon.log` 搜索 `port.*invalid.*fallback to 9777` | 修改 config.json 中 daemon.port 为合法值 |
| db_path 指向不可写路径 | `sqlite3.connect()` 失败（权限） | daemon 启动失败 | `daemon.log` 搜索 `cannot open database` | 修改 config 或 env var → 重启 daemon |

**Function-Level Steps**（按实现顺序）:

```python
# config.py（Phase 0 已有，本 feature 验证/补充）
# 无需新增函数——验证现有 load_config() 正确读取 daemon 相关配置即可

# 测试中验证：
# load_config() 返回的 AppConfig.daemon.port == 9777
# load_config() 返回的 AppConfig.daemon.db_path 中 ~ 已展开
```

**交付标准**:

- [ ] `load_config()` 读取 `daemon.port`（default 9777, env override `BIBLE_CC_DAEMON_PORT`）
- [ ] `load_config()` 读取 `daemon.db_path`（default `~/.bible-cc/daemon.db`, `~` 展开为 `Path.home()`）
- [ ] port 非法值自动回退到 9777
- [ ] config.json 损坏时使用 defaults 启动（不阻止 daemon）

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_daemon_port_default` — 无 config 文件 → `daemon.port == 9777`
- [ ] `test_daemon_port_env_override` — `BIBLE_CC_DAEMON_PORT=9999` → `daemon.port == 9999`
- [ ] `test_daemon_port_invalid_fallback` — port=80 → 回退 9777
- [ ] `test_db_path_expands_tilde` — `db_path="~/test.db"` → 展开为 `Path.home() / "test.db"`

*意图测试*:
- [ ] `test_corrupt_config_does_not_block_daemon_startup` — **意图: 防御性**。config.json 是本地的、用户可编辑的文件。损坏不应导致 daemon 完全不可用。使用 defaults 启动 + 输出 warning，让用户知道配置有问题但 daemon 仍可用。
- [ ] `test_db_path_not_writable_detected_early` — **意图: 尽早失败**。如果 db_path 指向的目录不可写，应在启动阶段（Step 3）就发现并报错——不是在运行中某个 turn 写入失败时才暴露。用户看到的是清晰的启动失败原因，而不是"中途突然不工作了"。

---

