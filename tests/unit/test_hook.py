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
