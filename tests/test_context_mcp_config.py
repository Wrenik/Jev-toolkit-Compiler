"""The plugin's MCP launch config must not depend on a checkout path, a configured option or a Windows-only path."""

import json
from pathlib import Path

CONFIG = Path(__file__).parent.parent / "claude-mod" / "jev-context" / ".mcp.json"


def test_mcp_config_needs_no_checkout_path_and_uses_a_portable_command():
    text = CONFIG.read_text(encoding="utf-8")
    server = json.loads(text)["mcpServers"]["jev-context"]
    assert server["command"] == "python"  # the package is pip-installed; the data location comes from JEV_HOME
    assert server["args"] == ["-m", "jev_context.mcp_server"]
    assert "cwd" not in server and "env" not in server
    for forbidden in ("user_config", "JEV_TOOLKIT", "Scripts", "toolkitPath"):
        assert forbidden not in text
