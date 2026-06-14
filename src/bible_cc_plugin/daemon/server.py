"""Phase 0 daemon server — minimal FastAPI app with health + start/stop endpoints.

Design: 02-interfaces.md §1.1 (lifecycle endpoints).
Phase 1a: health endpoint reads real SQLite data via buffer.py.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bible_cc_plugin.logging_config import setup_logging

_logger = setup_logging(level="INFO")
_START_TIME = time.time()

app = FastAPI(title="bible-cc-daemon", version="0.1.0")

_logger.info("daemon starting on port %s", os.getenv("BIBLE_CC_DAEMON_PORT", "9777"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── lazy SQLite init (Phase 1a) ───────────────────────────────────────

_db_conn = None
_db_error: str | None = None

# TODO Phase 1b: replace lazy init with full 6-step startup sequence
# (03-daemon/startup.md §1).  Current lazy init is a temporary bridge to
# get real SQLite data into the health endpoint without changing the
# Phase 0 daemon lifecycle.


def _get_db():
    """Return the singleton SQLite connection, initialising on first call.

    Lazy-init avoids changing the daemon startup sequence (Phase 1b).
    If initialisation fails the error is surfaced in ``/daemon/health``.
    """
    global _db_conn, _db_error
    if _db_conn is not None:
        return _db_conn
    if _db_error is not None:
        return None

    try:
        from bible_cc_plugin.daemon.buffer import (
            apply_pragmas,
            open_database,
            run_migrations,
            verify_integrity,
        )

        db_path = os.getenv("BIBLE_CC_DB_PATH", str(Path.home() / ".bible-cc" / "daemon.db"))
        conn = open_database(db_path)
        apply_pragmas(conn)
        run_migrations(conn)
        # create_tables() is NOT called here — run_migrations() v1 already
        # creates all tables.  create_tables() remains available as a
        # standalone function for tests and recovery scenarios.
        integrity = verify_integrity(conn)
        if integrity != "ok":
            _logger.warning("SQLite integrity check: %s", integrity)

        _db_conn = conn
        _logger.info("SQLite ready — %s", db_path)
        return conn
    except Exception as exc:
        _db_error = str(exc)
        _logger.error("SQLite init failed: %s", exc)
        return None


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Lifecycle endpoints (02-interfaces.md §1.1)
# ---------------------------------------------------------------------------


@app.post("/daemon/start")
async def daemon_start():
    """Idempotent start. If already running, return current state."""
    return {"pid": os.getpid(), "port": _read_port(), "status": "running"}


@app.post("/daemon/stop")
async def daemon_stop():
    """Graceful shutdown. Phase 0: no SQLite to flush."""
    _logger.info("daemon shutting down")
    import asyncio

    asyncio.get_event_loop().call_later(0.5, lambda: os._exit(0))
    return {"status": "stopped"}


@app.get("/daemon/health")
async def daemon_health():
    """Liveness + diagnostic probe (02-interfaces.md §1.1).

    Phase 1a: SQLite fields read real data. Falls back to zeros on DB error.
    """
    conn = _get_db()

    if conn is not None:
        from bible_cc_plugin.daemon.buffer import (
            count_active_sessions,
            count_completed_sessions,
            count_pending_moments,
            count_total_turns,
            get_schema_version,
            verify_integrity,
        )

        active = count_active_sessions(conn)
        completed = count_completed_sessions(conn)
        turns = count_total_turns(conn)
        pending = count_pending_moments(conn)
        integrity = verify_integrity(conn)
        schema_ver = get_schema_version(conn)
        db_size = (
            Path(os.getenv("BIBLE_CC_DB_PATH", str(Path.home() / ".bible-cc" / "daemon.db")))
            .stat()
            .st_size
        )
    else:
        active = completed = turns = pending = 0
        integrity = _db_error or "unavailable"
        schema_ver = -1
        db_size = -1

    return {
        "status": "ok",
        "pid": os.getpid(),
        "port": _read_port(),
        "uptime": int(time.time() - _START_TIME),
        "sessions": {"active": active, "completed": completed},
        "buffer": {"total_turns": turns, "pending_moments": pending},
        "bible_connectivity": {"reachable": None, "latency_ms": None},
        "sqlite": {
            "integrity": integrity,
            "schema_version": schema_ver,
            "size_bytes": db_size,
        },
    }


def _read_port() -> int:
    return int(os.getenv("BIBLE_CC_DAEMON_PORT", "9777"))
