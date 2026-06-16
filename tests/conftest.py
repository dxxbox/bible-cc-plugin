"""Global test fixtures — enforced isolation from production state.

All tests automatically get a temporary SQLite database so they never
touch ~/.bible-cc/daemon.db or ~/.bible-cc/config.json.
"""

from __future__ import annotations

import pytest


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
