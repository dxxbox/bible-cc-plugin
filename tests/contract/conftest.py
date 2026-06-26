"""Contract-test fixtures that isolate spawned daemon subprocesses.

Contract tests use module-scoped daemon fixtures, so they cannot rely on the
function-scoped autouse fixture in tests/conftest.py.  Any daemon subprocess
must receive this env explicitly to avoid touching ~/.bible-cc state.
"""

from __future__ import annotations

import os
import subprocess

import pytest


def _build_contract_daemon_env(state_dir) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not (key.startswith("BIBLE_") or key.startswith("ANTHROPIC_"))
    }
    env.update(
        {
            "BIBLE_CC_DB_PATH": str(state_dir / "daemon.db"),
            "BIBLE_CC_CONFIG_PATH": str(state_dir / "nonexistent-config.json"),
            "BIBLE_CC_LOG_FILE": str(state_dir / "daemon.log"),
            "DETECTOR_TEST_MODE": "1",
        }
    )
    return env


@pytest.fixture(scope="module")
def contract_daemon_env(tmp_path_factory) -> dict[str, str]:
    """Return a base environment for an isolated contract-test daemon."""
    state_dir = tmp_path_factory.mktemp("bible-cc-contract")
    return _build_contract_daemon_env(state_dir)


def terminate_process(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Terminate a daemon subprocess, force-killing if graceful exit stalls."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)
