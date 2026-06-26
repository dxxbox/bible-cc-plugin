"""Unit tests for config loading — three-tier priority: default → config.json → env var."""

import json


class TestConfigDefaults:
    """Verify built-in default values are correct."""

    def test_default_base_url(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.bible.base_url == "http://localhost:5555"

    def test_default_token_is_none(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.bible.token is None

    def test_default_port(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.daemon.port == 9777

    def test_default_port_auto_fallback(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.daemon.port_auto_fallback is False

    def test_default_capture_enabled(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.capture.enabled is True
        assert config.capture.commit_threshold_turns == 4
        assert config.capture.commit_threshold_chars == 2000

    def test_default_hint_format(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.capture.hint_format == "quote_with_command"


class TestConfigValidation:
    """Verify silent fallback on invalid values."""

    def test_port_out_of_range_falls_back_to_default(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig(daemon={"port": 80})
        assert config.daemon.port == 9777

    def test_port_too_high_falls_back_to_default(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig(daemon={"port": 99999})
        assert config.daemon.port == 9777

    def test_invalid_base_url_falls_back(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig(bible={"base_url": "ftp://not-http"})
        assert config.bible.base_url == "http://localhost:5555"

    def test_base_url_trailing_slash_falls_back(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig(bible={"base_url": "http://localhost:5555/"})
        assert config.bible.base_url == "http://localhost:5555"

    def test_invalid_hint_format_falls_back(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig(capture={"hint_format": "invalid_mode"})
        assert config.capture.hint_format == "command_only"

    def test_empty_token_treated_as_none(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig(bible={"token": ""})
        assert config.bible.token is None


class TestConfigFileLoading:
    """Verify config.json overlay on defaults."""

    def test_load_from_file_overrides_defaults(self):
        from bible_cc_plugin.config import AppConfig

        file_data = {"daemon": {"port": 8888}, "bible": {"base_url": "http://example.com:5555"}}
        config = AppConfig(**file_data)
        assert config.daemon.port == 8888
        assert config.bible.base_url == "http://example.com:5555"
        # Unspecified fields keep defaults
        assert config.capture.enabled is True


class TestLoadConfigFunction:
    """Verify the three-tier load_config() function."""

    def test_load_config_returns_app_config(self):
        from bible_cc_plugin.config import load_config

        config = load_config()
        assert config.bible.base_url == "http://localhost:5555"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://env-override:5555")
        from bible_cc_plugin.config import load_config

        config = load_config()
        assert config.bible.base_url == "http://env-override:5555"

    def test_env_port_override(self, monkeypatch):
        monkeypatch.setenv("BIBLE_CC_DAEMON_PORT", "12345")
        from bible_cc_plugin.config import load_config

        config = load_config()
        assert config.daemon.port == 12345

    def test_env_token_override(self, monkeypatch):
        monkeypatch.setenv("BIBLE_ATLAS_TOKEN", "sk-ant-test-token")
        from bible_cc_plugin.config import load_config

        config = load_config()
        assert config.bible.token == "sk-ant-test-token"

    def test_env_db_path_override(self, monkeypatch):
        monkeypatch.setenv("BIBLE_CC_DB_PATH", "/tmp/test-daemon.db")
        from bible_cc_plugin.config import load_config

        config = load_config()
        assert config.daemon.db_path == "/tmp/test-daemon.db"

    def test_load_from_json_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "daemon": {"port": 7777},
                    "bible": {"base_url": "http://file-config:5555"},
                }
            )
        )
        from bible_cc_plugin.config import load_config

        config = load_config(config_path=config_file)
        assert config.daemon.port == 7777
        assert config.bible.base_url == "http://file-config:5555"

    def test_env_wins_over_file(self, monkeypatch, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "bible": {"base_url": "http://file-config:5555"},
                }
            )
        )
        monkeypatch.setenv("BIBLE_ATLAS_BASE_URL", "http://env-wins:5555")
        from bible_cc_plugin.config import load_config

        config = load_config(config_path=config_file)
        assert config.bible.base_url == "http://env-wins:5555"

    def test_load_config_debug_output(self, tmp_path, capsys):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"daemon": {"port": 6666}}))
        from bible_cc_plugin.config import load_config

        load_config(config_path=config_file, debug=True)
        captured = capsys.readouterr()
        assert "6666" in captured.err or "6666" in captured.out


class TestDetectionConfig:
    """2a.2: detection.model default + env override."""

    def test_detection_model_default(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.detection.model == "deepseek-v4-flash"


class TestCaptureModeValidation:
    """2a.2: capture.mode tightened to Literal['key_moments', 'all']."""

    def test_capture_mode_default(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig()
        assert config.capture.mode == "key_moments"

    def test_capture_mode_all_accepted(self):
        from bible_cc_plugin.config import AppConfig

        config = AppConfig(capture={"mode": "all"})
        assert config.capture.mode == "all"

    def test_capture_mode_invalid_raises(self):
        import pydantic

        from bible_cc_plugin.config import AppConfig

        try:
            AppConfig(capture={"mode": "invalid_mode"})
            assert False, "should have raised"
        except pydantic.ValidationError:
            pass

    def test_capture_enabled_env_override(self, monkeypatch):
        monkeypatch.setenv("BIBLE_CC_CAPTURE_ENABLED", "false")
        from bible_cc_plugin.config import load_config

        config = load_config()
        assert config.capture.enabled is False

    def test_capture_enabled_env_override_true(self, monkeypatch):
        monkeypatch.setenv("BIBLE_CC_CAPTURE_ENABLED", "1")
        from bible_cc_plugin.config import load_config

        config = load_config()
        assert config.capture.enabled is True
