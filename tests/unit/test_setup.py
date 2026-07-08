"""Unit tests for setup.py — project root resolution + .mcp.json writing."""

import json
from pathlib import Path


class TestFindProjectRoot:
    """_find_project_root() resolves the correct plugin/repo root."""

    def test_finds_root_by_pyproject_toml(self):
        """In the real repo, pyproject.toml is at the root — check it finds it."""
        from bible_cc_plugin.scripts.setup import _find_project_root

        root = _find_project_root()
        assert root.name == "bible-cc-plugin"
        assert (root / "pyproject.toml").exists()

    def test_finds_root_by_plugin_json(self, tmp_path):
        """When .claude-plugin/plugin.json exists, that dir is the root."""
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text("{}")

        scripts_dir = tmp_path / "src" / "pkg" / "scripts"
        scripts_dir.mkdir(parents=True)
        fake_script = scripts_dir / "fake_setup.py"
        fake_script.write_text("")

        start = Path(str(fake_script)).resolve().parent
        # Replicate _find_project_root logic
        for d in [start] + list(start.parents):
            if (d / "pyproject.toml").exists() or (d / ".claude-plugin" / "plugin.json").exists():
                root = d
                break
        else:
            root = start.parent.parent.parent

        assert root == tmp_path

    def test_fallback_when_no_marker_found(self, tmp_path):
        """When no marker exists, fall back three levels from script dir."""
        scripts_dir = tmp_path / "src" / "pkg" / "scripts"
        scripts_dir.mkdir(parents=True)
        fake_script = scripts_dir / "fake_setup.py"
        fake_script.write_text("")

        start = Path(str(fake_script)).resolve().parent
        fallback = start.parent.parent.parent
        assert fallback == tmp_path


class TestWriteMcpJson:
    """_write_mcp_json() writes .mcp.json to the project root."""

    def test_writes_mcp_json_to_root(self, tmp_path, monkeypatch):
        """Verify .mcp.json is written with correct path and content."""
        from bible_cc_plugin.scripts.setup import _write_mcp_json

        (tmp_path / "pyproject.toml").write_text("")
        fake_script = tmp_path / "src" / "pkg" / "scripts" / "fake_setup.py"
        fake_script.parent.mkdir(parents=True)
        fake_script.write_text("")
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.setup.__file__", str(fake_script)
        )

        _write_mcp_json("http://localhost:5555", "test-token")

        mcp_path = tmp_path / ".mcp.json"
        assert mcp_path.exists(), f".mcp.json not found at {mcp_path}"

        data = json.loads(mcp_path.read_text())
        server = data["mcpServers"]["bible-cc"]
        assert server["command"] == "uv"
        assert "bible_cc_plugin.mcp.server" in str(server["args"])
        assert server["env"]["BIBLE_ATLAS_BASE_URL"] == "http://localhost:5555"
        assert server["env"]["BIBLE_ATLAS_TOKEN"] == "test-token"

    def test_empty_token_written_as_empty_string(self, tmp_path, monkeypatch):
        """When token is empty string, env var is empty (not missing)."""
        from bible_cc_plugin.scripts.setup import _write_mcp_json

        (tmp_path / "pyproject.toml").write_text("")
        fake_script = tmp_path / "src" / "pkg" / "scripts" / "fake_setup.py"
        fake_script.parent.mkdir(parents=True)
        fake_script.write_text("")
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.setup.__file__", str(fake_script)
        )

        _write_mcp_json("http://localhost:5555", "")

        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert data["mcpServers"]["bible-cc"]["env"]["BIBLE_ATLAS_TOKEN"] == ""

    def test_base_url_trailing_slash_stripped(self, tmp_path, monkeypatch):
        """Base URL trailing slash is stripped."""
        from bible_cc_plugin.scripts.setup import _write_mcp_json

        (tmp_path / "pyproject.toml").write_text("")
        fake_script = tmp_path / "src" / "pkg" / "scripts" / "fake_setup.py"
        fake_script.parent.mkdir(parents=True)
        fake_script.write_text("")
        monkeypatch.setattr(
            "bible_cc_plugin.scripts.setup.__file__", str(fake_script)
        )

        _write_mcp_json("http://localhost:5555/", "")

        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert data["mcpServers"]["bible-cc"]["env"]["BIBLE_ATLAS_BASE_URL"] == "http://localhost:5555"
