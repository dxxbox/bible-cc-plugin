"""2a.1: unit tests for hook bridge action handlers."""

import argparse
import json
from unittest.mock import MagicMock, patch

import httpx


class TestHookSessionStart:
    """Verify session-start handler behaviour."""

    def test_startup_event_starts_daemon_only(self, monkeypatch, caplog):
        """startup event (no session_id) → starts daemon, skips session ops."""
        import logging

        daemon_started = [False]
        http_calls = []

        def track_daemon_start(*a, **kw):
            daemon_started[0] = True
            return True

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.ensure_daemon_started",
            track_daemon_start,
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"session_id": "abc-123", "is_new": True}

            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            http_calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_session_start

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id=None, message=None)

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        with patch("builtins.print"):
            _handle_session_start(config, args)
        root.propagate = False

        assert daemon_started[0], "ensure_daemon_started should have been called"
        assert len(http_calls) == 0, "no HTTP calls expected on startup"
        assert any(
            "session-start missing --session-id" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        ), "should log WARNING (not ERROR) for missing session-id on startup"

    def test_calls_session_start_endpoint(self, monkeypatch):
        """session-start → POST /session/start with correct body."""
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"session_id": "abc-123", "is_new": True}

            def raise_for_status(self):
                pass

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
        assert session_start_calls[0][2] == {
            "session_id": "abc-123",
            "reset_threshold": False,
        }

    def test_clear_session_start_resets_threshold(self, monkeypatch):
        """SessionStart source=clear asks daemon to reset threshold counters."""
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"session_id": "abc-123", "is_new": False}

            def raise_for_status(self):
                pass

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
        args = argparse.Namespace(session_id="abc-123", message=None, source="clear")

        with patch("builtins.print"):
            _handle_session_start(config, args)

        session_start_calls = [c for c in calls if "/session/start" in c[1]]
        assert session_start_calls[0][2] == {
            "session_id": "abc-123",
            "reset_threshold": True,
        }

    def test_calls_context_inject_endpoint(self, monkeypatch):
        """session-start → POST /context/inject with correct body."""
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"context": "<relevant-memories></relevant-memories>"}

            def raise_for_status(self):
                pass

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

    def test_graceful_degradation_on_daemon_unreachable(self, monkeypatch, caplog):
        """session-start → daemon fails → log warning."""
        import logging

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.ensure_daemon_started",
            lambda *a, **kw: False,
        )

        from bible_cc_plugin.scripts.hook import _handle_session_start

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message=None)

        # Bible_cc logger has propagate=False — temporarily enable for caplog
        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        with patch("builtins.print"):  # suppress protocol print(context)
            _handle_session_start(config, args)
        root.propagate = False

        assert any("failed to start" in r.message for r in caplog.records)


class TestHookTurnUser:
    """Verify turn-user handler behaviour."""

    def test_calls_turn_endpoint(self, monkeypatch):
        """turn-user → POST /turn/user with correct body."""
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 1, "queued": False}

            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_turn_user

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False
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

    def test_capture_disabled_skips_hint_polling(self, monkeypatch):
        """turn-user should not poll hints when capture is disabled."""
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 1, "queued": False}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: calls.append(a),
        )

        from bible_cc_plugin.scripts.hook import _handle_turn_user

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False
        config.capture.mid_session_detection = True
        args = argparse.Namespace(session_id="abc-123", message="hello")

        _handle_turn_user(config, args)

        assert calls == []

    def test_queued_turn_creates_hint_watch(self, monkeypatch, tmp_path):
        """turn-user queued=true probes silently via _probe_hints, writes hint_watch."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 1, "queued": True}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._probe_hints",
            lambda *a, **kw: 0,
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda sid: tmp_path / f".hint_watch_{sid}",
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = True
        config.capture.mid_session_detection = True
        config.capture.hint_format = "quote_with_command"
        args = argparse.Namespace(session_id="abc-123", message="hello")

        _handle_turn_user(config, args)

        assert (tmp_path / ".hint_watch_abc-123").exists()

    def test_turn_user_probes_silently_leaves_emission_to_stop(self, monkeypatch, tmp_path):
        """turn-user now uses _probe_hints — emission is Stop's job exclusively."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        probe_calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 1, "queued": True}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._probe_hints",
            lambda *a, **kw: probe_calls.append(1) or 1,
        )
        print_hints_called = []
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: print_hints_called.append(1) or 1,
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda sid: tmp_path / f".hint_watch_{sid}",
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = True
        config.capture.mid_session_detection = True
        config.capture.hint_format = "quote_with_command"
        args = argparse.Namespace(session_id="abc-123", message="hello")

        _handle_turn_user(config, args)

        # _probe_hints was called (silent probe), _print_hints was NOT
        assert len(probe_calls) == 1
        assert len(print_hints_called) == 0
        # hint_watch is still written so Stop knows to wait
        assert (tmp_path / ".hint_watch_abc-123").exists()

    def test_safety_net_delivers_on_expired_watch(self, monkeypatch, tmp_path):
        """When hint_watch is expired and cursor not advanced, safety net delivers."""
        import time as _time

        from bible_cc_plugin.scripts.hook import _handle_turn_user

        sid = "safety-net-1"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda s: tmp_path / f".hint_cursor_{s}",
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda s: tmp_path / f".hint_watch_{s}",
        )
        # Write expired watch
        watch_path = tmp_path / f".hint_watch_{sid}"
        watch_path.write_text(
            json.dumps({"cursor": 0, "expires_at": _time.time() - 10})
        )

        print_calls = []
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: print_calls.append(kw.get("hook_event_name")) or 1,
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._probe_hints",
            lambda *a, **kw: 0,
        )

        class FakeResponse:
            status_code = 200
            def json(self): return {"turn_id": 1, "queued": False}
            def raise_for_status(self): pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = True
        config.capture.mid_session_detection = True
        config.capture.hint_format = "quote_with_command"
        args = argparse.Namespace(session_id=sid, message="hello")

        _handle_turn_user(config, args)

        assert len(print_calls) == 1
        assert print_calls[0] == "UserPromptSubmit"
        assert not watch_path.exists()

    def test_safety_net_skips_when_watch_active(self, monkeypatch, tmp_path):
        """When hint_watch is NOT expired, safety net leaves it for Stop hook."""
        import time as _time

        from bible_cc_plugin.scripts.hook import _handle_turn_user

        sid = "safety-net-2"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda s: tmp_path / f".hint_cursor_{s}",
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda s: tmp_path / f".hint_watch_{s}",
        )
        # Write watch still active (far future)
        watch_path = tmp_path / f".hint_watch_{sid}"
        watch_path.write_text(
            json.dumps({"cursor": 0, "expires_at": _time.time() + 60})
        )

        print_calls = []
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: print_calls.append(1) or 1,
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._probe_hints",
            lambda *a, **kw: 0,
        )

        class FakeResponse:
            status_code = 200
            def json(self): return {"turn_id": 1, "queued": False}
            def raise_for_status(self): pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = True
        config.capture.mid_session_detection = True
        config.capture.hint_format = "quote_with_command"
        args = argparse.Namespace(session_id=sid, message="hello")

        _handle_turn_user(config, args)

        assert len(print_calls) == 0
        assert watch_path.exists()


class TestHookTurnTool:
    """Verify turn-tool handler behaviour."""

    def test_sends_full_output(self, monkeypatch):
        """turn-tool → POST /turn/tool with full tool_output."""
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 2, "queued": False}

            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False
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

            def raise_for_status(self):
                pass

        def fake_post(url, json=None, **kwargs):
            calls.append(("post", url, json))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False
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
            session_id="abc-123",
            tool="Bash",
            input=None,
            output="",
        )

        with patch("builtins.print"):
            _handle_turn_tool(config, args)  # should NOT raise

    def test_capture_disabled_skips_hint_polling(self, monkeypatch):
        """turn-tool should not poll hints when capture is disabled."""
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 2, "queued": False}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: calls.append(a),
        )

        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False
        config.capture.mid_session_detection = True
        args = argparse.Namespace(session_id="abc-123", tool="Bash", input=None, output="ok")

        _handle_turn_tool(config, args)

        assert calls == []


class TestHookSessionEnd:
    """Verify session-end handler behaviour."""

    def test_calls_session_end_endpoint(self, monkeypatch):
        """session-end → POST /session/end with correct body."""
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"status": "completed", "detection": None}

            def raise_for_status(self):
                pass

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

    def test_session_not_found_404_logs_clear_message(self, monkeypatch, caplog):
        """404 with 'session not found' → specific log message, no raise."""
        import logging

        def fake_post(*a, **kw):
            resp = MagicMock()
            resp.status_code = 404
            resp.json.return_value = {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "session not found: abc-123",
                }
            }
            resp.text = '{"error":{"code":"NOT_FOUND","message":"session not found: abc-123"}}'
            raise httpx.HTTPStatusError("Not Found", request=MagicMock(), response=resp)

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_session_end

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123")

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        with patch("builtins.print"):
            _handle_session_end(config, args)
        root.propagate = False

        assert any("was never registered" in r.message for r in caplog.records), (
            "should log 'was never registered' for session-not-found 404"
        )

    def test_unknown_404_logs_with_body_detail(self, monkeypatch, caplog):
        """404 without 'session not found' → logs HTTP status + body detail."""
        import logging

        def fake_post(*a, **kw):
            resp = MagicMock()
            resp.status_code = 404
            resp.json.return_value = {"detail": "Not Found"}
            resp.text = '{"detail":"Not Found"}'
            raise httpx.HTTPStatusError("Not Found", request=MagicMock(), response=resp)

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_session_end

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123")

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        with patch("builtins.print"):
            _handle_session_end(config, args)
        root.propagate = False

        assert any("HTTP 404" in r.message for r in caplog.records), (
            "should log HTTP status code for unknown 404"
        )

    def test_server_error_includes_body_detail(self, monkeypatch, caplog):
        """HTTP 500 → logs status code AND body message for debugging."""
        import logging

        def fake_post(*a, **kw):
            resp = MagicMock()
            resp.status_code = 500
            resp.json.return_value = {
                "error": {
                    "code": "INTERNAL",
                    "message": "database connection lost",
                }
            }
            resp.text = '{"error":{"code":"INTERNAL","message":"database connection lost"}}'
            raise httpx.HTTPStatusError("Server Error", request=MagicMock(), response=resp)

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_session_end

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123")

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        with patch("builtins.print"):
            _handle_session_end(config, args)
        root.propagate = False

        assert any(
            "HTTP 500" in r.message and "database connection lost" in r.message
            for r in caplog.records
        ), "should log HTTP 500 AND body message detail"

    def test_request_error_logs_unreachable(self, monkeypatch, caplog):
        """httpx.RequestError (ConnectError/Timeout/NetworkError) → 'unreachable'."""
        import logging

        def fake_post(*a, **kw):
            raise httpx.ConnectError("connection refused")

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", fake_post)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _handle_session_end

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123")

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        with patch("builtins.print"):
            _handle_session_end(config, args)
        root.propagate = False

        assert any("unreachable" in r.message for r in caplog.records), (
            "should log 'unreachable' for transport errors"
        )

    def test_missing_session_id_logs_error(self, caplog):
        """No session_id → error log, no HTTP call."""
        import logging

        from bible_cc_plugin.scripts.hook import _handle_session_end

        config = MagicMock()
        args = argparse.Namespace(session_id=None)

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.ERROR)
        with patch("builtins.print"):
            _handle_session_end(config, args)
        root.propagate = False

        assert any("missing --session-id" in r.message for r in caplog.records), (
            "should log error for missing session-id"
        )


class TestHookTurnStop:
    """Verify turn-stop polls already detected moments for hints."""

    def test_polls_hints_for_session(self, monkeypatch):
        from bible_cc_plugin.scripts.hook import _handle_turn_stop

        calls = []

        def fake_print_hints(session_id, base_url, hint_format, **kwargs):
            calls.append((session_id, base_url, hint_format, kwargs))

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._print_hints", fake_print_hints)

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.hint_format = "quote_with_command"
        args = argparse.Namespace(session_id="abc-123")

        _handle_turn_stop(config, args)

        assert calls[0][3]["wait_seconds"] == 0.0
        assert calls[0][3].get("hook_event_name") == "Stop"

    def test_waits_briefly_when_detection_was_queued(self, monkeypatch, tmp_path):
        from bible_cc_plugin.scripts import hook
        from bible_cc_plugin.scripts.hook import _handle_turn_stop

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda sid: tmp_path / f".hint_watch_{sid}",
        )
        monkeypatch.setattr(hook.time, "time", lambda: 100.0)
        hook._write_hint_watch("abc-123")

        calls = []

        def fake_print_hints(session_id, base_url, hint_format, **kwargs):
            calls.append((session_id, base_url, hint_format, kwargs))
            hook._write_hint_cursor(session_id, 1)
            return 1

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._print_hints", fake_print_hints)

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = True
        config.capture.mid_session_detection = True
        config.capture.hint_format = "quote_with_command"

        _handle_turn_stop(config, argparse.Namespace(session_id="abc-123"))

        assert calls[0][3]["wait_seconds"] > 0
        assert not (tmp_path / ".hint_watch_abc-123").exists()

    def test_stale_watch_does_not_wait_when_cursor_already_advanced(self, monkeypatch, tmp_path):
        from bible_cc_plugin.scripts import hook
        from bible_cc_plugin.scripts.hook import _handle_turn_stop

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda sid: tmp_path / f".hint_watch_{sid}",
        )
        monkeypatch.setattr(hook.time, "time", lambda: 100.0)
        hook._write_hint_watch("abc-123")
        hook._write_hint_cursor("abc-123", 1)

        calls = []

        def fake_print_hints(session_id, base_url, hint_format, **kwargs):
            calls.append((session_id, base_url, hint_format, kwargs))
            return 0

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._print_hints", fake_print_hints)

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = True
        config.capture.mid_session_detection = True
        config.capture.hint_format = "quote_with_command"

        _handle_turn_stop(config, argparse.Namespace(session_id="abc-123"))

        assert calls[0][3]["wait_seconds"] == 0.0
        assert not (tmp_path / ".hint_watch_abc-123").exists()

    def test_stop_posts_last_assistant_message_before_polling(self, monkeypatch):
        from bible_cc_plugin.scripts.hook import _handle_turn_stop

        posts = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 2, "queued": False}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(
            client,
            "post",
            lambda url, json=None, **kw: posts.append((url, json)) or FakeResponse(),
        )
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: 0,
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = True
        config.capture.mid_session_detection = True
        config.capture.hint_format = "quote_with_command"
        args = argparse.Namespace(
            session_id="abc-123",
            message="I checked the API contract and found last_assistant_message.",
        )

        _handle_turn_stop(config, args)

        assert posts[0][0].endswith("/turn/assistant")
        assert posts[0][1] == {
            "session_id": "abc-123",
            "message": "I checked the API contract and found last_assistant_message.",
        }

    def test_stop_assistant_post_uses_tight_timeout(self, monkeypatch):
        from bible_cc_plugin.scripts.hook import _post_assistant_turn

        timeouts = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 2, "queued": False}

            def raise_for_status(self):
                pass

        class FakeClient:
            def post(self, *args, **kwargs):
                return FakeResponse()

        def fake_local_client(timeout=5.0):
            timeouts.append(timeout)
            return FakeClient()

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._local_client", fake_local_client)

        _post_assistant_turn("abc-123", "Done.", "http://127.0.0.1:9777")

        assert timeouts == [1.0]

    def test_stop_assistant_timeout_logs_chars_and_timeout(self, monkeypatch):
        import httpx

        from bible_cc_plugin.scripts.hook import _post_assistant_turn

        messages = []

        def fake_local_client(timeout=5.0):
            raise httpx.TimeoutException("timed out")

        def fake_warning(message, *args):
            messages.append(message % args)

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._local_client", fake_local_client)
        monkeypatch.setattr("bible_cc_plugin.scripts.hook._logger.warning", fake_warning)

        body = _post_assistant_turn("abc-123", "Done.", "http://127.0.0.1:9777")

        assert body == {"queued": False}
        assert any("chars=5" in message for message in messages)
        assert any("timeout=1.0s" in message for message in messages)

    def test_stop_writes_watch_when_assistant_detection_queued(self, monkeypatch, tmp_path):
        from bible_cc_plugin.scripts.hook import _handle_turn_stop

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"turn_id": 8, "queued": True}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "post", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: 0,
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda sid: tmp_path / f".hint_watch_{sid}",
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = True
        config.capture.mid_session_detection = True
        config.capture.hint_format = "quote_with_command"
        args = argparse.Namespace(session_id="abc-123", message="Done.")

        _handle_turn_stop(config, args)

        assert (tmp_path / ".hint_watch_abc-123").exists()

    def test_missing_session_id_does_not_poll(self, monkeypatch, caplog):
        import logging

        from bible_cc_plugin.scripts.hook import _handle_turn_stop

        calls = []
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: calls.append(a),
        )

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        _handle_turn_stop(MagicMock(), argparse.Namespace(session_id=None))
        root.propagate = False

        assert calls == []
        assert any("turn-stop missing --session-id" in r.message for r in caplog.records)

    def test_capture_disabled_does_not_poll(self, monkeypatch):
        from bible_cc_plugin.scripts.hook import _handle_turn_stop

        calls = []
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints",
            lambda *a, **kw: calls.append(a),
        )

        config = MagicMock()
        config.capture.enabled = False
        config.capture.mid_session_detection = True
        args = argparse.Namespace(session_id="abc-123")

        _handle_turn_stop(config, args)

        assert calls == []


class TestPrintHints:
    """Unit tests for _print_hints() — dict → MomentCandidate adapter."""

    def test_formats_daemon_json_with_moment_type_key(self, monkeypatch, capsys, tmp_path):
        """dict with 'moment_type' key → hint emitted as JSON systemMessage."""
        # Trigger anthropic import before monkeypatching httpx.Client.
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints

        session_id = "test-hint-1"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )

        get_calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "moments": [
                        {
                            "id": 1,
                            "moment_type": "decision",
                            "title": "Use Postgres",
                            "narrative": "Chose PostgreSQL for auth storage",
                        }
                    ]
                }

            def raise_for_status(self):
                pass

        def fake_get(url, params=None, **kwargs):
            get_calls.append(("get", url, params))
            return FakeResponse()

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", fake_get)
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        _print_hints(session_id, "http://127.0.0.1:9777", "quote_with_command")

        output = capsys.readouterr().out
        assert "/daemon/moments" in get_calls[0][1]
        payload = json.loads(output)
        assert payload["continue"] is True
        assert "Postgres" in payload["systemMessage"]
        assert "Decision" in payload["systemMessage"]
        assert "⎿ ⏳" in payload["systemMessage"]
        assert "/bible-cc:review" in payload["systemMessage"]

    def test_print_hints_logs_printed_count(self, monkeypatch, capsys, tmp_path, caplog):
        """Hint JSON systemMessage path should be visible in bible-cc logs."""
        import logging

        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints

        session_id = "test-hint-log"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "moments": [
                        {
                            "id": 7,
                            "moment_type": "session_start",
                            "title": "Start 3a",
                            "narrative": "Start Phase 3a.",
                        }
                    ]
                }

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.INFO)
        printed = _print_hints(session_id, "http://127.0.0.1:9777", "quote_only")
        root.propagate = False

        assert printed == 1
        payload = json.loads(capsys.readouterr().out)
        assert "Start 3a" in payload["systemMessage"]
        assert any("_collect_hints: collected" in r.message for r in caplog.records)
        assert any("cursor=0->7" in r.message for r in caplog.records)

    def test_one_bad_moment_does_not_block_subsequent(self, monkeypatch, capsys, tmp_path):
        """Bad moment (None keys) → skipped, next moment still prints."""
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints

        session_id = "test-hint-2"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "moments": [
                        {"id": 1, "moment_type": None, "title": None, "narrative": None},
                        {
                            "id": 2,
                            "moment_type": "accomplishment",
                            "title": "Rate limiting done",
                            "narrative": "Implemented rate limiter",
                        },
                    ]
                }

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        _print_hints(session_id, "http://127.0.0.1:9777", "quote_only")

        payload = json.loads(capsys.readouterr().out)
        assert "Rate limiting done" in payload["systemMessage"]
        # first moment is bad but won't crash; second still appears

    def test_cursor_prevents_duplicate_hints(self, monkeypatch, capsys, tmp_path):
        """Second call with same session_id skips already-hinted moments."""
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints

        session_id = "test-hint-cursor"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )

        call_count = [0]

        class FakeResponse:
            status_code = 200

            def json(self):
                call_count[0] += 1
                return {
                    "moments": [
                        {
                            "id": 1,
                            "moment_type": "decision",
                            "title": "Only once",
                            "narrative": "Only once",
                        },
                    ]
                }

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        _print_hints(session_id, "http://127.0.0.1:9777", "quote_only")
        out1 = capsys.readouterr().out
        payload1 = json.loads(out1)
        assert "Only once" in payload1["systemMessage"], "first call should emit hint"

        _print_hints(session_id, "http://127.0.0.1:9777", "quote_only")
        out2 = capsys.readouterr().out
        assert out2.strip() == "", "second call should skip (no new hints)"
        # cursor should still be 1, no re-emit of already-seen moment

    def test_waits_for_late_async_moment(self, monkeypatch, capsys, tmp_path):
        """Brief polling lets Stop surface a moment inserted after the first GET."""
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints

        session_id = "test-hint-late"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )

        responses = [
            {"moments": []},
            {
                "moments": [
                    {
                        "id": 3,
                        "moment_type": "session_start",
                        "title": "开始 3a 开发",
                        "narrative": "User started Phase 3a development.",
                    }
                ]
            },
        ]

        class FakeResponse:
            status_code = 200

            def json(self):
                return responses.pop(0)

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        printed = _print_hints(
            session_id,
            "http://127.0.0.1:9777",
            "quote_only",
            wait_seconds=0.01,
            poll_interval=0,
        )

        payload = json.loads(capsys.readouterr().out)
        assert printed == 1
        assert "开始 3a 开发" in payload["systemMessage"]
        assert "Session Start" in payload["systemMessage"]

    def test_get_failure_logs_warning_and_returns(self, monkeypatch, caplog):
        """GET /daemon/moments fails → logged, no crash, no hints."""
        import logging

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(
            client,
            "get",
            lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("connection refused")),
        )
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _print_hints

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        _print_hints("abc-123", "http://127.0.0.1:9777", "quote_with_command")
        root.propagate = False

        assert any(
            "_collect_hints: GET /daemon/moments failed" in r.message
            for r in caplog.records
        )

    def test_non_200_response_logs_and_returns(self, monkeypatch, caplog):
        """HTTP 500 from daemon → logged, no crash."""
        import logging

        class FakeResponse:
            status_code = 500

            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "Server Error",
                    request=MagicMock(),
                    response=MagicMock(status_code=500),
                )

            def json(self):
                return {}

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _print_hints

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        _print_hints("abc-123", "http://127.0.0.1:9777", "quote_with_command")
        root.propagate = False

        assert any(
            "_collect_hints: GET /daemon/moments failed" in r.message
            for r in caplog.records
        )


class TestProbeHints:
    """Unit tests for _probe_hints() — silent poll, writes hint_watch, no emit."""

    def test_writes_hint_watch_when_moments_found(self, monkeypatch, tmp_path):
        """_probe_hints writes hint_watch but does NOT emit or advance cursor."""
        from bible_cc_plugin.scripts.hook import _probe_hints

        session_id = "probe-test"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda sid: tmp_path / f".hint_watch_{sid}",
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"moments": [{"id": 1, "moment_type": "decision", "title": "test"}]}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        result = _probe_hints(session_id, "http://127.0.0.1:9777", "quote_with_command")

        assert result == 1  # found 1 moment
        # hint_watch was written
        assert (tmp_path / ".hint_watch_probe-test").exists()
        # cursor was NOT advanced
        assert not (tmp_path / ".hint_cursor_probe-test").exists()

    def test_returns_zero_when_no_new_moments(self, monkeypatch, tmp_path):
        """_probe_hints returns 0 and writes nothing when no moments found."""
        from bible_cc_plugin.scripts.hook import _probe_hints

        session_id = "probe-empty"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda sid: tmp_path / f".hint_watch_{sid}",
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"moments": []}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        result = _probe_hints(session_id, "http://127.0.0.1:9777", "quote_with_command")

        assert result == 0
        assert not (tmp_path / ".hint_watch_probe-empty").exists()

    def test_does_not_print_to_stdout(self, monkeypatch, tmp_path, capsys):
        """_probe_hints is silent — no stdout output."""
        from bible_cc_plugin.scripts.hook import _probe_hints

        session_id = "probe-silent"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_watch_path",
            lambda sid: tmp_path / f".hint_watch_{sid}",
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"moments": [{"id": 1, "moment_type": "decision", "title": "test"}]}

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        _probe_hints(session_id, "http://127.0.0.1:9777", "quote_with_command")

        assert capsys.readouterr().out == ""


class TestCollectHints:
    """Unit tests for _collect_hints() — pure data collection, no side effects."""

    def test_returns_messages_without_printing(self, monkeypatch, capsys, tmp_path):
        """_collect_hints returns hint strings but does NOT write to stdout."""
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _collect_hints

        session_id = "test-collect-1"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "moments": [
                        {
                            "id": 1,
                            "moment_type": "decision",
                            "title": "Use Postgres",
                            "narrative": "Chose PostgreSQL",
                        }
                    ]
                }

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        messages, max_id = _collect_hints(
            session_id, "http://127.0.0.1:9777", "quote_with_command"
        )

        assert len(messages) == 1
        assert "Postgres" in messages[0]
        assert max_id == 1
        # No stdout emitted by _collect_hints
        assert capsys.readouterr().out == ""

    def test_respects_cursor(self, monkeypatch, tmp_path):
        """Moments with id ≤ cursor are excluded from returned messages."""
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _collect_hints, _write_hint_cursor

        session_id = "test-collect-cursor"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )
        # Pre-set cursor so moment id=1 is already consumed
        _write_hint_cursor(session_id, 1)

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "moments": [
                        {
                            "id": 1,
                            "moment_type": "decision",
                            "title": "Already seen",
                            "narrative": "Should be skipped",
                        },
                        {
                            "id": 2,
                            "moment_type": "accomplishment",
                            "title": "New moment",
                            "narrative": "Should be included",
                        },
                    ]
                }

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        messages, max_id = _collect_hints(
            session_id, "http://127.0.0.1:9777", "quote_only"
        )

        assert len(messages) == 1
        assert "New moment" in messages[0]
        assert "Already seen" not in messages[0]
        assert max_id == 2  # max_id across ALL moments, not just new ones

    def test_returns_empty_on_daemon_failure(self, monkeypatch):
        """GET /daemon/moments fails → returns ([], 0)."""
        from bible_cc_plugin.scripts.hook import _collect_hints

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(
            client, "get",
            lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("refused")),
        )
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        messages, max_id = _collect_hints(
            "abc-123", "http://127.0.0.1:9777", "quote_with_command"
        )

        assert messages == []
        assert max_id == 0

    def test_returns_empty_when_no_new_hints(self, monkeypatch, tmp_path):
        """All moments already consumed → ([], 0)."""
        from bible_cc_plugin.scripts.hook import _collect_hints, _write_hint_cursor

        session_id = "test-collect-nonew"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )
        _write_hint_cursor(session_id, 99)

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "moments": [
                        {
                            "id": 1,
                            "moment_type": "decision",
                            "title": "Old",
                            "narrative": "Old",
                        }
                    ]
                }

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        messages, max_id = _collect_hints(
            session_id, "http://127.0.0.1:9777", "quote_only"
        )

        assert messages == []
        assert max_id == 0  # no new hints, no cursor advancement needed


class TestEmitHookMessage:
    """Unit tests for _emit_hook_message() — JSON stdout output."""

    def test_single_hint_emits_json(self, capsys):
        """Single hint → JSON with continue:true + systemMessage."""
        from bible_cc_plugin.scripts.hook import _emit_hook_message

        result = _emit_hook_message(
            ['⎿ ⏳ Captured: "Use Postgres" — Decision.'],
            "UserPromptSubmit",
        )

        assert result is True
        payload = json.loads(capsys.readouterr().out)
        assert payload["continue"] is True
        assert "Use Postgres" in payload["systemMessage"]
        assert "Decision" in payload["systemMessage"]

    def test_multiple_hints_joined_with_newline(self, capsys):
        """Multiple hints → single systemMessage with \\n separator."""
        from bible_cc_plugin.scripts.hook import _emit_hook_message

        messages = [
            '⎿ ⏳ Captured: "Use Postgres" — Decision.',
            '⎿ ⏳ Captured: "Rate limiting done" — Accomplishment.',
        ]
        result = _emit_hook_message(messages, "PostToolUse")

        assert result is True
        stdout = capsys.readouterr().out
        payload = json.loads(stdout)
        assert payload["continue"] is True
        assert "\n" in payload["systemMessage"]
        assert "Use Postgres" in payload["systemMessage"]
        assert "Rate limiting done" in payload["systemMessage"]
        # Single JSON object, not multiple lines of JSON
        lines = stdout.strip().split("\n")
        assert len(lines) == 1

    def test_empty_list_returns_false(self, capsys):
        """Empty messages → returns False, no stdout."""
        from bible_cc_plugin.scripts.hook import _emit_hook_message

        result = _emit_hook_message([], "Stop")

        assert result is False
        assert capsys.readouterr().out == ""

    def test_has_continue_not_decision(self, capsys):
        """Stop hook output must NOT include 'decision' field."""
        from bible_cc_plugin.scripts.hook import _emit_hook_message

        _emit_hook_message(
            ['⎿ ⏳ Captured: "Start 3a" — Session Start.'],
            "Stop",
        )

        payload = json.loads(capsys.readouterr().out)
        assert "continue" in payload
        assert payload["continue"] is True
        assert "decision" not in payload
        assert "systemMessage" in payload

    def test_emit_failure_returns_false(self, monkeypatch):
        """If json.dumps fails → returns False."""
        from bible_cc_plugin.scripts.hook import _emit_hook_message

        # Cause json.dumps to fail (doesn't break logging like print mock would)
        monkeypatch.setattr(
            "json.dumps",
            lambda *a, **kw: (_ for _ in ()).throw(TypeError("not serializable")),
        )

        result = _emit_hook_message(["test hint"], "UserPromptSubmit")

        assert result is False


class TestPrintHintsCursor:
    """Cursor advancement behaviour across the composed _print_hints."""

    def test_cursor_not_advanced_on_emit_failure(self, monkeypatch, tmp_path):
        """If _emit_hook_message fails, cursor stays unchanged."""
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints, _read_hint_cursor

        session_id = "test-cursor-fail"
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._hint_cursor_path",
            lambda sid: tmp_path / f".hint_cursor_{sid}",
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "moments": [
                        {
                            "id": 1,
                            "moment_type": "decision",
                            "title": "T",
                            "narrative": "T",
                        }
                    ]
                }

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        # Make _emit_hook_message fail
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._emit_hook_message",
            lambda *a, **kw: False,
        )

        cursor_before = _read_hint_cursor(session_id)
        printed = _print_hints(session_id, "http://127.0.0.1:9777", "quote_only")
        cursor_after = _read_hint_cursor(session_id)

        assert printed == 0
        assert cursor_before == cursor_after


class TestStdinJsonParsing:
    """Verify main() reads hook event data from stdin JSON."""

    def test_session_start_from_stdin(self, monkeypatch):
        """SessionStart stdin → session_id flows to handler."""
        stdin_json = json.dumps(
            {
                "session_id": "abc-123",
                "hook_event_name": "SessionStart",
            }
        )
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "session-start"])

        def fake_start(config, args):
            called_with["session_id"] = args.session_id

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._handle_session_start", fake_start)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr("bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None)

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "abc-123"

    def test_turn_user_from_stdin(self, monkeypatch):
        """UserPromptSubmit stdin → session_id + prompt."""
        stdin_json = json.dumps(
            {
                "session_id": "def-456",
                "prompt": "hello world",
                "hook_event_name": "UserPromptSubmit",
            }
        )
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "turn-user"])

        def fake_handler(config, args):
            called_with["session_id"] = args.session_id
            called_with["message"] = args.message

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._handle_turn_user", fake_handler)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr("bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None)

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "def-456"
        assert called_with.get("message") == "hello world"

    def test_turn_tool_from_stdin(self, monkeypatch):
        """PostToolUse stdin → session_id + tool_name + input + output."""
        stdin_json = json.dumps(
            {
                "session_id": "ghi-789",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
                "tool_response": "All tests passed.",
            }
        )
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "turn-tool"])

        def fake_handler(config, args):
            called_with["session_id"] = args.session_id
            called_with["tool"] = args.tool
            called_with["input"] = args.input
            called_with["output"] = args.output

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._handle_turn_tool", fake_handler)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr("bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None)

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "ghi-789"
        assert called_with.get("tool") == "Bash"
        assert called_with.get("output") == "All tests passed."
        # tool_input from stdin is serialized to JSON string
        assert called_with.get("input") == '{"command": "pytest"}'

    def test_turn_stop_last_assistant_message_from_stdin(self, monkeypatch):
        """Stop stdin → session_id + last_assistant_message."""
        stdin_json = json.dumps(
            {
                "session_id": "stop-123",
                "hook_event_name": "Stop",
                "last_assistant_message": "I checked the API contract.",
            }
        )
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "turn-stop"])

        def fake_handler(config, args):
            called_with["session_id"] = args.session_id
            called_with["message"] = args.message

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._handle_turn_stop", fake_handler)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr("bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None)

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "stop-123"
        assert called_with.get("message") == "I checked the API contract."

    def test_cli_overrides_stdin(self, monkeypatch):
        """CLI --session-id wins over stdin session_id."""
        stdin_json = json.dumps({"session_id": "from-stdin"})

        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "session-start", "--session-id", "from-cli"])

        def fake_start(config, args):
            called_with["session_id"] = args.session_id

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._handle_session_start", fake_start)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr("bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None)

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "from-cli"

    def test_empty_stdin_graceful(self, monkeypatch):
        """Empty stdin (no pipe) → does not crash."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)  # TTY → skip read
        monkeypatch.setattr("sys.argv", ["hook", "session-start", "--session-id", "tty-test"])

        called_with = {}

        def fake_start(config, args):
            called_with["session_id"] = args.session_id

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._handle_session_start", fake_start)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr("bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None)

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "tty-test"

    def test_startup_empty_session_id_from_stdin(self, monkeypatch):
        """startup event: stdin has empty session_id → handler sees empty string."""
        stdin_json = json.dumps(
            {
                "session_id": "",
                "hook_event_name": "SessionStart",
            }
        )
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "session-start"])

        def fake_start(config, args):
            called_with["session_id"] = args.session_id

        monkeypatch.setattr("bible_cc_plugin.scripts.hook._handle_session_start", fake_start)
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr("bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None)

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == ""


# ══════════════════════════════════════════════════════════════════════════════
# Self-healing recovery tests (turn-user / turn-tool)
# ══════════════════════════════════════════════════════════════════════════════


class _FakeResponse:
    """A minimal httpx.Response stand-in for error body parsing tests."""

    def __init__(self, status_code: int, body: dict | str):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("not JSON")

    @property
    def text(self) -> str:
        if isinstance(self._body, dict):
            return json.dumps(self._body)
        return self._body


class _HTTPStatusError(httpx.HTTPStatusError):
    """Minimal httpx.HTTPStatusError stand-in with custom response body."""

    def __init__(self, status_code: int = 400, body: dict | str = ""):
        self._response = _FakeResponse(status_code, body)
        self._request = httpx.Request("POST", "http://127.0.0.1:9777/")
        super().__init__(
            f"Client error '{status_code}'",
            request=self._request,
            response=self._response,
        )


def _make_ok_response():
    """Return a minimal success response object for /session/start and /turn/*."""

    class Ok:
        status_code = 200

        def json(self):
            return {"turn_id": 42, "is_new": True, "queued": False}

        def raise_for_status(self):
            pass

    return Ok()


class _SequencedClient:
    """Fake client that yields responses/exceptions from a queue."""

    def __init__(self, sequence: list):
        self._seq = sequence
        self._i = 0
        self.calls: list[tuple[str, dict | None]] = []

    def post(self, url, json=None, **kw):
        self.calls.append((url, json))
        if self._i >= len(self._seq):
            raise RuntimeError(f"unexpected post #{self._i}: {url}")
        item = self._seq[self._i]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kw):
        class Ok:
            status_code = 200

            def json(self):
                return {"moments": []}

            def raise_for_status(self):
                pass

        return Ok()


class TestHookSelfHealing:
    """Turn hooks: 400 'session not found' → auto-recover → retry."""

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _session_not_found_400():
        return _HTTPStatusError(
            400, {"error": {"code": "BAD_REQUEST", "message": "session not found: abc-123"}}
        )

    @staticmethod
    def _session_completed_400():
        return _HTTPStatusError(
            400, {"error": {"code": "BAD_REQUEST", "message": "session abc-123 is completed"}}
        )

    @staticmethod
    def _fastapi_detail_400():
        return _HTTPStatusError(400, {"detail": "session not found: abc-123"})

    @staticmethod
    def _raw_html_400():
        return _HTTPStatusError(400, "<html>502 Bad Gateway</html>")

    # ── turn-user ─────────────────────────────────────────────────────

    def test_user_recovery_succeeds(self, monkeypatch):
        """400 'session not found' → /session/start → retry turn OK."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        client = _SequencedClient(
            [
                self._session_not_found_400(),
                _make_ok_response(),  # /session/start success
                _make_ok_response(),  # retry turn success
            ]
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False  # skip hint polling
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_turn_user(config, args)

        assert len(client.calls) == 3
        assert client.calls[0][0] == "http://127.0.0.1:9777/turn/user"
        assert client.calls[1][0] == "http://127.0.0.1:9777/session/start"
        assert client.calls[2][0] == "http://127.0.0.1:9777/turn/user"

    def test_user_recovery_fails_gracefully(self, monkeypatch):
        """recovery /session/start itself fails → turn skipped, no crash."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        client = _SequencedClient(
            [
                self._session_not_found_400(),
                _HTTPStatusError(500, {"error": {"code": "INTERNAL_ERROR", "message": "db error"}}),
            ]
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_turn_user(config, args)  # should NOT raise

        assert len(client.calls) == 2  # turn → recovery (failed)

    def test_user_retry_fails_after_recovery(self, monkeypatch):
        """recovery OK but turn retry still 400 → graceful skip."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        client = _SequencedClient(
            [
                self._session_not_found_400(),
                _make_ok_response(),  # /session/start OK
                self._session_completed_400(),  # retry fails with different 400
            ]
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_turn_user(config, args)

        assert len(client.calls) == 3

    def test_user_session_completed_no_recovery(self, monkeypatch):
        """400 'session X is completed' → log, NO recovery attempt."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        client = _SequencedClient([self._session_completed_400()])
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_turn_user(config, args)

        assert len(client.calls) == 1  # no recovery attempted

    def test_user_malformed_body_no_recovery(self, monkeypatch):
        """Non-JSON 400 body → log, no crash, no recovery."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        client = _SequencedClient([self._raw_html_400()])
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_turn_user(config, args)

        assert len(client.calls) == 1  # no recovery

    def test_user_detail_format_triggers_recovery(self, monkeypatch):
        """FastAPI default {"detail": "session not found: ..."} recovers."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        client = _SequencedClient(
            [
                self._fastapi_detail_400(),
                _make_ok_response(),
                _make_ok_response(),
            ]
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_turn_user(config, args)

        assert len(client.calls) == 3  # recovery triggered for detail format too

    def test_user_session_not_found_match_is_case_insensitive(self, monkeypatch):
        """Recovery should tolerate daemon/proxy capitalization changes."""
        from bible_cc_plugin.scripts.hook import _handle_turn_user

        client = _SequencedClient(
            [
                _HTTPStatusError(
                    400, {"error": {"code": "BAD_REQUEST", "message": "Session not found: abc-123"}}
                ),
                _make_ok_response(),
                _make_ok_response(),
            ]
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False
        args = argparse.Namespace(session_id="abc-123", message="hello")

        with patch("builtins.print"):
            _handle_turn_user(config, args)

        assert len(client.calls) == 3

    # ── turn-tool ─────────────────────────────────────────────────────

    def test_tool_recovery_succeeds(self, monkeypatch):
        """turn-tool 400 'session not found' → recovery → retry OK."""
        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        client = _SequencedClient(
            [
                self._session_not_found_400(),
                _make_ok_response(),
                _make_ok_response(),
            ]
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.enabled = False
        args = argparse.Namespace(
            session_id="abc-123",
            tool="Bash",
            input=json.dumps({"command": "ls"}),
            output="file list...",
        )

        with patch("builtins.print"):
            _handle_turn_tool(config, args)

        assert len(client.calls) == 3
        assert client.calls[0][0] == "http://127.0.0.1:9777/turn/tool"
        assert client.calls[1][0] == "http://127.0.0.1:9777/session/start"
        assert client.calls[2][0] == "http://127.0.0.1:9777/turn/tool"

    def test_tool_recovery_fails_gracefully(self, monkeypatch):
        """turn-tool recovery fails → turn skipped, no crash."""
        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        client = _SequencedClient(
            [
                self._session_not_found_400(),
                _HTTPStatusError(500, {"error": {"code": "INTERNAL_ERROR", "message": "db error"}}),
            ]
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(
            session_id="abc-123",
            tool="Bash",
            input=json.dumps({"command": "ls"}),
            output="file list...",
        )

        with patch("builtins.print"):
            _handle_turn_tool(config, args)

        assert len(client.calls) == 2  # turn → recovery (failed)

    def test_tool_non_recoverable_400(self, monkeypatch):
        """turn-tool 400 not 'session not found' → log body, no recovery."""
        from bible_cc_plugin.scripts.hook import _handle_turn_tool

        client = _SequencedClient([self._session_completed_400()])
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._local_client",
            lambda timeout=5: client,
        )

        config = MagicMock()
        config.daemon.port = 9777
        args = argparse.Namespace(
            session_id="abc-123",
            tool="Read",
            input="{}",
            output="content",
        )

        with patch("builtins.print"):
            _handle_turn_tool(config, args)

        assert len(client.calls) == 1  # no recovery attempted
