import json

from agentos_cli import cli


def test_status_overview_json_outside_a_space(home, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["setup", "--yes", "--agents", "codex"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["signed_in"] is None and info["folder"] is None
    codex = next(a for a in info["agents"] if a["agent"] == "Codex")
    assert codex["mcp"] == "ours" and codex["skills"] == 6
    assert cli.main(["status"]) == 0  # the human view, not linked: overview only


def test_status_in_a_linked_folder_names_the_space(home, monkeypatch, tmp_path, capsys):
    (tmp_path / ".aos").mkdir()
    (tmp_path / ".aos" / "state.json").write_text(json.dumps({"space": "deck", "version": 3, "tree": {}}))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["folder"]["space"] == "deck"


def test_help_topics_and_aliases(capsys):
    for t in ("overview", "mcp", "link", "sync", "integrations", "tools", "secrets", "status", "setup", "skills", "space"):
        assert cli.main(["help", t]) == 0
    out = capsys.readouterr().out
    assert "aos space sync" in out and "AgentOS" in out and "GMAIL_SEND_EMAIL" in out
    assert cli.main(["help", "nope"]) == 2


def test_capture_hidden_from_help_when_not_installed(home, capsys):
    assert cli.main([]) == 0
    assert "aos capture" not in capsys.readouterr().out
