"""Port conflict detection and resolution (1d.1).

Design: 03-daemon/port-conflict.md.
"""

from __future__ import annotations

import socket
import subprocess

from bible_cc_plugin.logging_config import setup_logging

_logger = setup_logging(level="INFO")


class PortExhaustedError(Exception):
    """Raised when all ports in the probe range are occupied."""


def get_port_owner(port: int) -> tuple[int, str] | None:
    """Return ``(pid, process_name)`` of the process holding *port*, or ``None``."""
    try:
        pid_str = subprocess.check_output(["lsof", "-ti", f":{port}"], text=True, timeout=2).strip()
        if not pid_str:
            return None
        pid = int(pid_str)
        name = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "comm="], text=True, timeout=2
        ).strip()
        return pid, name
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        ValueError,
    ):
        return None


def find_available_port(start_port: int, max_attempts: int = 10) -> int:
    """Find the first free port starting from *start_port*, incrementing by 1.

    Raises :class:`PortExhaustedError` if no free port is found.
    """
    for offset in range(max_attempts):
        port = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                _logger.debug("port %d is free", port)
                return port
            except OSError:
                _logger.debug("port %d occupied", port)
                continue

    raise PortExhaustedError(f"all ports {start_port}..{start_port + max_attempts - 1} occupied")


def build_port_conflict_hint(port: int, owner: tuple[int, str] | None) -> str:
    """Build a human-readable error hint for port conflict."""
    if owner:
        pid, name = owner
        return (
            f"bible-cc daemon cannot start on port {port}. "
            f"Port is occupied by pid {pid} ({name}). "
            f"Fix: free port {port}, set daemon.port in ~/.bible-cc/config.json, "
            f"or enable daemon.port_auto_fallback to auto-select."
        )
    return (
        f"bible-cc daemon cannot start on port {port} "
        f"(cannot identify process). "
        f"Fix: free port {port} or set daemon.port_auto_fallback: true."
    )
