"""aos sync, against the fake Files server from test_spaces."""
import json

from agentos_cli import cli, spaces, sync
from test_spaces import FakeFS, fs, run, two_folders  # noqa: F401  (fs is a fixture)


def test_sync_once_pushes_local_changes(fs, tmp_path, monkeypatch):
    a, b = two_folders(tmp_path, monkeypatch)
    (a / "notes.md").write_text("from A\n")
    assert sync.run(a, once=True) == 0
    assert run(monkeypatch, b, "pull") == 0
    assert (b / "notes.md").read_text() == "from A\n"


def test_sync_once_pulls_then_pushes(fs, tmp_path, monkeypatch):
    a, b = two_folders(tmp_path, monkeypatch)
    (a / "one.md").write_text("1\n")
    run(monkeypatch, a, "push")
    (b / "two.md").write_text("2\n")
    assert sync.run(b, once=True) == 0
    assert (b / "one.md").read_text() == "1\n"
    assert run(monkeypatch, a, "pull") == 0 and (a / "two.md").read_text() == "2\n"


def test_sync_stops_on_a_conflict_and_overwrites_nothing(fs, tmp_path, monkeypatch, capsys):
    a, b = two_folders(tmp_path, monkeypatch)
    (a / "n.md").write_text("base\n")
    run(monkeypatch, a, "push")
    run(monkeypatch, b, "pull")
    (a / "n.md").write_text("base\nA\n")
    run(monkeypatch, a, "push", who="alice")
    (b / "n.md").write_text("base\nB\n")
    monkeypatch.setenv("AOS_AS", "bob")
    assert sync.run(b, once=True) == 1
    assert (b / "n.md").read_text() == "base\nB\n" and (b / "n.md.theirs").read_text() == "base\nA\n"
    assert not any("v000003" in k for k in fs.files)
    assert sync.run(b, once=True) == 1  # still stopped until resolved


def test_sync_main_outside_a_linked_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert sync.main([]) == 1
    assert "aos space link" in capsys.readouterr().err


def test_status_shows_sync_state_for_a_linked_folder(fs, tmp_path, monkeypatch, capsys):
    a, _ = two_folders(tmp_path, monkeypatch)
    monkeypatch.chdir(a)
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["folder"]["space"] and isinstance(d["old_background_sync"], list)


def test_help_sync_topic(capsys):
    assert cli.main(["help", "sync"]) == 0
    out = capsys.readouterr().out
    assert "aos space sync" in out and "no background mode" in out
    assert cli.main(["help", "auto-sync"]) == 0
