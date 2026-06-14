"""Unit tests for port conflict detection (1d.1).

[Unit] [Pre] — mock lsof/socket, no daemon process needed.
"""

from __future__ import annotations

import socket

import pytest


class TestGetPortOwner:
    def test_returns_pid_and_name_when_lsof_available(self, monkeypatch):
        import subprocess

        def multi_mock(cmd, text=False, timeout=None):
            if isinstance(cmd, list) and "lsof" in str(cmd):
                return "1234\n"
            return "python3.12\n"

        monkeypatch.setattr(subprocess, "check_output", multi_mock)

        from bible_cc_plugin.daemon.port_manager import get_port_owner

        result = get_port_owner(9777)
        assert result is not None
        pid, name = result
        assert pid == 1234
        assert "python" in name

    def test_returns_none_when_lsof_fails(self, monkeypatch):
        import subprocess

        def fake_check_output(cmd, text=False, timeout=None):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "check_output", fake_check_output)

        from bible_cc_plugin.daemon.port_manager import get_port_owner

        assert get_port_owner(9777) is None


class TestFindAvailablePort:
    def test_returns_start_port_when_free(self):
        from bible_cc_plugin.daemon.port_manager import find_available_port

        port = find_available_port(9777, max_attempts=5)
        assert port >= 9777
        assert port < 9777 + 5

    def test_raises_when_all_occupied(self):
        from bible_cc_plugin.daemon.port_manager import (
            PortExhaustedError,
            find_available_port,
        )

        socks = []
        for offset in range(3):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 9877 + offset))
            socks.append(s)
        try:
            with pytest.raises(PortExhaustedError):
                find_available_port(9877, max_attempts=3)
        finally:
            for s in socks:
                s.close()


class TestBuildPortConflictHint:
    def test_includes_pid_and_name(self):
        from bible_cc_plugin.daemon.port_manager import build_port_conflict_hint

        hint = build_port_conflict_hint(9777, (1234, "python3.12"))
        assert "9777" in hint
        assert "1234" in hint
        assert "python" in hint

    def test_without_owner(self):
        from bible_cc_plugin.daemon.port_manager import build_port_conflict_hint

        hint = build_port_conflict_hint(9777, None)
        assert "9777" in hint
        assert "cannot identify" in hint.lower() or "occupied" in hint.lower()


class TestPortConflictIntent:
    def test_fail_fast_by_default(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.daemon.port_auto_fallback is False, (
            "Default must be fail-fast — silent port switch breaks hook scripts"
        )
