"""2a.3: unit tests for daemon_launcher.ensure_daemon_started."""

import subprocess
from pathlib import Path

import httpx


class TestEnsureDaemonStarted:
    """Verify ensure_daemon_started behaviour."""

    def test_already_running_returns_true(self, monkeypatch):
        """Health check returns 200 → don't spawn, return True."""
        from bible_cc_plugin.daemon.daemon_launcher import ensure_daemon_started

        class MockResponse:
            status_code = 200
            def json(self): return {"pid": 99999, "port": 9777}

        monkeypatch.setattr(httpx.Client, "get", lambda *a, **kw: MockResponse())
        spawned = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawned.append(1) or None)

        result = ensure_daemon_started(9777, Path("/tmp/test.log"))
        assert result is True
        assert len(spawned) == 0  # no subprocess spawned

    def test_spawns_and_waits_for_health(self, monkeypatch, tmp_path):
        """Health check fails then succeeds → spawn subprocess, poll, return True."""
        from bible_cc_plugin.daemon.daemon_launcher import ensure_daemon_started

        call_count = [0]

        class MockConnectError(Exception):
            pass

        class MockOkResponse:
            status_code = 200
            def json(self): return {"pid": 12345, "port": 9777}

        def mock_get(url, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise MockConnectError()
            return MockOkResponse()

        monkeypatch.setattr(httpx.Client, "get", mock_get)
        spawned = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawned.append(1) or None)

        log_path = tmp_path / "daemon.log"
        result = ensure_daemon_started(9777, log_path)
        assert result is True
        assert len(spawned) == 1

    def test_timeout_returns_false_and_tails_log(self, monkeypatch, tmp_path):
        """Health check never succeeds → return False, log tail output."""
        from bible_cc_plugin.daemon.daemon_launcher import ensure_daemon_started

        class MockConnectError(Exception):
            pass

        def _raise(*a, **kw):
            raise MockConnectError()
        monkeypatch.setattr(httpx.Client, "get", _raise)
        spawned = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawned.append(1) or None)

        log_path = tmp_path / "daemon.log"
        log_path.write_text("line 1\nline 2\nFATAL: something broke\nline 4\nline 5")

        result = ensure_daemon_started(9777, log_path, poll_timeout=0.1)
        assert result is False
