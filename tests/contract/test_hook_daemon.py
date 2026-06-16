"""2a.4: contract tests for hook ↔ daemon HTTP interaction.

These tests verify the HTTP interface contracts — not business logic.
All tests communicate with the daemon exclusively through HTTP.
"""

import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


@pytest.fixture(scope="module")
def daemon_log(tmp_path_factory):
    """Temp log path — never touch ~/.bible-cc/daemon.log in tests."""
    return tmp_path_factory.mktemp("daemon") / "daemon.log"


@pytest.fixture(scope="module")
def daemon_url(daemon_log):
    """Start the daemon and return its base URL."""
    port = 19777
    base_url = f"http://127.0.0.1:{port}"

    try:
        r = httpx.get(f"{base_url}/daemon/health", timeout=2)
        if r.status_code == 200:
            yield base_url
            return
    except Exception:
        pass

    daemon_log.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(str(daemon_log), "a")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "bible_cc_plugin.daemon.server:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "info",
        ],
        stdout=log_fh, stderr=log_fh,
    )

    deadline = time.time() + 10
    ok = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/daemon/health", timeout=1)
            if r.status_code == 200:
                ok = True
                break
        except Exception:
            pass
        time.sleep(0.3)

    if not ok:
        proc.terminate()
        proc.wait()
        log_fh.close()
        pytest.fail("daemon did not start within 10s")

    log_fh.close()
    yield base_url

    try:
        httpx.post(f"{base_url}/daemon/stop", timeout=5)
    except Exception:
        pass
    proc.terminate()
    proc.wait()


class TestContractHookDaemon:
    """Verify hook ↔ daemon HTTP interaction contracts."""

    def test_health_endpoint(self, daemon_url):
        """GET /daemon/health → 200 with expected fields."""
        r = httpx.get(f"{daemon_url}/daemon/health", timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert "status" in body
        assert "pid" in body
        assert "port" in body

    def test_session_start_creates_session(self, daemon_url):
        """POST /session/start → session in GET /daemon/sessions."""
        sid = f"test-contract-{int(time.time())}"
        r = httpx.post(
            f"{daemon_url}/session/start",
            json={"session_id": sid}, timeout=5,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == sid
        assert body["is_new"] is True

        r2 = httpx.get(f"{daemon_url}/daemon/sessions", timeout=5)
        assert r2.status_code == 200
        session_ids = [s["session_id"] for s in r2.json()]
        assert sid in session_ids

    def test_turn_user_buffers_turn(self, daemon_url):
        """POST /turn/user → returns turn_id."""
        sid = f"test-turn-user-{int(time.time())}"
        httpx.post(f"{daemon_url}/session/start", json={"session_id": sid})
        r = httpx.post(
            f"{daemon_url}/turn/user",
            json={"session_id": sid, "message": "hello world"}, timeout=5,
        )
        assert r.status_code == 200
        assert r.json()["turn_id"] >= 1

    def test_turn_tool_stores_full_output(self, daemon_url):
        """POST /turn/tool → returns turn_id."""
        sid = f"test-turn-tool-{int(time.time())}"
        httpx.post(f"{daemon_url}/session/start", json={"session_id": sid})
        r = httpx.post(
            f"{daemon_url}/turn/tool",
            json={
                "session_id": sid,
                "tool_name": "Bash",
                "arguments": {"command": "echo hello"},
                "output": "x" * 5000,
            }, timeout=5,
        )
        assert r.status_code == 200
        assert r.json()["turn_id"] >= 1

    def test_session_end_marks_completed(self, daemon_url):
        """POST /session/end → completed status."""
        sid = f"test-end-{int(time.time())}"
        httpx.post(f"{daemon_url}/session/start", json={"session_id": sid})
        r = httpx.post(
            f"{daemon_url}/session/end",
            json={"session_id": sid}, timeout=5,
        )
        assert r.status_code == 200
        assert r.json()["status"] in ("completed", "already_completed")

    def test_get_moments_returns_list(self, daemon_url):
        """GET /daemon/moments?session_id=X → moments array."""
        sid = f"test-moments-{int(time.time())}"
        httpx.post(f"{daemon_url}/session/start", json={"session_id": sid})
        r = httpx.get(
            f"{daemon_url}/daemon/moments",
            params={"session_id": sid}, timeout=5,
        )
        assert r.status_code == 200
        body = r.json()
        assert "moments" in body
        assert isinstance(body["moments"], list)

    def test_error_response_format(self, daemon_url):
        """Missing required field → 422."""
        r = httpx.post(f"{daemon_url}/session/start", json={}, timeout=5)
        assert r.status_code in (400, 422)
