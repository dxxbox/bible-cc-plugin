"""Contract test for GET /daemon/health — verifies response schema matches 02-interfaces.md §1.1.

Phase 1a: verifies that SQLite data is real (no longer hardcoded zeros).
"""

import subprocess
import sys
import time

import httpx
import pytest

from tests.contract.conftest import terminate_process


@pytest.fixture(scope="module")
def daemon_url(contract_daemon_env):
    """Start daemon on a dynamic port, yield the base URL, stop after tests."""
    port = _find_free_port()
    env = contract_daemon_env | {"BIBLE_CC_DAEMON_PORT": str(port)}
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
        env=env,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url, timeout=5)
        yield base_url
    finally:
        terminate_process(proc)


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


def _wait_for_connectivity(base_url: str, timeout: float = 10.0) -> dict:
    """Poll health until the first background probe has populated the cache."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/daemon/health", timeout=2)
            if r.status_code == 200:
                last = r.json()["bible_connectivity"]
                if last.get("reachable") is not None:
                    return last
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError(f"connectivity not resolved within {timeout}s: {last}")


def _start_daemon(env: dict, port: int) -> subprocess.Popen:
    return subprocess.Popen(
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
        env=env,
    )


@pytest.fixture(scope="module")
def unreachable_daemon_url(contract_daemon_env):
    """Daemon pointed at an unused BiBLE port — reachable must be False.

    Shares ``contract_daemon_env`` with ``daemon_url`` (accepted trade-off:
    health tests only read, and migrations are idempotent).
    """
    port = _find_free_port()
    dead_port = _find_free_port()
    env = contract_daemon_env | {
        "BIBLE_CC_DAEMON_PORT": str(port),
        "BIBLE_ATLAS_BASE_URL": f"http://127.0.0.1:{dead_port}",
    }
    proc = _start_daemon(env, port)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url, timeout=5)
        yield base_url
    finally:
        terminate_process(proc)


@pytest.fixture(scope="module")
def debug_daemon_url(contract_daemon_env):
    """Daemon started with BIBLE_CC_DEBUG=1 — debug endpoints must be live."""
    port = _find_free_port()
    env = contract_daemon_env | {
        "BIBLE_CC_DAEMON_PORT": str(port),
        "BIBLE_CC_DEBUG": "1",
    }
    proc = _start_daemon(env, port)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url, timeout=5)
        yield base_url
    finally:
        terminate_process(proc)


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


# ---------------------------------------------------------------------------
# Phase 3c — connectivity + diagnostic endpoints
# ---------------------------------------------------------------------------


class TestDaemonHealthConnectivity:
    def test_connectivity_resolves_after_first_probe(self, daemon_url):
        conn = _wait_for_connectivity(daemon_url)
        assert isinstance(conn["reachable"], bool)
        assert "base_url" in conn
        assert "checked_at" in conn
        if not conn["reachable"]:
            assert conn["latency_ms"] is None
            assert conn["error"]

    def test_health_200_when_bible_unreachable(self, unreachable_daemon_url):
        r = httpx.get(f"{unreachable_daemon_url}/daemon/health")
        assert r.status_code == 200

        conn = _wait_for_connectivity(unreachable_daemon_url)
        assert conn["reachable"] is False
        assert conn["latency_ms"] is None
        assert conn["error"]
        assert conn["base_url"].startswith("http://127.0.0.1:")


class TestPhase3cDebugEndpointsContract:
    def test_bible_requests_returns_list(self, debug_daemon_url):
        r = httpx.get(f"{debug_daemon_url}/daemon/debug/bible-requests?limit=5")
        assert r.status_code == 200
        assert isinstance(r.json()["requests"], list)

    def test_flush_log_unknown_session_returns_empty(self, debug_daemon_url):
        r = httpx.get(f"{debug_daemon_url}/daemon/debug/flush-log?session_id=none")
        assert r.status_code == 200
        assert r.json() == {"session_id": "none", "flushes": []}

    def test_debug_endpoints_404_without_debug_env(self, daemon_url):
        assert httpx.get(f"{daemon_url}/daemon/debug/bible-requests").status_code == 404
        assert httpx.get(f"{daemon_url}/daemon/debug/flush-log?session_id=x").status_code == 404
