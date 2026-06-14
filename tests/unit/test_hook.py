"""2a.1: unit tests for hook bridge action handlers."""

import argparse
import json
from unittest.mock import MagicMock, patch

import httpx


class TestHookSessionStart:
    """Verify session-start handler behaviour."""

    def test_calls_session_start_endpoint(self, monkeypatch):
        """session-start → POST /session/start with correct body."""
        calls = []

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"session_id": "abc-123", "is_new": True}
            def raise_for_status(self): pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.ensure_daemon_started",
            lambda *a, **kw: True,
        )

        from bible_cc_plugin.scripts.hook import _handle_session_start

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message=None)

        with patch("builtins.print"):
            _handle_session_start(config, args)

        session_start_calls = [c for c in calls if "/session/start" in c[1]]
        assert len(session_start_calls) == 1
        assert session_start_calls[0][2] == {"session_id": "abc-123"}

    def test_calls_context_inject_endpoint(self, monkeypatch):
        """session-start → POST /context/inject with correct body."""
        calls = []

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"context": "<relevant-memories></relevant-memories>"}
            def raise_for_status(self): pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.ensure_daemon_started",
            lambda *a, **kw: True,
        )

        from bible_cc_plugin.scripts.hook import _handle_session_start

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_session_start(config, args)

        inject_calls = [c for c in calls if "/context/inject" in c[1]]
        assert len(inject_calls) == 1
        assert inject_calls[0][2] == {
            "session_id": "abc-123",
            "user_message": "hello",
        }

    def test_graceful_degradation_on_daemon_unreachable(self, monkeypatch):
        """session-start → daemon fails → print warning."""
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.ensure_daemon_started",
            lambda *a, **kw: False,
        )

        from bible_cc_plugin.scripts.hook import _handle_session_start

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message=None)

        prints = []
        with patch("builtins.print", side_effect=lambda *a, **kw: prints.append(a)):
            _handle_session_start(config, args)

        assert any("failed to start" in str(p) for p in prints)


class TestHookTurnUser:
    """Verify turn-user handler behaviour."""

    def test_calls_turn_endpoint(self, monkeypatch):
        """turn-user → POST /turn/user with correct body."""
        calls = []

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"turn_id": 1, "queued": False}
            def raise_for_status(self): pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_turn_user

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message="hello world")

        with patch("builtins.print"):
            _handle_turn_user(config, args)

        assert len(calls) == 1
        assert calls[0][2] == {"session_id": "abc-123", "message": "hello world"}

    def test_graceful_skip_when_daemon_unreachable(self, monkeypatch):
        """turn-user → httpx.ConnectError → no raise."""
        def fake_post(*a, **kw):
            raise httpx.ConnectError("connection refused")

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_turn_user

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_turn_user(config, args)  # should NOT raise


class TestHookTurnTool:
    """Verify turn-tool handler behaviour."""

    def test_sends_full_output(self, monkeypatch):
        """turn-tool → POST /turn/tool with full tool_output."""
        calls = []

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"turn_id": 2, "queued": False}
            def raise_for_status(self): pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(
            session_id="abc-123",
            tool="Bash",
            input=json.dumps({"command": "pytest"}),
            output="All tests passed.",
        )

        with patch("builtins.print"):
            _handle_turn_tool(config, args)

        assert len(calls) == 1
        body = calls[0][2]
        assert body["session_id"] == "abc-123"
        assert body["tool_name"] == "Bash"
        assert body["arguments"] == {"command": "pytest"}
        assert body["output"] == "All tests passed."

    def test_input_parse_failure_falls_back_to_empty_dict(self, monkeypatch):
        """turn-tool → invalid JSON input → arguments={}."""
        calls = []

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"turn_id": 3, "queued": False}
            def raise_for_status(self): pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(
            session_id="abc-123",
            tool="Bash",
            input="not valid json {{{",
            output="",
        )

        with patch("builtins.print"):
            _handle_turn_tool(config, args)

        assert len(calls) == 1
        assert calls[0][2]["arguments"] == {}

    def test_graceful_skip_when_daemon_unreachable(self, monkeypatch):
        """turn-tool → httpx.ConnectError → no raise."""
        def fake_post(*a, **kw):
            raise httpx.ConnectError("connection refused")

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(
            session_id="abc-123", tool="Bash", input=None, output="",
        )

        with patch("builtins.print"):
            _handle_turn_tool(config, args)  # should NOT raise


class TestHookSessionEnd:
    """Verify session-end handler behaviour."""

    def test_calls_session_end_endpoint(self, monkeypatch):
        """session-end → POST /session/end with correct body."""
        calls = []

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"status": "completed", "detection": None}
            def raise_for_status(self): pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_session_end

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123")

        with patch("builtins.print"):
            _handle_session_end(config, args)

        assert len(calls) == 1
        assert calls[0][2] == {"session_id": "abc-123"}

    def test_graceful_skip_when_daemon_unreachable(self, monkeypatch):
        """session-end → httpx.ConnectError → no raise."""
        def fake_post(*a, **kw):
            raise httpx.ConnectError("connection refused")

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_session_end

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123")

        with patch("builtins.print"):
            _handle_session_end(config, args)  # should NOT raise
