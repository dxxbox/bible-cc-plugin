# 03-daemon/sqlite-schema.md — SQLite Schema（L3）

> 完整表结构、索引、PRAGMA 设置、content-hash dedup 约束、migration 策略。实现时直接照此写 DDL。

---

## 1. PRAGMA

daemon 启动时（在任何读写之前）执行：

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
```

- **WAL**：写操作不阻塞读操作。多 session 并发写时的 `SQLITE_BUSY` 变为 block-wait。
- **busy_timeout=5000**：写锁等待最多 5 秒。超时后 SQLite 返回 `SQLITE_BUSY`，应用层需 retry。
- **无需连接池**：SQLite 单 writer 模型 + WAL 已足够应对 daemon 的并发量（hook HTTP calls 间隔 ≥ 1s）。
- 连接初始化时设 `conn.row_factory = sqlite3.Row`，所有查询返回 dict-like Row 对象。

---

## 2. 表结构

### 2.1 `schema_version` — Migration 版本追踪

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT
);
```

- 单行表，记录当前 schema 版本号。
- 首次启动时 version = 0（表不存在），依次执行所有 migration。
- Migration 脚本执行后 `INSERT OR REPLACE` 更新版本号。

### 2.2 `sessions` — 会话记录

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'completed'
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at     TEXT,
    turn_count    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
```

- `session_id`：Claude Code 提供的 `$CLAUDE_SESSION_ID`。
- `status`：`active` → session 进行中；`completed` → session 已结束（Stop hook 已触发）。
- `closed_at`：session end 时写入。
- `turn_count`：每次 `/turn/*` 写入时 `UPDATE sessions SET turn_count = turn_count + 1`。

### 2.3 `turns` — 对话 turn 缓冲

```sql
CREATE TABLE IF NOT EXISTS turns (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL REFERENCES sessions(session_id),
    seq            INTEGER NOT NULL,             -- per-session 序号，非全局
    role           TEXT NOT NULL,                -- 'user' | 'tool'
    content        TEXT,                         -- user message（role='user' 时）
    tool_name      TEXT,                         -- tool 名称（role='tool' 时）
    tool_arguments TEXT,                         -- tool 参数 JSON（role='tool' 时）
    tool_output    TEXT,                         -- 完整 tool output（role='tool' 时，无截断）
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_turns_session_seq ON turns(session_id, seq);
```

- **seq 是 per-session 自增**，非全局自增。daemon 端维护内存计数器（`session_seq: dict[str, int]`），每次 insert 取当前值并 +1。daemon 启动时从 SQLite 恢复所有活跃 session 的计数器：`SELECT session_id, MAX(seq) FROM turns GROUP BY session_id`（见 [`startup.md`](startup.md) Step 5）。
- `tool_output` 存储完整输出——不在此层截断。LLM 在 detection 阶段提取 `≤tool_result_max_chars` 摘要。
- `tool_arguments` 存 JSON string。
- UNIQUE(session_id, seq) 防止并发 insert 冲突。

### 2.4 `moments` — Key Moment 存储

```sql
CREATE TABLE IF NOT EXISTS moments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL REFERENCES sessions(session_id),
    moment_type       TEXT NOT NULL,             -- 'session_start' | 'decision' | 'accomplishment'
    title             TEXT NOT NULL,
    narrative         TEXT NOT NULL,
    tool_summary      TEXT,                      -- 关联 tool output 的 LLM 提取摘要（可为空）
    content_hash      TEXT UNIQUE NOT NULL,      -- SHA-256(session_id + title + narrative)
    turn_range_start  INTEGER,                  -- 触发 moment 的起始 turn seq
    turn_range_end    INTEGER,                  -- 触发 moment 的结束 turn seq
    phase             TEXT NOT NULL DEFAULT '1', -- '1' = mid-session, '2' = retrospective
    flushed           INTEGER NOT NULL DEFAULT 0, -- 0=pending, 1=sent, 2=confirmed, -1=failed
    import_task_id    TEXT,                      -- BiBLE async import task_id
    flushed_at        TEXT,                      -- flush timestamp
    retry_count       INTEGER DEFAULT 0,         -- 连续 flush 失败计数
    detected_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_moments_session ON moments(session_id);
CREATE INDEX IF NOT EXISTS idx_moments_flushed ON moments(flushed);
CREATE INDEX IF NOT EXISTS idx_moments_content_hash ON moments(content_hash);
```

- **content_hash UNIQUE** 是核心去重约束。所有 insert 前先计算 `SHA-256(session_id + title + narrative)`。
- **flushed 状态机**：
  - `0` = pending（未 flush）
  - `1` = sent（已发送到 BiBLE，轮询中）
  - `2` = confirmed（BiBLE import 完成）
  - `-1` = failed（需要 retry）
- **phase**：`1` = Phase 1 mid-session detection，`2` = Phase 2 retrospective detection。
- `retry_count`：仅对 failed 的 moment 递增。3 次后产出 warning hint。

---

## 3. 索引策略

| 索引 | 用途 | 查询示例 |
|------|------|---------|
| `idx_sessions_status` | Crash recovery：找出所有 active session | `WHERE status='active'` |
| `idx_turns_session_seq` | Phase 1 检测：取 session 最近 2-3 turns | `WHERE session_id=? ORDER BY seq DESC LIMIT 3` |
| `idx_moments_session` | `/daemon/moments`：列出 session pending moments | `WHERE session_id=? AND flushed=0` |
| `idx_moments_flushed` | `/bible-cc:push-all`：全量 pending | `WHERE flushed IN (0, -1)` |
| `idx_moments_content_hash` | Dedup 查询（可选——UNIQUE 约束本身使用索引） | 内部 |

不需要在 `turns.session_id` 和 `moments.session_id` 上额外建索引——`UNIQUE` 和 `REFERENCES` 约束已提供索引。

---

## 4. Migration 策略

### 4.1 版本数组

```python
MIGRATIONS = [
    Migration(version=1, description="Initial schema", sql="""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at TEXT,
            turn_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_name TEXT,
            tool_arguments TEXT,
            tool_output TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(session_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_turns_session_seq ON turns(session_id, seq);
        CREATE TABLE IF NOT EXISTS moments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            moment_type TEXT NOT NULL,
            title TEXT NOT NULL,
            narrative TEXT NOT NULL,
            tool_summary TEXT,
            content_hash TEXT UNIQUE NOT NULL,
            turn_range_start INTEGER,
            turn_range_end INTEGER,
            phase TEXT NOT NULL DEFAULT '1',
            flushed INTEGER NOT NULL DEFAULT 0,
            import_task_id TEXT,
            flushed_at TEXT,
            retry_count INTEGER DEFAULT 0,
            detected_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_moments_session ON moments(session_id);
        CREATE INDEX IF NOT EXISTS idx_moments_flushed ON moments(flushed);
        CREATE INDEX IF NOT EXISTS idx_moments_content_hash ON moments(content_hash);
    """),
    # Future migrations added here:
    # Migration(version=2, description="Add X column", sql="ALTER TABLE ..."),
]
```

### 4.2 执行逻辑

```python
def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now')),
        description TEXT
    )""")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = row[0] or 0

    for m in MIGRATIONS:
        if m.version > current:
            conn.executescript(m.sql)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version(version, description) VALUES(?, ?)",
                (m.version, m.description)
            )
    conn.commit()
```

- **幂等**：重复执行相同的 migration 不产生错误（`CREATE TABLE IF NOT EXISTS`）。
- **不可逆**：无 down migration。Schema 变更只前进不回退。
- **首次启动**（db 文件不存在）：`sqlite3.connect()` 自动创建文件，`current = 0`，所有 migration 顺序执行。

### 4.3 添加新 migration

1. 在 `MIGRATIONS` 列表末尾追加新 `Migration` 对象。
2. SQL 必须使用 `IF NOT EXISTS` / `IF EXISTS` 确保幂等。
3. 不得修改已有 migration 条目的 SQL——那会破坏已部署实例的一致性。

---

## 5. Content-Hash Dedup 实现

```python
import hashlib

def compute_content_hash(session_id: str, title: str, narrative: str) -> str:
    data = f"{session_id}\0{title}\0{narrative}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def insert_moment(conn, session_id, moment_type, title, narrative, ...) -> int | None:
    content_hash = compute_content_hash(session_id, title, narrative)
    try:
        conn.execute(
            """INSERT INTO moments (session_id, moment_type, title, narrative, content_hash, ...)
               VALUES (?, ?, ?, ?, ?, ...)""",
            (session_id, moment_type, title, narrative, content_hash, ...)
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.IntegrityError:
        # UNIQUE constraint on content_hash → duplicate, silently skip
        return None
```

- 用 `\0`（null byte）分隔字段——session_id、title、narrative 中的自然文本不含 null byte，避免 hash 碰撞。
- `INSERT OR IGNORE` 同理，但显式的 `try/except IntegrityError` 更清晰地表达"这是 expected path"。
- 不报错，不 log（dedup 是正常操作，不是异常）。

---

## 6. 并发策略

| 场景 | 并发量 | 策略 |
|------|--------|------|
| 单 session 多 turn 写入 | 顺序（hook 串行触发） | 无竞争 |
| 多 session 同时写入 | 2-3 session 并发 | WAL mode + busy_timeout |
| Crash recovery 慢路写 + 新 session 写入 | 可能重叠 | WAL mode — 慢路写 moments 表，新 session 写 turns 表，不冲突 |

不引入连接池或锁。WAL + busy_timeout 足够。

---

## 7. 参考文档

- [`startup.md`](startup.md) — Step 3 WAL PRAGMA，Step 4 migration 执行
- [`../../02-interfaces.md`](../../02-interfaces.md) — turn/user、turn/tool 端点的请求字段
- [`../../04-config/schema.md`](../../04-config/schema.md) — `daemon.db_path`
- [`../05-capture/flush.md`](../05-capture/flush.md) — flush 状态管理、moments 表 flush 字段定义
- [`../../CLAUDE.md`](../../../CLAUDE.md) — Dedup Strategy（两层去重）
