"""Global test fixtures — enforced isolation from production state.

All tests automatically get a temporary SQLite database so they never
touch ~/.bible-cc/daemon.db or ~/.bible-cc/config.json.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # bible-cc-plugin
BIBLE_ATLAS = PROJECT_ROOT.parent / "BiBLE-Atlas"
BIBLE_PY = BIBLE_ATLAS / ".venv" / "bin" / "python"


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_bible_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    client = httpx.Client(trust_env=False, timeout=2)
    while time.time() < deadline:
        try:
            if client.get(f"{base_url}/health").status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"BiBLE test server not healthy within {timeout}s")


@pytest.fixture(scope="session")
def bible_test_url() -> str:
    """Start the BiBLE test-mode server once per test session.

    Shared by integration and contract tests; skipped when the sibling
    BiBLE-Atlas checkout/venv is unavailable.
    """
    if not BIBLE_PY.exists():
        pytest.skip(f"BiBLE-Atlas venv not found at {BIBLE_PY}")

    port = _find_free_port()
    log_path = Path(tempfile.gettempdir()) / "bible-test-server.log"
    log_fh = open(log_path, "w")
    proc = subprocess.Popen(
        [str(BIBLE_PY), "-m", "bible.test_mode.server", "--addr", f"127.0.0.1:{port}"],
        cwd=str(BIBLE_ATLAS),
        stdout=log_fh,
        stderr=log_fh,
        env={**os.environ, "PYTHONPATH": str(BIBLE_ATLAS)},
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_bible_health(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log_fh.close()


@pytest.fixture(autouse=True)
def _isolate_bible_cc_env(tmp_path, monkeypatch):
    """Enforce DB isolation for every test.

    - BIBLE_CC_DB_PATH → temporary SQLite per test.
    - BIBLE_CC_CONFIG_PATH → nonexistent path to skip production config.json.

    Individual tests can override these via their own monkeypatch.setenv
    (later calls win).  Direct-sqlite3 tests (conn_wal, _fresh_conn) are
    unaffected since they don't read env vars.
    """
    db_dir = tmp_path / "bible-cc-test"
    db_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("BIBLE_CC_DB_PATH", str(db_dir / "daemon.db"))
    monkeypatch.setenv("BIBLE_CC_CONFIG_PATH", str(db_dir / "nonexistent-config.json"))
    monkeypatch.setenv("BIBLE_CC_LOG_FILE", str(db_dir / "daemon.log"))
