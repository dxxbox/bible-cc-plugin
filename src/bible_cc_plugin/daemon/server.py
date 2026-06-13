"""Phase 0 daemon server — minimal FastAPI app with health + start/stop endpoints.

Design: 02-interfaces.md §1.1 (lifecycle endpoints).
Phase 0 implements only health/start/stop. Session/turn endpoints added in Phase 1.
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_START_TIME = time.time()

app = FastAPI(title="bible-cc-daemon", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    import asyncio

    asyncio.get_event_loop().call_later(0.5, lambda: os._exit(0))
    return {"status": "stopped"}


@app.get("/daemon/health")
async def daemon_health():
    """Liveness + diagnostic probe (02-interfaces.md §1.1)."""
    return {
        "status": "ok",
        "pid": os.getpid(),
        "port": _read_port(),
        "uptime": int(time.time() - _START_TIME),
        "sessions": {"active": 0, "completed": 0},
        "buffer": {"total_turns": 0, "pending_moments": 0},
        "bible_connectivity": {"reachable": None, "latency_ms": None},
        "sqlite": {"integrity": "ok", "schema_version": 0, "size_bytes": 0},
    }


def _read_port() -> int:
    return int(os.getenv("BIBLE_CC_DAEMON_PORT", "9777"))
