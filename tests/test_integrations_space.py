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
            if kw.get("name"):
                return {"name": kw["name"], "description": "List tasks", "required": [], "optional": ["status"]} \
                    if kw["name"] == "tasks_list" else {"error": "unknown"}
            return {"tools": [{"name": "tasks_list", "family": "tasks", "description": "List tasks"}], "next_cursor": None}
        if tool == "workspace_call":
            name, args = kw["name"], json.loads(kw["arguments"])
            if name == "integration_list":
                return {"platforms": [{"toolkit_slug": "gmail", "connected": True, "tool_count": 23,
                                       "accounts": [{"label": "Work"}]},
                                      {"toolkit_slug": "notion", "connected": False, "tool_count": 27}]}
            if name == "integration_search":
                rows = [{"slug": "GMAIL_SEND_EMAIL", "toolkit_slug": "gmail", "connected": True, "description": "Send an email",
                         "inputSchema": {"properties": {"to": {"type": "string"}}, "required": ["to"]}},
                        {"slug": "GMAIL_SEND_DRAFT", "toolkit_slug": "gmail", "connected": True, "description": "Send a draft"}]
                if args.get("toolkit") and args["toolkit"] != "gmail":
                    rows = []
                return {"tools": rows, "has_more": False}
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


def ran(f):
    return [json.loads(k["arguments"]) for t, k in f.calls if t == "workspace_call" and k["name"] == "integration_call"]


def test_integrations_lists_only_connected_apps(fake, capsys):
    assert cli.main(["integrations"]) == 0
    assert [r["toolkit_slug"] for r in json.loads(capsys.readouterr().out)] == ["gmail"]
    assert cli.main(["integrations", "--all"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_integrations_help_mentions_no_actions(capsys):
    assert cli.main(["integrations", "--help"]) == 0
    out = capsys.readouterr().out
    assert "aos call <app>" in out and "tools" not in out and "run" not in out


def test_call_app_lists_searches_and_runs_exact_only(fake, capsys):
    assert cli.main(["call", "gmail"]) == 0
    assert [r["action"] for r in json.loads(capsys.readouterr().out)] == ["send_email", "send_draft"]
    assert cli.main(["call", "gmail", "send"]) == 0          # not an exact name: a search, nothing runs
    capsys.readouterr()
    assert ran(fake) == []
    assert cli.main(["call", "gmail", "send_email", '{"to":"a@b.c"}']) == 0
    assert json.loads(capsys.readouterr().out)["ran"] == "GMAIL_SEND_EMAIL"


def test_call_with_json_but_no_exact_action_refuses(fake, capsys):
    assert cli.main(["call", "gmail", "send", '{"to":"a@b.c"}']) == 1
    assert ran(fake) == []


def test_call_agentos_lists_and_runs(fake, capsys):
    assert cli.main(["call", "agentos"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["action"] == "tasks_list"
    assert cli.main(["call", "agentos", "tasks_list", '{"status":"open"}']) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "ran": "tasks_list", "args": {"status": "open"}}


def test_describe_never_runs(fake, capsys):
    assert cli.main(["call", "gmail", "send_email", "--describe"]) == 0
    assert "to" in capsys.readouterr().out and ran(fake) == []


def test_old_forms_still_work(fake, capsys):
    assert cli.main(["call", "tasks_list", "{}"]) == 0
    assert json.loads(capsys.readouterr().out)["ran"] == "tasks_list"
    assert cli.main(["call", "GMAIL_SEND_EMAIL", '{"to":"x"}']) == 0
    assert json.loads(capsys.readouterr().out)["ran"] == "GMAIL_SEND_EMAIL"
    assert cli.main(["integrations", "run", "tasks_list", "{}"]) == 0
    assert cli.main(["tools", "--q", "email"]) == 0
    capsys.readouterr()


def test_mcp_server_tools_run_as_app_actions(fake, capsys):
    from agentos_cli import call
    assert call.is_app_slug("mcp_15cfc985_stripe_analytics_60f66b5c") and not call.is_app_slug("tasks_list")
    assert cli.main(["call", "mcp_15cfc985_stripe_analytics_60f66b5c", "{}"]) == 0
    assert ran(fake)[-1]["tool_slug"] == "mcp_15cfc985_stripe_analytics_60f66b5c"


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


def test_unknown_agentos_name_is_a_search_not_a_run(fake, monkeypatch, capsys):
    orig = fake.call

    def call(tool, **kw):
        if tool == "workspace_tools" and kw.get("name") == "tasks":
            return {"found": False, "name": "tasks", "message": "No authorized AgentOS tool has this name."}
        return orig(tool, **kw)
    monkeypatch.setattr(fake, "call", call)
    assert cli.main(["call", "agentos", "tasks"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["action"] == "tasks_list"
