"""Phase 0/1 daemon server — FastAPI app with health, session, turn, debug endpoints.

Design: 02-interfaces.md §1.1-1.4.
Phase 1a: health endpoint reads real SQLite data via buffer.py.
Phase 1b: session/turn CRUD endpoints.
Phase 1c: context injection.
Phase 1d: request-id middleware, verbose health, debug endpoints.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bible_cc_plugin.logging_config import setup_logging

_logger = setup_logging(level="INFO")
_START_TIME = time.time()

app = FastAPI(title="bible-cc-daemon", version="0.1.0")

_startup_timings: dict[str, int] = {}  # Phase 1d: step → ms
_debug_mode: bool = os.getenv("BIBLE_CC_DEBUG", "") in ("1", "true", "yes")

_logger.info("daemon starting on port %s", os.getenv("BIBLE_CC_DAEMON_PORT", "9777"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request-ID middleware (Phase 1d) ─────────────────────────────────────


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Inject X-Request-ID into every response."""
    rid = uuid.uuid4().hex
    request.state.request_id = rid
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    response.headers["X-Request-ID"] = rid
    _logger.debug(
        "%s %s → %d (%dms) [rid=%s]",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        rid,
    )
    return response


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
async def daemon_health(verbose: bool = False):
    """Liveness + diagnostic probe (02-interfaces.md §1.1).

    Phase 1a: SQLite fields read real data.
    Phase 1d: verbose=true adds startup_timings, sqlite_detailed, config_sources.
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
        db_path_str = os.getenv("BIBLE_CC_DB_PATH", str(Path.home() / ".bible-cc" / "daemon.db"))
        try:
            db_size = Path(db_path_str).stat().st_size
        except OSError:
            db_size = -1
    else:
        active = completed = turns = pending = 0
        integrity = _db_error or "unavailable"
        schema_ver = -1
        db_size = -1

    result: dict = {
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

    if verbose:
        result["startup_timings"] = dict(_startup_timings) if _startup_timings else {}
        if conn is not None:
            result["sqlite_detailed"] = _build_sqlite_detailed(conn)
        result["config_sources"] = _build_config_sources()

    return result


# ── recovery cache (Phase 1c) ────────────────────────────────────────────

_recovery_cache: dict[str, dict] = {}  # session_id → recovery data


# ── request models (Phase 1b-1c) ─────────────────────────────────────────
class _ContextInjectRequest(BaseModel):
    session_id: str
    user_message: str = ""


class _SessionStartRequest(BaseModel):
    session_id: str


class _SessionEndRequest(BaseModel):
    session_id: str


class _TurnUserRequest(BaseModel):
    session_id: str
    message: str


class _TurnToolRequest(BaseModel):
    session_id: str
    tool_name: str
    arguments: dict = {}
    output: str = ""


# ── Session / Turn endpoints (Phase 1b, 02-interfaces.md §1.2-1.4) ──────


@app.post("/session/start")
async def session_start(req: _SessionStartRequest):
    """Create a new session.  Scans for unclosed sessions (crash recovery)."""
    conn = _get_db()
    if conn is None:
        raise HTTPException(503, "daemon database unavailable")

    from bible_cc_plugin.daemon.buffer import (
        get_recovery,
        insert_session,
    )

    # crash recovery scan — collect unclosed session ids
    try:
        recovery = get_recovery(conn, current_session_id=req.session_id)
    except Exception as exc:
        _logger.warning("crash recovery scan failed: %s", exc)
        recovery = None

    # Cache recovery data for /context/inject
    if recovery is not None:
        _recovery_cache[req.session_id] = recovery

    is_new = insert_session(conn, req.session_id)
    _logger.info(
        "session/start %s is_new=%s recovery=%s",
        req.session_id,
        is_new,
        recovery["unclosed_sessions_found"] if recovery else 0,
    )

    return {
        "session_id": req.session_id,
        "is_new": is_new,
        "recovery": recovery,
    }


@app.post("/session/end")
async def session_end(req: _SessionEndRequest):
    """Mark a session completed.  Phase 1: no LLM / no flush."""
    conn = _get_db()
    if conn is None:
        raise HTTPException(503, "daemon database unavailable")

    from bible_cc_plugin.daemon.buffer import get_session, mark_session_completed

    row = get_session(conn, req.session_id)
    if row is None:
        raise HTTPException(404, f"session not found: {req.session_id}")
    if row["status"] != "active":
        _logger.info("session/end %s already %s", req.session_id, row["status"])
        return {
            "session_id": req.session_id,
            "moments_flushed": 0,
            "status": "already_completed",
        }

    mark_session_completed(conn, req.session_id)
    _recovery_cache.pop(req.session_id, None)  # DRIFT #1: prevent unbounded growth
    _logger.info("session/end %s completed", req.session_id)
    return {
        "session_id": req.session_id,
        "moments_flushed": 0,
        "status": "completed",
    }


@app.post("/turn/user")
async def turn_user(req: _TurnUserRequest):
    """Buffer a user message.  Returns immediately (<50ms)."""
    conn = _get_db()
    if conn is None:
        raise HTTPException(503, "daemon database unavailable")

    from bible_cc_plugin.daemon.buffer import (
        get_session,
        increment_turn_count,
        insert_turn_user,
    )

    row = get_session(conn, req.session_id)
    if row is None:
        raise HTTPException(400, f"session not found: {req.session_id}")
    if row["status"] != "active":
        raise HTTPException(400, f"session {req.session_id} is {row['status']}")

    turn_id = insert_turn_user(conn, req.session_id, req.message)
    increment_turn_count(conn, req.session_id, len(req.message))
    _logger.debug("turn/user %s seq=%d", req.session_id, turn_id)
    return {"turn_id": turn_id, "queued": False}


@app.post("/turn/tool")
async def turn_tool(req: _TurnToolRequest):
    """Buffer a tool invocation.  Stores full output verbatim."""
    conn = _get_db()
    if conn is None:
        raise HTTPException(503, "daemon database unavailable")

    from bible_cc_plugin.daemon.buffer import (
        get_session,
        increment_turn_count,
        insert_turn_tool,
    )

    row = get_session(conn, req.session_id)
    if row is None:
        raise HTTPException(400, f"session not found: {req.session_id}")
    if row["status"] != "active":
        raise HTTPException(400, f"session {req.session_id} is {row['status']}")

    turn_id = insert_turn_tool(conn, req.session_id, req.tool_name, req.arguments, req.output)
    increment_turn_count(conn, req.session_id, len(req.output))
    _logger.debug("turn/tool %s seq=%d tool=%s", req.session_id, turn_id, req.tool_name)
    return {"turn_id": turn_id, "queued": False}


@app.post("/context/inject")
async def context_inject(req: _ContextInjectRequest):
    """Return local-buffer context for session (02-interfaces.md §1.4).

    Pure local SQLite — no BiBLE calls.  Three branches:
    - empty (new session, no data)
    - crash_recovery (prior unclosed session data)
    - clear_or_compact (current session turns + moments)
    """
    conn = _get_db()
    if conn is None:
        raise HTTPException(503, "daemon database unavailable")

    from bible_cc_plugin.daemon.injector import build_context

    recovery = _recovery_cache.pop(req.session_id, None)

    ctx, sources = build_context(
        conn,
        session_id=req.session_id,
        recovery_data=recovery,
        fallback_mode="skip",
        token_budget=1200,
        include_turns_summary=True,
        include_moments=True,
    )
    return {"context": ctx, "sources": sources}


# ── Moment management (Phase 2a.4) ────────────────────────────────────────


@app.get("/daemon/moments")
async def list_moments(session_id: str):
    """Return all moments for a session, newest first."""
    conn = _get_db()
    if conn is None:
        raise HTTPException(503, "daemon database unavailable")
    from bible_cc_plugin.daemon.buffer import get_moments_by_session

    return {"moments": get_moments_by_session(conn, session_id)}


@app.get("/daemon/sessions")
async def list_sessions():
    """Return all sessions ordered by created_at (newest first)."""
    conn = _get_db()
    if conn is None:
        raise HTTPException(503, "daemon database unavailable")

    rows = conn.execute(
        "SELECT session_id, status, created_at, closed_at, turn_count "
        "FROM sessions ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------


# ── verbose health helpers (Phase 1d) ───────────────────────────────────────


def _build_sqlite_detailed(conn) -> dict:
    """Build sqlite_detailed section for verbose health."""
    tables = ("sessions", "turns", "moments", "metrics")
    row_counts = {}
    for t in tables:
        r = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
        row_counts[t] = r[0] if r else 0
    return {"table_row_counts": row_counts}


def _build_config_sources() -> dict:
    """Build config_sources section — key→source mapping, token masked."""
    sources = {
        "bible.base_url": os.getenv("BIBLE_ATLAS_BASE_URL") or "default",
        "daemon.port": os.getenv("BIBLE_CC_DAEMON_PORT") or "default(9777)",
        "daemon.db_path": os.getenv("BIBLE_CC_DB_PATH") or "default(~/.bible-cc/daemon.db)",
    }
    token = os.getenv("BIBLE_ATLAS_TOKEN")
    sources["bible.token"] = "***" if token else "<none>"
    return sources


# ── Debug endpoints (Phase 1d, only in debug mode) ──────────────────────────


if _debug_mode:

    @app.get("/daemon/debug/schema")
    async def debug_schema():
        conn = _get_db()
        if conn is None:
            raise HTTPException(503, "database unavailable")
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        ).fetchall()
        return {"ddl": [r[0] for r in rows]}

    @app.get("/daemon/debug/tables/{name}")
    async def debug_table_rows(name: str, limit: int = 20):
        conn = _get_db()
        if conn is None:
            raise HTTPException(503, "database unavailable")
        if name not in ("sessions", "turns", "moments", "metrics", "schema_version"):
            raise HTTPException(404, f"unknown table: {name}")
        limit = min(limit, 100)
        rows = conn.execute(
            f"SELECT * FROM {name} ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        return {"rows": [dict(r) for r in rows], "total": total}

    @app.get("/daemon/debug/turns")
    async def debug_turns_by_session(session_id: str, limit: int = 50):
        conn = _get_db()
        if conn is None:
            raise HTTPException(503, "database unavailable")
        limit = min(limit, 100)
        rows = conn.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY seq DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return {"turns": [dict(r) for r in rows]}


def _read_port() -> int:
    return int(os.getenv("BIBLE_CC_DAEMON_PORT", "9777"))
