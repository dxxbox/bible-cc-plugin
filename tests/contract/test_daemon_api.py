"""Contract tests for daemon HTTP API — Phase 1b endpoints.

Verifies response JSON schema matches 02-interfaces.md spec.
Contract tests are black-box — they only use HTTP, never import buffer.py.
"""

from __future__ import annotations

import httpx
import pytest

from tests.contract.conftest import terminate_process


@pytest.fixture(scope="module")
def daemon_url(contract_daemon_env):
    """Start daemon on a dynamic port, yield the base URL, stop after tests."""
    import subprocess
    import sys

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
    import time

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


class TestSessionStartContract:
    """POST /session/start response schema (02-interfaces.md §1.2)."""

    def test_response_has_session_id_and_is_new(self, daemon_url):
        r = httpx.post(f"{daemon_url}/session/start", json={"session_id": "ct-s1"})
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == "ct-s1"
        assert isinstance(data["is_new"], bool)
        assert "recovery" in data

    def test_invalid_body_returns_422(self, daemon_url):
        r = httpx.post(f"{daemon_url}/session/start", json={})
        assert r.status_code in (400, 422)


class TestSessionEndContract:
    """POST /session/end response schema (02-interfaces.md §1.2)."""

    def test_response_has_status_and_moments_flushed(self, daemon_url):
        httpx.post(f"{daemon_url}/session/start", json={"session_id": "ct-e1"})
        r = httpx.post(f"{daemon_url}/session/end", json={"session_id": "ct-e1"})
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert isinstance(data["moments_flushed"], int)


class TestTurnUserContract:
    """POST /turn/user response schema (02-interfaces.md §1.3)."""

    def test_response_has_turn_id_and_queued(self, daemon_url):
        httpx.post(f"{daemon_url}/session/start", json={"session_id": "ct-t1"})
        r = httpx.post(
            f"{daemon_url}/turn/user",
            json={"session_id": "ct-t1", "message": "hello"},
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["turn_id"], int)
        assert isinstance(data["queued"], bool)


class TestTurnToolContract:
    """POST /turn/tool response schema (02-interfaces.md §1.3)."""

    def test_response_has_turn_id_and_queued(self, daemon_url):
        httpx.post(f"{daemon_url}/session/start", json={"session_id": "ct-t2"})
        r = httpx.post(
            f"{daemon_url}/turn/tool",
            json={
                "session_id": "ct-t2",
                "tool_name": "test",
                "arguments": {},
                "output": "result",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["turn_id"], int)
        assert isinstance(data["queued"], bool)


class TestSessionsListContract:
    """GET /daemon/sessions response schema."""

    def test_returns_list(self, daemon_url):
        r = httpx.get(f"{daemon_url}/daemon/sessions")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)


class TestErrorFormatContract:
    """All error responses must follow the structured format (02-interfaces.md §1.6)."""

    def test_session_start_invalid_body_has_detail(self, daemon_url):
        r = httpx.post(f"{daemon_url}/session/start", json={})
        assert r.status_code in (400, 422)
        data = r.json()
        assert "error" in data
        assert data["error"]["code"] == "BAD_REQUEST"
        assert "session_id" in data["error"]["message"]

    def test_session_end_missing_id_has_detail(self, daemon_url):
        r = httpx.post(f"{daemon_url}/session/end", json={})
        assert r.status_code in (400, 422)
        data = r.json()
        assert "error" in data
        assert data["error"]["code"] == "BAD_REQUEST"
        assert "session_id" in data["error"]["message"]


class TestContextInjectContract:
    """POST /context/inject response schema (02-interfaces.md §1.4)."""

    def test_response_has_context_and_sources(self, daemon_url):
        httpx.post(f"{daemon_url}/session/start", json={"session_id": "ct-ci1"})
        r = httpx.post(
            f"{daemon_url}/context/inject",
            json={"session_id": "ct-ci1", "user_message": "hello"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "context" in data
        assert "sources" in data
        assert "turns" in data["sources"]
        assert "moments" in data["sources"]
        assert "crash_recovery" in data["sources"]
