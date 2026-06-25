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
            "session-start missing --session-id" in r.message
            and r.levelname == "WARNING"
            for r in caplog.records
        ), "should log WARNING (not ERROR) for missing session-id on startup"

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
        args = argparse.Namespace(
            session_id="abc-123", tool="Bash", input=None, output="ok"
        )

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

        assert any(
            "was never registered" in r.message
            for r in caplog.records
        ), "should log 'was never registered' for session-not-found 404"

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

        assert any(
            "HTTP 404" in r.message
            for r in caplog.records
        ), "should log HTTP status code for unknown 404"

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

        assert any(
            "unreachable" in r.message
            for r in caplog.records
        ), "should log 'unreachable' for transport errors"

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

        assert any(
            "missing --session-id" in r.message
            for r in caplog.records
        ), "should log error for missing session-id"


class TestHookTurnStop:
    """Verify turn-stop polls already detected moments for hints."""

    def test_polls_hints_for_session(self, monkeypatch):
        from bible_cc_plugin.scripts.hook import _handle_turn_stop

        calls = []

        def fake_print_hints(session_id, base_url, hint_format):
            calls.append((session_id, base_url, hint_format))

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._print_hints", fake_print_hints
        )

        config = MagicMock()
        config.daemon.port = 9777
        config.capture.hint_format = "quote_with_command"
        args = argparse.Namespace(session_id="abc-123")

        _handle_turn_stop(config, args)

        assert calls == [
            ("abc-123", "http://127.0.0.1:9777", "quote_with_command")
        ]

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

    def test_formats_daemon_json_with_moment_type_key(self, monkeypatch, capsys):
        """dict with 'moment_type' key → hint printed to stdout."""
        # Trigger anthropic import before monkeypatching httpx.Client.
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints, _hint_cursor_path

        session_id = "test-hint-1"
        # Clean up cursor from prior runs
        try:
            _hint_cursor_path(session_id).unlink()
        except FileNotFoundError:
            pass

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
        assert "Postgres" in output
        assert "Decision" in output
        assert "⎿ ⏳" in output
        assert "/bible-cc:review" in output

    def test_one_bad_moment_does_not_block_subsequent(self, monkeypatch, capsys):
        """Bad moment (None keys) → skipped, next moment still prints."""
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints, _hint_cursor_path

        session_id = "test-hint-2"
        try:
            _hint_cursor_path(session_id).unlink()
        except FileNotFoundError:
            pass

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

        output = capsys.readouterr().out
        assert "Rate limiting done" in output
        # first moment is bad but won't crash; second still appears

    def test_cursor_prevents_duplicate_hints(self, monkeypatch, capsys):
        """Second call with same session_id skips already-hinted moments."""
        from bible_cc_plugin.daemon.detector import MomentCandidate  # noqa: F401
        from bible_cc_plugin.scripts.hook import _print_hints, _hint_cursor_path

        session_id = "test-hint-cursor"
        try:
            _hint_cursor_path(session_id).unlink()
        except FileNotFoundError:
            pass

        call_count = [0]

        class FakeResponse:
            status_code = 200

            def json(self):
                call_count[0] += 1
                return {
                    "moments": [
                        {"id": 1, "moment_type": "decision", "title": "Only once", "narrative": "Only once"},
                    ]
                }

            def raise_for_status(self):
                pass

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        _print_hints(session_id, "http://127.0.0.1:9777", "quote_only")
        out1 = capsys.readouterr().out
        assert "Only once" in out1, "first call should print hint"

        _print_hints(session_id, "http://127.0.0.1:9777", "quote_only")
        out2 = capsys.readouterr().out
        assert "Only once" not in out2, "second call should skip already-hinted moment"

    def test_get_failure_logs_warning_and_returns(self, monkeypatch, caplog):
        """GET /daemon/moments fails → logged, no crash, no hints."""
        import logging

        client = httpx.Client(trust_env=False)
        monkeypatch.setattr(client, "get", lambda *a, **kw: (_ for _ in ()).throw(
            httpx.ConnectError("connection refused")))
        monkeypatch.setattr(httpx, "Client", lambda **kw: client)

        from bible_cc_plugin.scripts.hook import _print_hints

        root = logging.getLogger("bible_cc")
        root.propagate = True
        caplog.set_level(logging.WARNING)
        _print_hints("abc-123", "http://127.0.0.1:9777", "quote_with_command")
        root.propagate = False

        assert any(
            "_print_hints: GET /daemon/moments failed" in r.message
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
            "_print_hints: GET /daemon/moments failed" in r.message
            for r in caplog.records
        )


class TestStdinJsonParsing:
    """Verify main() reads hook event data from stdin JSON."""

    def test_session_start_from_stdin(self, monkeypatch):
        """SessionStart stdin → session_id flows to handler."""
        stdin_json = json.dumps({
            "session_id": "abc-123",
            "hook_event_name": "SessionStart",
        })
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "session-start"])

        def fake_start(config, args):
            called_with["session_id"] = args.session_id

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._handle_session_start", fake_start
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None
        )

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "abc-123"

    def test_turn_user_from_stdin(self, monkeypatch):
        """UserPromptSubmit stdin → session_id + prompt."""
        stdin_json = json.dumps({
            "session_id": "def-456",
            "prompt": "hello world",
            "hook_event_name": "UserPromptSubmit",
        })
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "turn-user"])

        def fake_handler(config, args):
            called_with["session_id"] = args.session_id
            called_with["message"] = args.message

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._handle_turn_user", fake_handler
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None
        )

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "def-456"
        assert called_with.get("message") == "hello world"

    def test_turn_tool_from_stdin(self, monkeypatch):
        """PostToolUse stdin → session_id + tool_name + input + output."""
        stdin_json = json.dumps({
            "session_id": "ghi-789",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "tool_response": "All tests passed.",
        })
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "turn-tool"])

        def fake_handler(config, args):
            called_with["session_id"] = args.session_id
            called_with["tool"] = args.tool
            called_with["input"] = args.input
            called_with["output"] = args.output

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._handle_turn_tool", fake_handler
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None
        )

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "ghi-789"
        assert called_with.get("tool") == "Bash"
        assert called_with.get("output") == "All tests passed."
        # tool_input from stdin is serialized to JSON string
        assert called_with.get("input") == '{"command": "pytest"}'

    def test_cli_overrides_stdin(self, monkeypatch):
        """CLI --session-id wins over stdin session_id."""
        stdin_json = json.dumps({"session_id": "from-stdin"})

        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr(
            "sys.argv", ["hook", "session-start", "--session-id", "from-cli"]
        )

        def fake_start(config, args):
            called_with["session_id"] = args.session_id

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._handle_session_start", fake_start
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None
        )

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

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._handle_session_start", fake_start
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None
        )

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == "tty-test"

    def test_startup_empty_session_id_from_stdin(self, monkeypatch):
        """startup event: stdin has empty session_id → handler sees empty string."""
        stdin_json = json.dumps({
            "session_id": "",
            "hook_event_name": "SessionStart",
        })
        called_with = {}

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: stdin_json)
        monkeypatch.setattr("sys.argv", ["hook", "session-start"])

        def fake_start(config, args):
            called_with["session_id"] = args.session_id

        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook._handle_session_start", fake_start
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.load_config", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.hook.configure_logging", lambda *a, **kw: None
        )

        from bible_cc_plugin.scripts.hook import main

        main()
        assert called_with.get("session_id") == ""
