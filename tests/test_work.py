import json

from agentos_cli import __version__, cli, work


class FakeClient:
    server_info = {"version": "t"}

    def __init__(self, result):
        self.result = result
        self.calls = []

    def initialize(self, client_name=""):
        pass

    def list_tools(self):
        return ["context", "workspace_call", "workspace_tools"]

    def call(self, tool, **args):
        self.calls.append((tool, args))
        return self.result


def test_call_routes_top_level_and_workspace_tools(monkeypatch):
    monkeypatch.setattr(work, "_TOP", None)
    c = FakeClient({"ok": True})
    work.call(c, "context", {"subject": "x"})
    work.call(c, "tasks_list", {"status": "open"})
    assert c.calls[0] == ("context", {"subject": "x"})
    assert c.calls[1] == ("workspace_call", {"name": "tasks_list", "arguments": json.dumps({"status": "open"})})


def test_exit_codes_for_results():
    assert work.outcome({"ok": True}) == 0
    assert work.outcome({"queued_for_approval": True}) == 2
    assert work.outcome({"status": "denied"}) == 1


def test_aos_call_prints_json_and_returns_approval_code(home, monkeypatch, capsys):
    monkeypatch.setattr(work, "_TOP", None)
    c = FakeClient({"queued_for_approval": True, "approval_id": "a1"})
    monkeypatch.setattr(work, "connect", lambda timeout=60: c)
    assert cli.main(["call", "crm_contacts_create", '{"name":"Ada"}']) == 2
    assert json.loads(capsys.readouterr().out)["approval_id"] == "a1"


def test_not_signed_in_is_a_clean_error(home, capsys):
    assert cli.main(["whoami"]) == 1
    assert "aos login" in capsys.readouterr().err
    assert cli.main(["tools"]) == 1


def test_help_and_version(capsys):
    assert cli.main([]) == 0
    assert cli.main(["--version"]) == 0
    assert f"aos {__version__}" in capsys.readouterr().out
