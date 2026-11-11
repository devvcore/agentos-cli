import json

import pytest

from agentos_cli import agents as A, paths

URL = "https://tryagentos.net/api/mcp"


def apply(ch):
    paths.write_text(ch.path, ch.after)


def test_detects_only_installed_agents(home):
    ids = [a.id for a, _ in A.detected()]
    assert ids == ["claude-code", "codex", "opencode"]


def test_claude_code_add_keeps_everything_else_and_is_idempotent(home):
    a = A.BY_ID["claude-code"]
    assert A.mcp_status(a) == "absent"
    ch = A.plan_mcp_add(a)
    apply(ch)
    d = json.loads((home / ".claude.json").read_text())
    assert d["mcpServers"]["agentos"] == {"type": "http", "url": URL}
    assert d["mcpServers"]["aws-mcp"] and d["numStartups"] == 3 and "/x" in d["projects"]
    assert "Authorization" not in (home / ".claude.json").read_text()  # no token on disk
    assert A.mcp_status(a) == "ours"
    assert A.plan_mcp_add(a) is None
    apply(A.plan_mcp_remove(a))
    d = json.loads((home / ".claude.json").read_text())
    assert "agentos" not in d["mcpServers"] and "aws-mcp" in d["mcpServers"]


def test_codex_toml_keeps_comments_and_other_servers(home):
    a = A.BY_ID["codex"]
    before = (home / ".codex" / "config.toml").read_text()
    apply(A.plan_mcp_add(a))
    text = (home / ".codex" / "config.toml").read_text()
    assert text.startswith(before)
    assert f'[mcp_servers.agentos]\nurl = "{URL}"\n' in text
    import tomllib
    assert tomllib.loads(text)["mcp_servers"]["agentos"] == {"url": URL}
    assert A.mcp_status(a) == "ours"
    apply(A.plan_mcp_remove(a))
    after = (home / ".codex" / "config.toml").read_text()
    assert "agentos" not in after and "# my codex config" in after and "other_tool" in after


def test_opencode_entry_shape(home):
    a = A.BY_ID["opencode"]
    apply(A.plan_mcp_add(a))
    d = json.loads((home / ".config" / "opencode" / "opencode.json").read_text())
    assert d["mcp"]["agentos"] == {"type": "remote", "url": URL, "enabled": True}
    assert "aws-mcp" in d["mcp"]


def test_custom_entry_is_left_alone(home):
    p = home / ".claude.json"
    d = json.loads(p.read_text())
    d["mcpServers"]["agentos"] = {"type": "http", "url": URL, "headers": {"Authorization": "Bearer x"}}
    p.write_text(json.dumps(d))
    a = A.BY_ID["claude-code"]
    assert A.mcp_status(a) == "custom"
    with pytest.raises(FileExistsError):
        A.plan_mcp_add(a)
    assert A.plan_mcp_remove(a) is None  # never remove an entry we didn't write
    ch = A.plan_mcp_add(a, force=True)
    assert json.loads(ch.after)["mcpServers"]["agentos"] == {"type": "http", "url": URL}


def test_broken_json_gives_a_readable_error(home):
    (home / ".claude.json").write_text("{ nope")
    a = A.BY_ID["claude-code"]
    assert A.mcp_status(a) == "error"
    with pytest.raises(ValueError, match="isn't valid JSON"):
        A.plan_mcp_add(a)


def test_new_file_for_missing_config(home):
    (home / ".cursor").mkdir()
    a = A.BY_ID["cursor"]
    ch = A.plan_mcp_add(a)
    assert ch.before is None
    assert json.loads(ch.after) == {"mcpServers": {"agentos": {"url": URL}}}


def test_claude_desktop_link():
    assert "connectorUrl=https%3A%2F%2Ftryagentos.net%2Fapi%2Fmcp" in A.claude_connector_link()
