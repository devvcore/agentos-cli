import json
from types import SimpleNamespace

from agentos_cli import cli, paths


def setup(*extra):
    return cli.main(["setup", "--yes", *extra])


def test_setup_then_undo_round_trip(home):
    claude_before = json.loads((home / ".claude.json").read_text())
    codex_before = (home / ".codex" / "config.toml").read_text()

    assert setup() == 0
    assert json.loads((home / ".claude.json").read_text())["mcpServers"]["agentos"]["url"].endswith("/api/mcp")
    assert "[mcp_servers.agentos]" in (home / ".codex" / "config.toml").read_text()
    assert "agentos" in json.loads((home / ".config/opencode/opencode.json").read_text())["mcp"]
    for d in (home / ".claude/skills", home / ".agents/skills"):
        assert {"aos", "agentos-setup", "agentos-spaces"} <= {p.name for p in d.iterdir()}
    runs = paths.read_json(paths.manifest_path(), {})["runs"]
    assert len(runs[0]["mcp"]) == 3 and len(runs[0]["skills"]) == 2 * 7
    backups = list((home / ".agentos" / "backups").rglob("*"))
    assert any(p.name.endswith(".claude.json") for p in backups)

    assert setup() == 0  # second run: nothing to do
    assert len(paths.read_json(paths.manifest_path(), {})["runs"]) == 1

    assert cli.main(["setup", "--yes", "--undo"]) == 0
    assert json.loads((home / ".claude.json").read_text()) == claude_before
    assert (home / ".codex" / "config.toml").read_text() == codex_before
    assert not (home / ".claude/skills/aos").exists()


def test_setup_only_chosen_agents_and_dry_run(home):
    assert setup("--agents", "codex", "--dry-run") == 0
    assert "agentos" not in (home / ".codex" / "config.toml").read_text()
    assert setup("--agents", "codex", "--skills", "none") == 0
    assert "agentos" in (home / ".codex" / "config.toml").read_text()
    assert "agentos" not in (home / ".claude.json").read_text()
    assert not (home / ".agents/skills").exists()


def test_setup_needs_a_terminal_or_yes(home):
    assert cli.main(["setup"]) == 2


def test_unknown_agent_is_refused(home):
    assert setup("--agents", "notepad") == 2


def test_reviewed_file_changed_before_write_is_replanned(home, monkeypatch):
    from agentos_cli import agents as A, setup_cmd
    a = A.BY_ID["claude-code"]
    ch = A.plan_mcp_add(a)
    d = json.loads((home / ".claude.json").read_text())
    d["numStartups"] = 4  # Claude Code wrote meanwhile
    (home / ".claude.json").write_text(json.dumps(d, indent=2))
    setup_cmd._apply_change(ch, "t", lambda: A.plan_mcp_add(a))
    d = json.loads((home / ".claude.json").read_text())
    assert d["numStartups"] == 4 and "agentos" in d["mcpServers"]
