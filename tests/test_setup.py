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


def test_openbot_machine_skips_claude_code_by_default(home):
    (home / "Library" / "Application Support" / "OpenBot").mkdir(parents=True)
    assert setup() == 0
    assert "agentos" not in json.loads((home / ".claude.json").read_text())["mcpServers"]
    assert "[mcp_servers.agentos]" in (home / ".codex" / "config.toml").read_text()
    assert setup("--agents", "claude-code") == 0  # asked for by name: included
    assert "agentos" in json.loads((home / ".claude.json").read_text())["mcpServers"]


class FakeWorkspace:
    server_info = {}

    def __init__(self, existing):
        self.existing = existing
        self.created = []

    def initialize(self, client_name=""):
        pass

    def list_tools(self):
        return ["workspace_call"]

    def call(self, tool, **kw):
        name, args = kw["name"], json.loads(kw["arguments"])
        if name == "skills_list":
            return {"skills": [{"slug": s, "name": s, "description": ""} for s in self.existing], "next_cursor": None}
        if name == "skills_create":
            self.created.append(args)
            self.existing.append(args["name"])
            return {"ok": True, "slug": args["name"]}
        raise AssertionError(name)


def _skill(base, name, body="Do the thing.\n", extra=None):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: about {name}\n---\n{body}")
    for f in extra or []:
        (d / f).write_text("x")
    return d


def test_setup_uploads_only_your_own_new_skills(home, monkeypatch):
    from agentos_cli import setup_cmd
    cs = home / ".claude" / "skills"
    _skill(cs, "deploy-checklist")
    _skill(cs, "already-there")
    _skill(cs, "aws-iam", extra=[".aws-skill-metadata"])       # another vendor's
    _skill(home / ".agents" / "skills", "weekly-report")
    ws = FakeWorkspace(["already-there"])
    monkeypatch.setenv("AGENTOS_PAT", "t")
    monkeypatch.setattr(setup_cmd, "_client", lambda: ws)
    assert setup("--upload") == 0
    assert sorted(c["name"] for c in ws.created) == ["deploy-checklist", "weekly-report"]
    assert ws.created[0]["content"].startswith("Do the thing") and ws.created[0]["description"].startswith("about")
    ws.created.clear()
    assert setup("--upload") == 0
    assert ws.created == []  # second run: nothing new; our installed AgentOS skills are never uploaded


def test_skills_push_command(home, monkeypatch):
    from agentos_cli import cli as C
    _skill(home / ".claude" / "skills", "my-skill")
    ws = FakeWorkspace([])
    monkeypatch.setenv("AGENTOS_PAT", "t")
    monkeypatch.setattr(C, "_ws_client", lambda: ws)
    assert C.main(["skills", "push"]) == 0
    assert [c["name"] for c in ws.created] == ["my-skill"]


def test_untitled_drafts_are_hidden():
    from agentos_cli import skills as S

    class C:
        def call(self, tool, name, arguments):
            return {"skills": [{"slug": "untitled-skill-9", "name": "Untitled skill"},
                               {"slug": "gmail", "name": "Gmail body", "description": "d"}], "next_cursor": None}
    assert [s["slug"] for s in S.workspace_list(C())] == ["gmail"]
    assert len(S.workspace_list(C(), include_untitled=True)) == 2


def test_running_inside_an_openbot_agent_skips_claude_code(home, monkeypatch):
    monkeypatch.setenv("OPENBOT_AGENT_PROCESS", "1")
    assert setup() == 0
    assert "agentos" not in json.loads((home / ".claude.json").read_text())["mcpServers"]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))  # its own config: not shared
    from agentos_cli import agents as A
    assert A.openbot_shares_claude() is False


def test_many_skills_upload_in_parallel(home, monkeypatch):
    import threading
    import time as _t
    from agentos_cli import setup_cmd

    class Slow(FakeWorkspace):
        lock = threading.Lock()

        def call(self, tool, **kw):
            if kw["name"] == "skills_create":
                _t.sleep(0.1)
            if kw["name"] == "skills_list":
                assert json.loads(kw["arguments"])["limit"] == 50
            with self.lock:
                return super().call(tool, **kw)

    for i in range(40):
        _skill(home / ".claude" / "skills", f"s{i:02d}")
    ws = Slow([])
    monkeypatch.setenv("AGENTOS_PAT", "t")
    monkeypatch.setattr(setup_cmd, "_client", lambda: ws)
    t0 = _t.time()
    assert setup("--upload", "--skills", "none", "--no-mcp") == 0
    assert len(ws.created) == 40
    assert _t.time() - t0 < 2.0  # 40 x 0.1 s one by one would be 4 s
