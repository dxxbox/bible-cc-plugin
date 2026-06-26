"""Contract daemon subprocess environment isolation."""

from __future__ import annotations

from tests.contract.conftest import _build_contract_daemon_env


def test_contract_daemon_env_strips_production_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("BIBLE_CC_DB_PATH", "/Users/me/.bible-cc/daemon.db")
    monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "https://prod.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "prod-secret")

    env = _build_contract_daemon_env(tmp_path)

    assert env["BIBLE_CC_DB_PATH"] == str(tmp_path / "daemon.db")
    assert env["BIBLE_CC_CONFIG_PATH"] == str(tmp_path / "nonexistent-config.json")
    assert env["BIBLE_CC_LOG_FILE"] == str(tmp_path / "daemon.log")
    assert env["DETECTOR_TEST_MODE"] == "1"
    assert "BIBLE_ATLAS_BASE_URL" not in env
    assert "ANTHROPIC_API_KEY" not in env
