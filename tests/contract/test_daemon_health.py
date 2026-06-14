"""Contract test for GET /daemon/health — verifies response schema matches 02-interfaces.md §1.1.

Phase 1a: verifies that SQLite data is real (no longer hardcoded zeros).
"""

import subprocess
import sys
import time

import httpx
import pytest


@pytest.fixture(scope="module")
def daemon_url():
    """Start daemon on a dynamic port, yield the base URL, stop after tests."""
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "bible_cc_plugin.daemon.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_health(base_url, timeout=5)
    yield base_url
    proc.terminate()
    proc.wait(timeout=5)


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/daemon/health", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Daemon did not become healthy within {timeout}s")


class TestDaemonHealthContract:
    """Verify health endpoint response schema per 02-interfaces.md."""

    def test_health_returns_200(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        assert r.status_code == 200

    def test_health_response_has_status_field(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert data["status"] == "ok"

    def test_health_response_has_pid_int(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert "pid" in data
        assert isinstance(data["pid"], int)
        assert data["pid"] > 0

    def test_health_response_has_port_int(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert "port" in data
        assert isinstance(data["port"], int)

    def test_health_response_has_uptime_seconds(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert "uptime" in data
        assert isinstance(data["uptime"], (int, float))
        assert data["uptime"] >= 0

    def test_health_response_has_sessions_structure(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert "sessions" in data
        assert "active" in data["sessions"]
        assert "completed" in data["sessions"]

    def test_health_response_has_buffer_structure(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert "buffer" in data
        assert "total_turns" in data["buffer"]
        assert "pending_moments" in data["buffer"]

    def test_health_response_has_bible_connectivity(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert "bible_connectivity" in data
        assert "reachable" in data["bible_connectivity"]
        assert "latency_ms" in data["bible_connectivity"]

    def test_health_response_has_sqlite_structure(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert "sqlite" in data
        assert "integrity" in data["sqlite"]
        assert "schema_version" in data["sqlite"]
        assert "size_bytes" in data["sqlite"]

    # Phase 1a — real SQLite data ────────────────────────────────────────

    def test_sqlite_schema_version_is_real_not_hardcoded(self, daemon_url):
        """Phase 1a: schema_version must reflect actual migration state.

        Before Phase 1a this was hardcoded to 0. After Phase 1a, the daemon
        lazy-inits SQLite on first health check — schema_version should be >= 1.
        """
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert data["sqlite"]["schema_version"] >= 1, (
            f"schema_version is {data['sqlite']['schema_version']} — "
            "expected >= 1 (Phase 1a migration should have run)"
        )

    def test_sqlite_integrity_is_ok_after_migration(self, daemon_url):
        """Phase 1a: sqlite.integrity should be 'ok' after successful migration."""
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert data["sqlite"]["integrity"] == "ok", (
            f"sqlite.integrity is {data['sqlite']['integrity']!r} — expected 'ok'"
        )

    def test_sqlite_size_bytes_is_positive(self, daemon_url):
        """Phase 1a: a migrated database must have non-zero size."""
        r = httpx.get(f"{daemon_url}/daemon/health")
        data = r.json()
        assert data["sqlite"]["size_bytes"] > 0, (
            f"sqlite.size_bytes is {data['sqlite']['size_bytes']} — "
            "expected > 0 after migration creates tables"
        )
