"""Unit tests for scripts/daemon.py ctl — --debug must enable debug endpoints."""

from __future__ import annotations

import httpx


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"pid": 123}


def _install_stubs(monkeypatch, tmp_path, daemon_cli):
    """Stub log path, local HTTP client, and Popen; return captured Popen kwargs."""
    monkeypatch.setattr(daemon_cli, "_resolve_log_path", lambda config: tmp_path / "daemon.log")

    calls = {"n": 0}

    def fake_local_client(timeout):
        class _Client:
            def get(self, url):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise httpx.ConnectError("refused")
                return _FakeResponse()

        return _Client()

    monkeypatch.setattr(daemon_cli, "_local_client", fake_local_client)

    captured: dict = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(daemon_cli.subprocess, "Popen", fake_popen)
    return captured


class TestDoStartDebugEnv:
    def test_debug_sets_bible_cc_debug_env(self, tmp_path, monkeypatch):
        import bible_cc_plugin.scripts.daemon as daemon_cli

        monkeypatch.delenv("BIBLE_CC_DEBUG", raising=False)
        captured = _install_stubs(monkeypatch, tmp_path, daemon_cli)

        daemon_cli._do_start(9777, object(), debug=True)

        assert captured["env"]["BIBLE_CC_DEBUG"] == "1"
        # Other environment variables are inherited, not dropped
        assert "PATH" in captured["env"]

    def test_no_debug_leaves_env_untouched(self, tmp_path, monkeypatch):
        import bible_cc_plugin.scripts.daemon as daemon_cli

        monkeypatch.delenv("BIBLE_CC_DEBUG", raising=False)
        captured = _install_stubs(monkeypatch, tmp_path, daemon_cli)

        daemon_cli._do_start(9777, object(), debug=False)

        assert "BIBLE_CC_DEBUG" not in captured["env"]

    def test_existing_env_is_preserved_when_debug_off(self, tmp_path, monkeypatch):
        import bible_cc_plugin.scripts.daemon as daemon_cli

        monkeypatch.setenv("BIBLE_CC_DEBUG", "1")
        captured = _install_stubs(monkeypatch, tmp_path, daemon_cli)

        daemon_cli._do_start(9777, object(), debug=False)

        # Explicit operator env still wins — do not strip it.
        assert captured["env"]["BIBLE_CC_DEBUG"] == "1"
