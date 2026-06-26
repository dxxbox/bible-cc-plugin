"""SQLite buffer — schema, CRUD, migration, content-hash dedup.

Phase 1a: complete local data layer for the bible-cc daemon.
Design: 03-daemon/sqlite-schema.md (L3), 03-daemon/startup.md (L3).

All writes go through functions in this module. No LLM calls, no BiBLE calls.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from bible_cc_plugin.logging_config import get_logger

_logger = get_logger("buffer")

# ── per-session turn sequence counters (in-memory, recovered from DB on start) ─
session_seq: dict[str, int] = {}


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.1: SQLite Schema + PRAGMA
# ══════════════════════════════════════════════════════════════════════════════


def open_database(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database at *db_path*.

    Creates the parent directory if it does not exist.  Sets
    ``row_factory = sqlite3.Row`` so queries return dict-like rows.

    Does **not** apply PRAGMA — the caller must call :func:`apply_pragmas`
    before any other operation.
    """
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    _logger.info("opening SQLite database at %s", path)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    # check_same_thread=False is safe because the daemon runs with a single
    # uvicorn worker in production (03-daemon/http-api.md §8).  It is
    # required for tests where FastAPI TestClient runs in a different thread.
    conn.row_factory = sqlite3.Row
    return conn


def apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply mandatory PRAGMA settings.

    Must be called immediately after :func:`open_database` and **before**
    any CREATE TABLE or INSERT.  Order is non-negotiable:

    1. ``PRAGMA journal_mode=WAL;``
    2. ``PRAGMA busy_timeout=5000;``

    Raises :class:`RuntimeError` if either PRAGMA does not produce the
    expected result.
    """
    conn.execute("PRAGMA journal_mode=WAL;")
    mode_row = conn.execute("PRAGMA journal_mode;").fetchone()
    mode = mode_row[0] if mode_row else "unknown"
    _logger.info("PRAGMA journal_mode → %s", mode)
    if mode != "wal":
        raise RuntimeError(f"PRAGMA journal_mode=WAL failed — returned {mode!r} instead of 'wal'")

    conn.execute("PRAGMA busy_timeout=5000;")
    timeout_row = conn.execute("PRAGMA busy_timeout;").fetchone()
    timeout = timeout_row[0] if timeout_row else -1
    _logger.info("PRAGMA busy_timeout → %s", timeout)
    if timeout != 5000:
        raise RuntimeError(
            f"PRAGMA busy_timeout=5000 failed — returned {timeout!r} instead of 5000"
        )


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes (idempotent — uses IF NOT EXISTS)."""
    _logger.info("creating tables (idempotent)")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (datetime('now')),
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_id     TEXT PRIMARY KEY,
            status         TEXT NOT NULL DEFAULT 'active',
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at      TEXT,
            turn_count     INTEGER NOT NULL DEFAULT 0,
            buffered_chars INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

        CREATE TABLE IF NOT EXISTS turns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL REFERENCES sessions(session_id),
            seq             INTEGER NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT,
            tool_name       TEXT,
            tool_arguments  TEXT,
            tool_output     TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(session_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_turns_session_seq ON turns(session_id, seq);

        CREATE TABLE IF NOT EXISTS moments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id       TEXT NOT NULL REFERENCES sessions(session_id),
            moment_type      TEXT NOT NULL,
            title            TEXT NOT NULL,
            narrative        TEXT NOT NULL,
            tool_summary     TEXT,
            content_hash     TEXT UNIQUE NOT NULL,
            turn_range_start INTEGER,
            turn_range_end   INTEGER,
            phase            TEXT NOT NULL DEFAULT '1',
            flushed          INTEGER NOT NULL DEFAULT 0,
            import_task_id   TEXT,
            flushed_at       TEXT,
            retry_count      INTEGER DEFAULT 0,
            detected_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_moments_session ON moments(session_id);
        CREATE INDEX IF NOT EXISTS idx_moments_flushed ON moments(flushed);
        CREATE INDEX IF NOT EXISTS idx_moments_content_hash ON moments(content_hash);

        CREATE TABLE IF NOT EXISTS metrics (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            metric_name  TEXT NOT NULL,
            metric_value REAL NOT NULL,
            recorded_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    _logger.info("tables created successfully")


def verify_integrity(conn: sqlite3.Connection) -> str:
    """Run ``PRAGMA integrity_check`` and return the result string.

    Typical return values:
    - ``"ok"`` — database is healthy
    - any other string — description of the corruption
    """
    row = conn.execute("PRAGMA integrity_check;").fetchone()
    result = row[0] if row else "no result"
    if result != "ok":
        _logger.error("SQLite integrity check FAILED: %s", result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.2: CRUD Layer
# ══════════════════════════════════════════════════════════════════════════════

# -- Session ----------------------------------------------------------------


def insert_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """Create a new session row.  Returns ``True`` for a new session,
    ``False`` if a session with this id already exists."""
    try:
        conn.execute("INSERT INTO sessions (session_id) VALUES (?)", (session_id,))
        conn.commit()
        _logger.debug("session created: %s", session_id)
        return True
    except sqlite3.IntegrityError:
        _logger.debug("session already exists: %s", session_id)
        return False


def reactivate_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """Reactivate a completed session so turns can be recorded again.

    Returns True if the row was updated (status was 'completed'),
    False if the session was already active or doesn't exist.
    """
    cur = conn.execute(
        "UPDATE sessions SET status='active', closed_at=NULL "
        "WHERE session_id=? AND status='completed'",
        (session_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_session_completed(conn: sqlite3.Connection, session_id: str) -> None:
    """Mark a session as completed.  Phase 1: only updates status + closed_at."""
    conn.execute(
        "UPDATE sessions SET status='completed', closed_at=datetime('now') WHERE session_id=?",
        (session_id,),
    )
    conn.commit()
    _logger.info("session completed: %s", session_id)


def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Return the session row (as a dict-like Row) or None."""
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return row


def count_active_sessions(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='active'").fetchone()
    return row[0] if row else 0


def count_completed_sessions(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='completed'").fetchone()
    return row[0] if row else 0


# -- Turn -------------------------------------------------------------------


def get_next_seq(conn: sqlite3.Connection, session_id: str) -> int:
    """Return the next per-session turn sequence number.

    On first call for a session, recovers the counter from SQLite
    (``MAX(seq)``).  Subsequent calls increment the in-memory counter.
    """
    global session_seq
    if session_id not in session_seq:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM turns WHERE session_id=?",
            (session_id,),
        ).fetchone()
        current = row[0] if row else 0
        session_seq[session_id] = current
        _logger.debug("seq counter recovered for %s: %d", session_id, current)
    session_seq[session_id] += 1
    return session_seq[session_id]


def _require_active_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Raise if *session_id* does not exist or is not active."""
    row = conn.execute("SELECT status FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        raise ValueError(f"session not found: {session_id}")
    if row["status"] != "active":
        raise ValueError(f"session {session_id} is {row['status']}, expected 'active'")


def get_all_session_turns(
    conn: sqlite3.Connection, session_id: str
) -> list[dict]:
    """Return ALL turns for *session_id* in chronological order.

    Used by Phase 2 retrospective detection.
    Each dict has keys: role, content, tool_name, tool_output, seq.
    Returns empty list for unknown sessions.
    """
    rows = conn.execute(
        "SELECT role, content, tool_name, tool_output, seq "
        "FROM turns WHERE session_id=? ORDER BY seq ASC",
        (session_id,),
    ).fetchall()
    result = [dict(r) for r in rows]
    _logger.debug(
        "get_all_session_turns: session=%s → %d turns",
        session_id[:8],
        len(result),
    )
    return result


def get_recent_turns(
    conn: sqlite3.Connection, session_id: str, limit: int = 3
) -> list[dict]:
    """Return the most recent *limit* turns for *session_id* (descending seq).

    Used by the Phase 1 detection pipeline to build the LLM prompt.
    Each returned dict has keys: role, content, tool_name, tool_output, seq.
    """
    rows = conn.execute(
        "SELECT role, content, tool_name, tool_output, seq "
        "FROM turns WHERE session_id=? "
        "ORDER BY seq DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    result = [dict(r) for r in rows]  # most recent first (DESC)
    _logger.debug(
        "get_recent_turns: session=%s limit=%d → %d turns",
        session_id[:8],
        limit,
        len(result),
    )
    return result


def get_phase1_detection_window(
    conn: sqlite3.Connection,
    session_id: str,
    limit: int = 8,
    max_seq: int | None = None,
    include_previous_user: bool = False,
) -> list[dict]:
    """Return a chronological Phase 1 detection window anchored on latest user turn.

    The mid-session detector needs the user's most recent intent/decision plus
    the tool activity that followed it. A plain "last N turns" window can be
    filled entirely by assistant tool calls, causing the detector to miss
    user-confirmed decisions.

    ``max_seq`` freezes the window at queue time so a lagging background worker
    does not drift into a later user prompt.

    When a user turn itself triggers the threshold, ``include_previous_user``
    keeps the prior user-anchored tool window in scope while still including
    the new prompt at ``max_seq``.
    """
    if max_seq is None:
        latest_user = conn.execute(
            "SELECT role, content, tool_name, tool_output, seq "
            "FROM turns WHERE session_id=? AND role='user' "
            "ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    else:
        latest_user = conn.execute(
            "SELECT role, content, tool_name, tool_output, seq "
            "FROM turns WHERE session_id=? AND role='user' AND seq<=? "
            "ORDER BY seq DESC LIMIT 1",
            (session_id, max_seq),
        ).fetchone()
        if include_previous_user and latest_user is not None and latest_user["seq"] == max_seq:
            previous_user = conn.execute(
                "SELECT role, content, tool_name, tool_output, seq "
                "FROM turns WHERE session_id=? AND role='user' AND seq<? "
                "ORDER BY seq DESC LIMIT 1",
                (session_id, max_seq),
            ).fetchone()
            if previous_user is not None:
                latest_user = previous_user
    if latest_user is None:
        if max_seq is None:
            return list(reversed(get_recent_turns(conn, session_id, limit=limit)))
        rows = conn.execute(
            "SELECT role, content, tool_name, tool_output, seq "
            "FROM turns WHERE session_id=? AND seq<=? "
            "ORDER BY seq DESC LIMIT ?",
            (session_id, max_seq, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    after_limit = max(limit - 1, 0)
    if max_seq is None:
        rows_after = conn.execute(
            "SELECT role, content, tool_name, tool_output, seq "
            "FROM turns WHERE session_id=? AND seq > ? "
            "ORDER BY seq DESC LIMIT ?",
            (session_id, latest_user["seq"], after_limit),
        ).fetchall()
    else:
        rows_after = conn.execute(
            "SELECT role, content, tool_name, tool_output, seq "
            "FROM turns WHERE session_id=? AND seq > ? AND seq<=? "
            "ORDER BY seq DESC LIMIT ?",
            (session_id, latest_user["seq"], max_seq, after_limit),
        ).fetchall()
    result = [dict(latest_user)] + [dict(r) for r in reversed(rows_after)]
    _logger.debug(
        "get_phase1_detection_window: session=%s limit=%d → %d turns",
        session_id[:8],
        limit,
        len(result),
    )
    return result


def insert_turn_user(conn: sqlite3.Connection, session_id: str, message: str) -> int:
    """Insert a user turn.  Returns the turn seq number."""
    _require_active_session(conn, session_id)
    seq = get_next_seq(conn, session_id)
    conn.execute(
        "INSERT INTO turns (session_id, seq, role, content) VALUES (?,?,'user',?)",
        (session_id, seq, message),
    )
    conn.commit()
    _logger.debug("turn/user %s seq=%d", session_id, seq)
    return seq


def insert_turn_tool(
    conn: sqlite3.Connection,
    session_id: str,
    tool_name: str,
    arguments: dict,
    output: str,
) -> int:
    """Insert a tool turn.  Returns the turn seq number.

    *output* is stored **verbatim** — no mechanical truncation.
    The LLM detector in Phase 2 will extract a summary respecting
    ``capture.tool_result_max_chars``.
    """
    import json as _json

    _require_active_session(conn, session_id)
    seq = get_next_seq(conn, session_id)
    conn.execute(
        "INSERT INTO turns (session_id, seq, role, tool_name, tool_arguments, tool_output) "
        "VALUES (?,?,'assistant',?,?,?)",
        (session_id, seq, tool_name, _json.dumps(arguments), output),
    )
    conn.commit()
    _logger.debug("turn/tool %s seq=%d tool=%s", session_id, seq, tool_name)
    return seq


def increment_turn_count(conn: sqlite3.Connection, session_id: str, chars: int) -> None:
    """Increment the turn counter and buffered character count for a session."""
    conn.execute(
        "UPDATE sessions SET turn_count = turn_count + 1, "
        "buffered_chars = buffered_chars + ? WHERE session_id=?",
        (chars, session_id),
    )
    conn.commit()


# -- Moment -----------------------------------------------------------------


def insert_moment(
    conn: sqlite3.Connection, session_id: str, moment_type: str, title: str,
    narrative: str, content_hash: str, phase: str = "1",
    turn_range_start: int | None = None, turn_range_end: int | None = None,
) -> int | None:
    """Insert a key moment.  Returns the ``id`` or ``None`` if a moment with
    the same *content_hash* already exists (dedup — normal operation)."""
    try:
        _insert_moment_row(
            conn,
            (
                session_id,
                moment_type,
                title,
                narrative,
                content_hash,
                phase,
                turn_range_start,
                turn_range_end,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        _logger.debug(
            "moment inserted id=%d type=%s hash=%s", row[0], moment_type, content_hash[:12]
        )
        return row[0]
    except sqlite3.IntegrityError:
        # content-hash UNIQUE constraint — dedup, not an error
        _logger.debug("moment dedup skipped hash=%s", content_hash[:12])
        return None


def _insert_moment_row(conn: sqlite3.Connection, values: tuple) -> None:
    conn.execute(
        "INSERT INTO moments "
        "(session_id, moment_type, title, narrative, content_hash, phase, "
        "turn_range_start, turn_range_end) "
        "VALUES (?,?,?,?,?,?,?,?)",
        values,
    )


def get_moment_by_anchor(
    conn: sqlite3.Connection,
    session_id: str,
    moment_type: str,
    turn_range_start: int,
) -> dict | None:
    """Return a moment for the same user-turn anchor, including flushed rows."""
    row = conn.execute(
        "SELECT * FROM moments "
        "WHERE session_id=? AND moment_type=? AND turn_range_start=? "
        "ORDER BY id ASC LIMIT 1",
        (session_id, moment_type, turn_range_start),
    ).fetchone()
    return dict(row) if row is not None else None


def update_pending_moment_from_detection(
    conn: sqlite3.Connection,
    moment_id: int,
    title: str,
    narrative: str,
    content_hash: str,
    turn_range_end: int | None,
) -> bool:
    """Update a pending anchored moment with a later detection result."""
    try:
        cur = conn.execute(
            "UPDATE moments SET title=?, narrative=?, content_hash=?, turn_range_end=? "
            "WHERE id=? AND flushed=0",
            (title, narrative, content_hash, turn_range_end, moment_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False


def update_moment(
    conn: sqlite3.Connection,
    moment_id: int,
    title: str,
    narrative: str,
    content_hash: str,
) -> bool:
    """Update a moment's title, narrative, and content_hash.  Only allowed for flushed=0.

    Returns True if the row was updated, False if the moment does not exist,
    is already flushed, or the new content_hash collides with an existing moment
    (UNIQUE constraint — TOCTOU race or duplicate content).
    """
    try:
        cur = conn.execute(
            "UPDATE moments SET title=?, narrative=?, content_hash=? "
            "WHERE id=? AND flushed=0",
            (title, narrative, content_hash, moment_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False


def delete_moment(conn: sqlite3.Connection, moment_id: int) -> bool:
    """Delete a moment.  Only allowed for flushed=0.

    Returns True if the row was deleted, False otherwise.
    """
    cur = conn.execute(
        "DELETE FROM moments WHERE id=? AND flushed=0",
        (moment_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def get_unflushed_moments(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Return all pending (flushed=0) moments for a session, newest first."""
    rows = conn.execute(
        "SELECT * FROM moments WHERE session_id=? AND flushed=0 ORDER BY detected_at DESC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_moments_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Return ALL moments for a session (including flushed), for debug/introspection."""
    rows = conn.execute(
        "SELECT * FROM moments WHERE session_id=? ORDER BY detected_at",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_pending_moments(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM moments WHERE flushed=0").fetchone()
    return row[0] if row else 0


def count_total_turns(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
    return row[0] if row else 0


# -- Recovery (Phase 1b) ------------------------------------------------------


def get_recovery(conn: sqlite3.Connection, current_session_id: str) -> dict | None:
    """Scan for unclosed sessions and collect recovery data.

    Fast path (sync) — only reads SQLite, no LLM calls.
    Returns ``None`` if no unclosed sessions are found (other than *current_session_id*).
    """
    unclosed = conn.execute(
        "SELECT session_id FROM sessions WHERE status='active' AND session_id != ?",
        (current_session_id,),
    ).fetchall()
    if not unclosed:
        return None

    unclosed_ids = [r["session_id"] for r in unclosed]
    placeholders = ",".join("?" for _ in unclosed_ids)

    # Read moments for all unclosed sessions
    recovery_moments = [
        dict(r)
        for r in conn.execute(
            f"SELECT * FROM moments WHERE session_id IN ({placeholders}) "
            f"AND flushed IN (0, -1) ORDER BY detected_at",
            unclosed_ids,
        ).fetchall()
    ]

    # Read recent turns for each unclosed session (up to 30 each)
    recovery_turns: list[dict] = []
    for sid in unclosed_ids:
        rows = conn.execute(
            "SELECT role, content, tool_name, tool_output FROM turns "
            "WHERE session_id=? ORDER BY seq DESC LIMIT 30",
            (sid,),
        ).fetchall()
        recovery_turns.extend(dict(r) for r in reversed(rows))

    moments_recovered = len(recovery_moments)

    _logger.info(
        "crash recovery scan: %d unclosed sessions, %d moments, %d turns",
        len(unclosed_ids),
        moments_recovered,
        len(recovery_turns),
    )
    return {
        "unclosed_sessions_found": len(unclosed_ids),
        "moments_recovered": moments_recovered,
        "_moments": recovery_moments,
        "_turns": recovery_turns,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.3: Migration Engine
# ══════════════════════════════════════════════════════════════════════════════


class Migration:
    """A single schema migration."""

    __slots__ = ("version", "description", "sql")

    def __init__(self, version: int, description: str, sql: str) -> None:
        self.version = version
        self.description = description
        self.sql = sql


# v1: Initial schema — all four tables + indexes
_INITIAL_SCHEMA = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at      TEXT,
    turn_count     INTEGER NOT NULL DEFAULT 0,
    buffered_chars INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT,
    tool_name       TEXT,
    tool_arguments  TEXT,
    tool_output     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_turns_session_seq ON turns(session_id, seq);

CREATE TABLE IF NOT EXISTS moments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL REFERENCES sessions(session_id),
    moment_type      TEXT NOT NULL,
    title            TEXT NOT NULL,
    narrative        TEXT NOT NULL,
    tool_summary     TEXT,
    content_hash     TEXT UNIQUE NOT NULL,
    turn_range_start INTEGER,
    turn_range_end   INTEGER,
    phase            TEXT NOT NULL DEFAULT '1',
    flushed          INTEGER NOT NULL DEFAULT 0,
    import_task_id   TEXT,
    flushed_at       TEXT,
    retry_count      INTEGER DEFAULT 0,
    detected_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_moments_session ON moments(session_id);
CREATE INDEX IF NOT EXISTS idx_moments_flushed ON moments(flushed);
CREATE INDEX IF NOT EXISTS idx_moments_content_hash ON moments(content_hash);

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    metric_name  TEXT NOT NULL,
    metric_value REAL NOT NULL,
    recorded_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

MIGRATIONS: list[Migration] = [
    Migration(version=1, description="Initial schema", sql=_INITIAL_SCHEMA),
]


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version (0 if no migrations applied yet)."""
    # schema_version table might not exist yet on a brand-new DB
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version     INTEGER PRIMARY KEY,"
        "  applied_at  TEXT NOT NULL DEFAULT (datetime('now')),"
        "  description TEXT"
        ")"
    )
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    return row[0] if row else 0


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any pending migrations in version order.

    Each migration runs inside ``executescript`` (atomic).  Already-applied
    migrations are skipped.  Idempotent — safe to call on every daemon start.
    """
    current = get_schema_version(conn)
    _logger.info("current schema version: %d", current)

    for m in MIGRATIONS:
        if m.version > current:
            _logger.info("applying migration v%d: %s", m.version, m.description)
            conn.executescript(m.sql)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version, description) VALUES (?, ?)",
                (m.version, m.description),
            )
            conn.commit()
            _logger.info("migration v%d applied successfully", m.version)


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.4: Content-Hash Dedup
# ══════════════════════════════════════════════════════════════════════════════


def compute_content_hash(session_id: str, title: str, narrative: str) -> str:
    """Return the SHA-256 hex digest of ``session_id\\0title\\0narrative``.

    The ``\\0`` (null byte) delimiter prevents accidental collisions when
    field boundaries shift (e.g. ``("ab","c","d")`` vs ``("a","bc","d")``).
    """
    data = f"{session_id}\0{title}\0{narrative}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
