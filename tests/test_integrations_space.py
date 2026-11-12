import json

import pytest

from agentos_cli import cli, integrations, work


class Fake:
    server_info = {}

    def __init__(self):
        self.calls = []

    def list_tools(self):
        return ["context", "workspace_call", "workspace_tools"]

    def call(self, tool, **kw):
        self.calls.append((tool, kw))
        if tool == "workspace_tools":
            return {"tools": [{"name": "tasks_list", "family": "tasks", "description": "List tasks"}]}
        if tool == "workspace_call":
            name, args = kw["name"], json.loads(kw["arguments"])
            if name == "integration_search":
                return {"tools": [{"slug": "GMAIL_SEND_EMAIL", "toolkit_slug": "gmail", "connected": True,
                                   "description": "Send an email"}]}
            if name == "integration_call":
                return {"ok": True, "ran": args["tool_slug"], "args": args["arguments"]}
            return {"ok": True, "ran": name, "args": args}
        raise AssertionError(tool)


@pytest.fixture
def fake(monkeypatch, home):
    f = Fake()
    monkeypatch.setattr(work, "_TOP", None)
    monkeypatch.setattr(work, "connect", lambda timeout=60, info=False: f)
    return f


def test_search_lists_agentos_and_apps_together(fake, capsys):
    assert cli.main(["integrations", "send", "email"]) == 0
    rows = json.loads(capsys.readouterr().out)
    # both kinds in one list; tasks_list doesn't name "send" or "email", so the app action leads
    assert [(r["action"], r["source"]) for r in rows] == [("GMAIL_SEND_EMAIL", "gmail"), ("tasks_list", "AgentOS")]


def test_run_routes_by_name(fake, capsys):
    assert cli.main(["integrations", "run", "tasks_list", '{"status":"open"}']) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "ran": "tasks_list", "args": {"status": "open"}}
    assert cli.main(["integrations", "run", "GMAIL_SEND_EMAIL", '{"to":"a@b.c"}']) == 0
    assert json.loads(capsys.readouterr().out)["ran"] == "GMAIL_SEND_EMAIL"


def test_old_tools_and_call_still_work(fake, capsys):
    assert cli.main(["call", "tasks_list", "{}"]) == 0
    assert json.loads(capsys.readouterr().out)["ran"] == "tasks_list"
    assert cli.main(["tools", "--q", "email"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_app_specific_commands_are_gone(home, capsys):
    with pytest.raises(SystemExit):
        cli.main(["stripe", "list-invoices"])


def test_space_group_and_old_verbs(home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["space"]) == 0
    assert "aos space <command>" in capsys.readouterr().out
    assert cli.main(["space", "nope"]) == 2
    for argv in (["space", "status"], ["pull"]):  # old verb and new group: same command
        with pytest.raises(SystemExit, match="aos space link"):
            cli.main(argv)
    assert cli.main(["space", "sync"]) == 1
    assert "aos space link" in capsys.readouterr().err


def test_background_sync_is_refused(home, tmp_path, monkeypatch, capsys):
    (tmp_path / ".aos").mkdir()
    (tmp_path / ".aos" / "state.json").write_text(json.dumps({"space": "s", "version": 0, "tree": {}}))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["space", "sync", "--background"]) == 2
    assert "removed" in capsys.readouterr().err


def test_naming_an_app_puts_its_actions_first(fake, capsys):
    assert cli.main(["integrations", "gmail", "send"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["action"] == "GMAIL_SEND_EMAIL"


def test_loose_agentos_matches_go_after_apps(fake, monkeypatch, capsys):
    orig = fake.call

    def call(tool, **kw):
        if tool == "workspace_tools":
            return {"tools": [{"name": "crm_contacts_get", "description": "has an email field"},
                              {"name": "send_message", "description": "send a chat message"}]}
        return orig(tool, **kw)
    monkeypatch.setattr(fake, "call", call)
    assert cli.main(["integrations", "send", "email"]) == 0
    order = [r["action"] for r in json.loads(capsys.readouterr().out)]
    assert order == ["send_message", "GMAIL_SEND_EMAIL", "crm_contacts_get"]


def test_mcp_server_tools_run_as_app_actions(fake, capsys):
    assert integrations.is_app_action("mcp_15cfc985_stripe_analytics_60f66b5c")
    assert not integrations.is_app_action("tasks_list")
    assert cli.main(["integrations", "run", "mcp_15cfc985_stripe_analytics_60f66b5c", "{}"]) == 0
    assert json.loads(capsys.readouterr().out)["ran"] == "mcp_15cfc985_stripe_analytics_60f66b5c"
    assert fake.calls[-1][1]["name"] == "integration_call"
