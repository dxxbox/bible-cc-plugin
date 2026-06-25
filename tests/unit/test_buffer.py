"""Unit tests for buffer.py — SQLite schema, CRUD, migration, content-hash.

Phase 1a — all tests [Unit] [Pre]. Uses tmp_path SQLite, no external processes.

Coverage: Feature 1a.1 (schema+PRAGMA), 1a.2 (CRUD), 1a.3 (migration),
         1a.4 (content-hash).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

# ── helpers ────────────────────────────────────────────────────────────────


def _fresh_conn(path: Path) -> sqlite3.Connection:
    """Create a new SQLite connection at path without PRAGMA applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.1: SQLite Schema + PRAGMA
# ══════════════════════════════════════════════════════════════════════════════


class TestOpenDatabase:
    """open_database() — creates file + parent dir, sets row_factory."""

    def test_creates_file_and_parent_dir(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import open_database

        db = tmp_path / "sub" / "test.db"
        conn = open_database(str(db))

        assert db.exists()
        assert conn.row_factory == sqlite3.Row
        conn.close()

    def test_opens_existing_db_without_error(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import open_database

        db = tmp_path / "test.db"
        conn1 = open_database(str(db))
        conn1.close()
        conn2 = open_database(str(db))
        assert db.exists()
        conn2.close()


class TestApplyPragmas:
    """apply_pragmas() — WAL mode + busy_timeout must be set."""

    def test_sets_wal_mode(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import apply_pragmas

        conn = _fresh_conn(tmp_path / "test.db")
        apply_pragmas(conn)
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_sets_busy_timeout(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import apply_pragmas

        conn = _fresh_conn(tmp_path / "test.db")
        apply_pragmas(conn)
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert timeout == 5000
        conn.close()


class TestCreateTables:
    """create_tables() — creates all four tables + indexes, idempotent."""

    def test_creates_all_four_tables(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import apply_pragmas, create_tables

        conn = _fresh_conn(tmp_path / "test.db")
        apply_pragmas(conn)
        create_tables(conn)

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "sessions" in tables
        assert "turns" in tables
        assert "moments" in tables
        assert "metrics" in tables
        conn.close()

    def test_is_idempotent(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import apply_pragmas, create_tables

        conn = _fresh_conn(tmp_path / "test.db")
        apply_pragmas(conn)
        create_tables(conn)
        create_tables(conn)  # second call must not raise
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "sessions" in tables
        conn.close()

    def test_creates_expected_indexes(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import apply_pragmas, create_tables

        conn = _fresh_conn(tmp_path / "test.db")
        apply_pragmas(conn)
        create_tables(conn)

        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        expected = {
            "idx_sessions_status",
            "idx_turns_session_seq",
            "idx_moments_session",
            "idx_moments_flushed",
            "idx_moments_content_hash",
        }
        assert expected <= indexes
        conn.close()


class TestVerifyIntegrity:
    """verify_integrity() — returns 'ok' for healthy DB."""

    def test_returns_ok_for_new_db(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import (
            apply_pragmas,
            create_tables,
            verify_integrity,
        )

        conn = _fresh_conn(tmp_path / "test.db")
        apply_pragmas(conn)
        create_tables(conn)
        result = verify_integrity(conn)
        assert result == "ok"
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.1 — Intent Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestWALBeforeWrite:
    """意图: 并发安全——WAL 必须在任何 CREATE TABLE 之前执行。"""

    def test_wal_before_any_write(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import apply_pragmas, create_tables

        conn = _fresh_conn(tmp_path / "test.db")
        mode_before = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode_before in ("delete", "memory"), (
            f"Default journal_mode should be 'delete', got {mode_before!r}"
        )
        apply_pragmas(conn)
        create_tables(conn)
        mode_after = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode_after == "wal", (
            "After apply_pragmas + create_tables, journal_mode must be 'wal'"
        )
        conn.close()


class TestFailureNotSilent:
    """意图: 失败不静默——open_database 在权限不足时必须抛异常。"""

    def test_raises_on_unwritable_path(self):
        from bible_cc_plugin.daemon.buffer import open_database

        with pytest.raises((sqlite3.OperationalError, PermissionError, OSError)):
            open_database("/root/not_allowed/test.db")


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.2: buffer.py CRUD Layer
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def conn_wal(tmp_path) -> sqlite3.Connection:
    """Return a connection with WAL + schema ready for CRUD tests."""
    from bible_cc_plugin.daemon.buffer import apply_pragmas, create_tables

    conn = _fresh_conn(tmp_path / "crud.db")
    apply_pragmas(conn)
    create_tables(conn)
    return conn


class TestSessionCRUD:
    """Session insert / get / mark_completed / count."""

    def test_insert_session_creates_new(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import insert_session

        result = insert_session(conn_wal, "sess-1")
        assert result is True

        row = conn_wal.execute("SELECT * FROM sessions WHERE session_id=?", ("sess-1",)).fetchone()
        assert row is not None
        assert row["status"] == "active"

    def test_insert_session_idempotent(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import insert_session

        assert insert_session(conn_wal, "sess-1") is True
        assert insert_session(conn_wal, "sess-1") is False

    def test_get_session_returns_row(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import get_session, insert_session

        insert_session(conn_wal, "sess-1")
        row = get_session(conn_wal, "sess-1")
        assert row is not None
        assert row["session_id"] == "sess-1"

    def test_get_session_returns_none_for_missing(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import get_session

        assert get_session(conn_wal, "nonexistent") is None

    def test_mark_session_completed(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import insert_session, mark_session_completed

        insert_session(conn_wal, "sess-1")
        mark_session_completed(conn_wal, "sess-1")
        row = conn_wal.execute(
            "SELECT status, closed_at FROM sessions WHERE session_id=?", ("sess-1",)
        ).fetchone()
        assert row["status"] == "completed"
        assert row["closed_at"] is not None

    def test_count_active_and_completed(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            count_active_sessions,
            count_completed_sessions,
            insert_session,
            mark_session_completed,
        )

        insert_session(conn_wal, "sess-a")
        insert_session(conn_wal, "sess-b")
        mark_session_completed(conn_wal, "sess-b")

        assert count_active_sessions(conn_wal) == 1
        assert count_completed_sessions(conn_wal) == 1


class TestTurnCRUD:
    """Turn insert (user/tool) with per-session seq and full output storage."""

    def test_insert_turn_user_creates_turn(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_user,
            session_seq,
        )

        insert_session(conn_wal, "sess-1")
        session_seq["sess-1"] = 0
        seq = insert_turn_user(conn_wal, "sess-1", "hello world")
        assert seq == 1

        row = conn_wal.execute(
            "SELECT * FROM turns WHERE session_id=? AND seq=?", ("sess-1", 1)
        ).fetchone()
        assert row["role"] == "user"
        assert row["content"] == "hello world"

    def test_turn_user_seq_increments(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_user,
            session_seq,
        )

        insert_session(conn_wal, "sess-1")
        session_seq["sess-1"] = 0
        assert insert_turn_user(conn_wal, "sess-1", "msg 1") == 1
        assert insert_turn_user(conn_wal, "sess-1", "msg 2") == 2
        assert insert_turn_user(conn_wal, "sess-1", "msg 3") == 3

    def test_insert_turn_tool_stores_full_output(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_tool,
            session_seq,
        )

        insert_session(conn_wal, "sess-1")
        session_seq["sess-1"] = 0
        big_output = "X" * 10000
        seq = insert_turn_tool(conn_wal, "sess-1", "read_file", {"path": "/f.py"}, big_output)
        assert seq == 1

        row = conn_wal.execute(
            "SELECT * FROM turns WHERE session_id=? AND seq=?", ("sess-1", 1)
        ).fetchone()
        assert row["tool_output"] == big_output
        assert row["tool_name"] == "read_file"
        assert row["role"] == "assistant"

    def test_turn_user_unknown_session_raises(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import insert_turn_user

        with pytest.raises(Exception):
            insert_turn_user(conn_wal, "nonexistent", "msg")

    def test_turn_user_completed_session_raises(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_user,
            mark_session_completed,
            session_seq,
        )

        insert_session(conn_wal, "sess-1")
        session_seq["sess-1"] = 0
        mark_session_completed(conn_wal, "sess-1")
        with pytest.raises(Exception):
            insert_turn_user(conn_wal, "sess-1", "msg")

    def test_increment_turn_count_and_buffered_chars(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import increment_turn_count, insert_session

        insert_session(conn_wal, "sess-1")
        increment_turn_count(conn_wal, "sess-1", 100)
        row = conn_wal.execute(
            "SELECT turn_count, buffered_chars FROM sessions WHERE session_id=?",
            ("sess-1",),
        ).fetchone()
        assert row["turn_count"] == 1
        assert row["buffered_chars"] == 100

        increment_turn_count(conn_wal, "sess-1", 50)
        row = conn_wal.execute(
            "SELECT turn_count, buffered_chars FROM sessions WHERE session_id=?",
            ("sess-1",),
        ).fetchone()
        assert row["turn_count"] == 2
        assert row["buffered_chars"] == 150


class TestNextSeqRecovery:
    """get_next_seq — recovers from DB on first call, increments in memory."""

    def test_get_next_seq_starts_at_one_for_new_session(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import get_next_seq, insert_session, session_seq

        insert_session(conn_wal, "sess-1")
        session_seq.pop("sess-1", None)
        seq = get_next_seq(conn_wal, "sess-1")
        assert seq == 1

    def test_get_next_seq_recovers_from_db(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            get_next_seq,
            insert_session,
            insert_turn_user,
            session_seq,
        )

        insert_session(conn_wal, "sess-1")
        session_seq["sess-1"] = 0
        insert_turn_user(conn_wal, "sess-1", "t1")
        insert_turn_user(conn_wal, "sess-1", "t2")
        insert_turn_user(conn_wal, "sess-1", "t3")
        # Simulate daemon restart
        session_seq.pop("sess-1", None)
        seq = get_next_seq(conn_wal, "sess-1")
        assert seq == 4


class TestMomentCRUD:
    """Moment insert with content-hash dedup, get_unflushed, get_by_session."""

    def test_insert_moment_succeeds(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment

        ch = compute_content_hash("sess-1", "t", "n")
        moment_id = insert_moment(conn_wal, "sess-1", "decision", "t", "n", ch)
        assert moment_id is not None
        assert moment_id >= 1

    def test_insert_moment_dedup_returns_none(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment

        ch = compute_content_hash("sess-1", "t", "n")
        id1 = insert_moment(conn_wal, "sess-1", "decision", "t", "n", ch)
        assert id1 is not None
        id2 = insert_moment(conn_wal, "sess-1", "decision", "t", "n", ch)
        assert id2 is None
        count = conn_wal.execute(
            "SELECT COUNT(*) FROM moments WHERE content_hash=?", (ch,)
        ).fetchone()[0]
        assert count == 1

    def test_get_unflushed_moments_filters_flushed(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            compute_content_hash,
            get_unflushed_moments,
            insert_moment,
            insert_session,
        )

        insert_session(conn_wal, "sess-1")
        ch1 = compute_content_hash("sess-1", "Mom 1", "n1")
        ch2 = compute_content_hash("sess-1", "Mom 2", "n2")
        insert_moment(conn_wal, "sess-1", "decision", "Mom 1", "n1", ch1)
        m2 = insert_moment(conn_wal, "sess-1", "decision", "Mom 2", "n2", ch2)
        conn_wal.execute("UPDATE moments SET flushed=1 WHERE id=?", (m2,))
        conn_wal.commit()

        unflushed = get_unflushed_moments(conn_wal, "sess-1")
        assert len(unflushed) == 1
        assert unflushed[0]["title"] == "Mom 1"

    def test_get_moments_by_session_returns_all(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            compute_content_hash,
            get_moments_by_session,
            insert_moment,
            insert_session,
        )

        insert_session(conn_wal, "sess-1")
        ch1 = compute_content_hash("sess-1", "A", "a")
        ch2 = compute_content_hash("sess-1", "B", "b")
        insert_moment(conn_wal, "sess-1", "decision", "A", "a", ch1)
        insert_moment(conn_wal, "sess-1", "decision", "B", "b", ch2)

        all_m = get_moments_by_session(conn_wal, "sess-1")
        assert len(all_m) == 2

    def test_count_pending_and_total_turns(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            compute_content_hash,
            count_pending_moments,
            count_total_turns,
            insert_moment,
            insert_session,
            insert_turn_user,
            session_seq,
        )

        insert_session(conn_wal, "sess-1")
        session_seq["sess-1"] = 0
        insert_turn_user(conn_wal, "sess-1", "hi")
        insert_turn_user(conn_wal, "sess-1", "bye")

        ch = compute_content_hash("sess-1", "m", "mm")
        insert_moment(conn_wal, "sess-1", "decision", "m", "mm", ch)

        assert count_total_turns(conn_wal) == 2
        assert count_pending_moments(conn_wal) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.2 — Intent Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCRUDThinWrappers:
    """意图: 职责分离——CRUD 函数只做单条 SQL + commit。"""

    def test_crud_functions_are_thin_wrappers(self):
        import inspect

        from bible_cc_plugin.daemon import buffer as buf

        crud_names = [
            "insert_session",
            "mark_session_completed",
            "get_session",
            "count_active_sessions",
            "count_completed_sessions",
            "insert_turn_user",
            "insert_turn_tool",
            "increment_turn_count",
            "insert_moment",
            "get_unflushed_moments",
            "get_moments_by_session",
            "count_pending_moments",
            "count_total_turns",
        ]
        for name in crud_names:
            fn = getattr(buf, name, None)
            assert fn is not None, f"Missing CRUD function: {name}"
            source = inspect.getsource(fn)
            non_doc_lines = [
                line
                for line in source.splitlines()
                if line.strip() and not line.strip().startswith(('"""', "#"))
            ]
            assert len(non_doc_lines) < 30, (
                f"{name} too long ({len(non_doc_lines)} lines). CRUD should be thin."
            )


class TestToolOutputPreserved:
    """意图: 数据完整——tool_output 必须完整存储不截断。"""

    def test_full_tool_output_preserved_for_llm_extraction(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            insert_session,
            insert_turn_tool,
            session_seq,
        )

        insert_session(conn_wal, "sess-1")
        session_seq["sess-1"] = 0
        big_output = "LINE_" * 2000  # ~10K chars
        seq = insert_turn_tool(conn_wal, "sess-1", "bash", {"cmd": "ls"}, big_output)
        row = conn_wal.execute(
            "SELECT tool_output FROM turns WHERE session_id=? AND seq=?",
            ("sess-1", seq),
        ).fetchone()
        assert row["tool_output"] == big_output


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.3: Migration Engine
# ══════════════════════════════════════════════════════════════════════════════


class TestMigrationEngine:
    """run_migrations — creates schema, idempotent, skips applied."""

    def test_run_migrations_creates_all_tables(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import open_database, run_migrations

        conn = open_database(str(tmp_path / "migrate.db"))
        run_migrations(conn)

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "sessions" in tables
        assert "turns" in tables
        assert "moments" in tables
        assert "metrics" in tables
        assert "schema_version" in tables
        conn.close()

    def test_run_migrations_is_idempotent(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import open_database, run_migrations

        conn = open_database(str(tmp_path / "migrate.db"))
        run_migrations(conn)
        run_migrations(conn)  # second call must not raise
        conn.close()

    def test_run_migrations_sets_schema_version(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import (
            get_schema_version,
            open_database,
            run_migrations,
        )

        conn = open_database(str(tmp_path / "migrate.db"))
        run_migrations(conn)
        v = get_schema_version(conn)
        assert v == 1
        conn.close()

    def test_run_migrations_skips_applied(self, tmp_path):
        from bible_cc_plugin.daemon.buffer import (
            get_schema_version,
            open_database,
            run_migrations,
        )

        conn = open_database(str(tmp_path / "migrate.db"))
        run_migrations(conn)
        assert get_schema_version(conn) == 1
        run_migrations(conn)
        assert get_schema_version(conn) == 1
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.3 — Intent Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestMigrationSafety:
    """意图: 生产兼容——已有 migration SQL 不可修改；失败不静默。"""

    def test_existing_migration_sql_never_modified(self):
        """MIGRATIONS[0].sql hash must be stable — breaking if changed."""
        from bible_cc_plugin.daemon.buffer import MIGRATIONS

        assert len(MIGRATIONS) >= 1, "MIGRATIONS must have at least v1"
        sql_hash = hashlib.sha256(MIGRATIONS[0].sql.encode("utf-8")).hexdigest()
        # This hash encodes the expected v1 migration SQL.
        # If it changes, you edited an existing migration — add a NEW one instead.
        assert sql_hash == ("32c109c71fc877c2ee1c3fb68d0716c312ed1beae53c58ea0fcdc74e25dcbe72"), (
            f"MIGRATIONS[0].sql hash changed ({sql_hash}). "
            "Never edit existing migrations. Add a new one."
        )

    def test_migration_failure_stops_daemon_startup(self, tmp_path):
        """A bad migration must raise, not silently skip."""
        from bible_cc_plugin.daemon.buffer import (
            MIGRATIONS,
            Migration,
            open_database,
            run_migrations,
        )

        original = list(MIGRATIONS)
        try:
            MIGRATIONS.append(
                Migration(version=999, description="bad", sql="CREATE TABLE oops (); BAD SQL !!!;")
            )
            conn = open_database(str(tmp_path / "bad.db"))
            with pytest.raises(Exception):
                run_migrations(conn)
            conn.close()
        finally:
            MIGRATIONS[:] = original


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.4: Content-Hash Dedup
# ══════════════════════════════════════════════════════════════════════════════


class TestContentHash:
    """compute_content_hash — deterministic, unique per content."""

    def test_content_hash_deterministic(self):
        from bible_cc_plugin.daemon.buffer import compute_content_hash

        h1 = compute_content_hash("s", "t", "n")
        h2 = compute_content_hash("s", "t", "n")
        assert h1 == h2

    def test_content_hash_different_title_produces_different_hash(self):
        from bible_cc_plugin.daemon.buffer import compute_content_hash

        h1 = compute_content_hash("s", "Title A", "n")
        h2 = compute_content_hash("s", "Title B", "n")
        assert h1 != h2

    def test_content_hash_different_session_id_produces_different_hash(self):
        from bible_cc_plugin.daemon.buffer import compute_content_hash

        h1 = compute_content_hash("sess-a", "t", "n")
        h2 = compute_content_hash("sess-b", "t", "n")
        assert h1 != h2

    def test_content_hash_integration_with_insert_moment(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment

        ch = compute_content_hash("sess-1", "Dec", "Go with Postgres")
        id1 = insert_moment(conn_wal, "sess-1", "decision", "Dec", "Go with Postgres", ch)
        assert id1 is not None
        id2 = insert_moment(conn_wal, "sess-1", "decision", "Dec", "Go with Postgres", ch)
        assert id2 is None


# ══════════════════════════════════════════════════════════════════════════════
# Feature 1a.4 — Intent Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDedupIsSilent:
    """意图: dedup 是正常行为——重复 moment 不可报错或 log warning。"""

    def test_dedup_is_silent_not_error(self, conn_wal, caplog):
        import logging

        from bible_cc_plugin.daemon.buffer import compute_content_hash, insert_moment

        caplog.set_level(logging.WARNING)

        ch = compute_content_hash("sess-1", "Dup", "dup narrative")
        insert_moment(conn_wal, "sess-1", "decision", "Dup", "dup narrative", ch)
        for _ in range(10):
            insert_moment(conn_wal, "sess-1", "decision", "Dup", "dup narrative", ch)

        warnings_or_worse = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "dedup" in r.getMessage().lower()
        ]
        assert len(warnings_or_worse) == 0, "Dedup must not produce WARNING+ log records."


# ══════════════════════════════════════════════════════════════════════════════
# Feature 2b.3: get_recent_turns (READ helper for detection pipeline)
# ══════════════════════════════════════════════════════════════════════════════


class TestGetRecentTurns:
    """get_recent_turns() — fetch last N turns for detection prompt."""

    def test_returns_limited(self, conn_wal):
        """Returns most recent turns up to limit, in descending seq order."""
        from bible_cc_plugin.daemon.buffer import (
            get_recent_turns,
            insert_session,
            insert_turn_user,
        )

        insert_session(conn_wal, "s1")
        for msg in ["m1", "m2", "m3", "m4", "m5"]:
            insert_turn_user(conn_wal, "s1", msg)

        turns = get_recent_turns(conn_wal, "s1", limit=3)
        assert len(turns) == 3
        assert turns[0]["content"] == "m5"
        assert turns[2]["content"] == "m3"
        for t in turns:
            assert "role" in t
            assert "content" in t
            assert "tool_name" in t
            assert "tool_output" in t

    def test_empty_session_returns_empty_list(self, conn_wal):
        """Non-existent session → [] without error."""
        from bible_cc_plugin.daemon.buffer import get_recent_turns

        turns = get_recent_turns(conn_wal, "nonexistent", limit=3)
        assert turns == []


class TestPhase1DetectionWindow:
    """get_phase1_detection_window() — latest user intent plus following tools."""

    def test_keeps_latest_user_turn_when_tools_fill_recent_window(self, conn_wal):
        """Recent tool-heavy activity must not push the user decision out."""
        from bible_cc_plugin.daemon.buffer import (
            get_phase1_detection_window,
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )

        insert_session(conn_wal, "s1")
        session_seq["s1"] = 0
        insert_turn_user(conn_wal, "s1", "Use the Atlas V4 contract as source of truth")
        for i in range(5):
            insert_turn_tool(conn_wal, "s1", "Read", {"path": f"f{i}.md"}, f"tool-{i}")

        turns = get_phase1_detection_window(conn_wal, "s1", limit=3)

        assert len(turns) == 3
        assert turns[0]["role"] == "user"
        assert "V4 contract" in turns[0]["content"]
        assert [t["tool_output"] for t in turns[1:]] == ["tool-3", "tool-4"]

    def test_returns_chronological_fallback_when_no_user_turn(self, conn_wal):
        """No user turn → preserve old recent behavior but feed prompt chronologically."""
        from bible_cc_plugin.daemon.buffer import (
            get_phase1_detection_window,
            insert_session,
            insert_turn_tool,
            session_seq,
        )

        insert_session(conn_wal, "s2")
        session_seq["s2"] = 0
        for i in range(4):
            insert_turn_tool(conn_wal, "s2", "Read", {}, f"tool-{i}")

        turns = get_phase1_detection_window(conn_wal, "s2", limit=2)

        assert [t["tool_output"] for t in turns] == ["tool-2", "tool-3"]

    def test_max_seq_freezes_window_before_later_user_turn(self, conn_wal):
        """A queued detection task should not drift into later user prompts."""
        from bible_cc_plugin.daemon.buffer import (
            get_phase1_detection_window,
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )

        insert_session(conn_wal, "s3")
        session_seq["s3"] = 0
        insert_turn_user(conn_wal, "s3", "First decision")
        trigger_seq = insert_turn_tool(conn_wal, "s3", "Read", {}, "first-tool")
        insert_turn_user(conn_wal, "s3", "Later unrelated prompt")
        insert_turn_tool(conn_wal, "s3", "Read", {}, "later-tool")

        turns = get_phase1_detection_window(conn_wal, "s3", limit=8, max_seq=trigger_seq)

        assert [t["content"] or t["tool_output"] for t in turns] == [
            "First decision",
            "first-tool",
        ]

    def test_max_seq_freezes_tool_only_fallback(self, conn_wal):
        """Tool-only fallback should also respect queued max_seq."""
        from bible_cc_plugin.daemon.buffer import (
            get_phase1_detection_window,
            insert_session,
            insert_turn_tool,
            session_seq,
        )

        insert_session(conn_wal, "s4")
        session_seq["s4"] = 0
        insert_turn_tool(conn_wal, "s4", "Read", {}, "tool-0")
        trigger_seq = insert_turn_tool(conn_wal, "s4", "Read", {}, "tool-1")
        insert_turn_tool(conn_wal, "s4", "Read", {}, "tool-2")

        turns = get_phase1_detection_window(conn_wal, "s4", limit=8, max_seq=trigger_seq)

        assert [t["tool_output"] for t in turns] == ["tool-0", "tool-1"]

    def test_user_trigger_can_include_previous_user_window(self, conn_wal):
        """User-triggered detection keeps prior tool-backed context in scope."""
        from bible_cc_plugin.daemon.buffer import (
            get_phase1_detection_window,
            insert_session,
            insert_turn_tool,
            insert_turn_user,
            session_seq,
        )

        insert_session(conn_wal, "s5")
        session_seq["s5"] = 0
        insert_turn_user(conn_wal, "s5", "First decision")
        insert_turn_tool(conn_wal, "s5", "Read", {}, "first-tool")
        trigger_seq = insert_turn_user(conn_wal, "s5", "Follow-up prompt")

        turns = get_phase1_detection_window(
            conn_wal,
            "s5",
            limit=8,
            max_seq=trigger_seq,
            include_previous_user=True,
        )

        assert [t["content"] or t["tool_output"] for t in turns] == [
            "First decision",
            "first-tool",
            "Follow-up prompt",
        ]


class TestNullByteDelimiter:
    """意图: hash 安全性——\\0 分隔符防止字段拼接碰撞。"""

    def test_null_byte_delimiter_prevents_field_splicing(self):
        from bible_cc_plugin.daemon.buffer import compute_content_hash

        h1 = compute_content_hash("ab", "c", "d")
        h2 = compute_content_hash("a", "bc", "d")
        assert h1 != h2, f"\\0 delimiter failed: h1={h1[:16]}... h2={h2[:16]}..."


# ══════════════════════════════════════════════════════════════════════════════
# Feature 2c.2: get_all_session_turns
# ══════════════════════════════════════════════════════════════════════════════


class TestGetAllSessionTurns:
    """get_all_session_turns() — fetch ALL turns for Phase 2 retrospective."""

    def test_returns_all_turns(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import (
            get_all_session_turns,
            insert_session,
            insert_turn_user,
        )

        insert_session(conn_wal, "s1")
        for msg in ["m1", "m2", "m3"]:
            insert_turn_user(conn_wal, "s1", msg)

        turns = get_all_session_turns(conn_wal, "s1")
        assert len(turns) == 3
        assert turns[0]["content"] == "m1"
        assert turns[2]["content"] == "m3"

    def test_empty_session_returns_empty_list(self, conn_wal):
        from bible_cc_plugin.daemon.buffer import get_all_session_turns

        assert get_all_session_turns(conn_wal, "nonexistent") == []
