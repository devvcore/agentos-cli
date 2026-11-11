import json
import shutil

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake HOME with Claude Code, Codex and opencode 'installed'. Nothing real is touched."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / ".claude.json").write_text(json.dumps({
        "numStartups": 3, "mcpServers": {"aws-mcp": {"command": "uvx", "args": ["x"]}},
        "projects": {"/x": {"allowedTools": []}}}, indent=2) + "\n")
    (h / ".codex").mkdir()
    (h / ".codex" / "config.toml").write_text(
        '# my codex config\nmodel = "gpt-5"\n\n[mcp_servers.other_tool]\ncommand = "other-mcp"\nenabled = false\n')
    (h / ".config" / "opencode").mkdir(parents=True)
    (h / ".config" / "opencode" / "opencode.json").write_text(json.dumps(
        {"$schema": "https://opencode.ai/config.json", "mcp": {"aws-mcp": {"type": "local", "command": ["uvx"]}}}, indent=2))
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("AOS_NO_KEYRING", "1")
    monkeypatch.setenv("AOS_NONINTERACTIVE", "1")
    monkeypatch.delenv("AGENTOS_PAT", raising=False)
    for v in ("OPENBOT_AGENT_PROCESS", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "AOS_AS", "AOS_AGENT"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv("AGENTOS_URL", raising=False)
    monkeypatch.delenv("AGENTOS_MCP_URL", raising=False)
    for var in ("OPENBOT_AGENT_PROCESS", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    return h
