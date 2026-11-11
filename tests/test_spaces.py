"""Space verbs against a fake Files server with production's limits: a reply is cut (and says so)
once twice its JSON passes 24,000 characters, and a folder listing keeps only 10 entries."""
import json
import threading

import pytest

from agentos_cli import mcp, spaces

REPLY_CAP = 24_000
LIST_CAP = 10


class FakeFS:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.url = "https://fake.test/api/mcp"
        self._token = "tok"

    def call(self, tool, **args):
        assert tool == "fs"
        verb, _, path = args["cmd"].partition(" ")
        with self.lock:
            self.calls.append(verb)
            if verb == "write":
                self.files[path] = args["content"]
                return {"ok": True, "output": path}
            if verb in ("cat", "stat") and path not in self.files:
                raise mcp.ToolError(f"{verb}: {path}: no such file\nRecovery: ...")
            if verb == "cat":
                out = {"ok": True, "output": self.files[path], "truncated": False}
                if 2 * len(json.dumps(out)) > REPLY_CAP:  # text + structuredContent, as on the real server
                    out = {"ok": True, "output": self.files[path][:1024], "truncated": True, "_truncated": True}
                return out
            if verb == "stat":
                return {"ok": True, "stat": {"path": path, "size": len(self.files[path])}}
            if verb == "ls":
                prefix = path.rstrip("/") + "/"
                kids = sorted({p[len(prefix):].split("/")[0] for p in self.files if p.startswith(prefix)})
                files = [k for k in kids if prefix + k in self.files]
                folders = [k for k in kids if prefix + k not in self.files]
                return {"ok": True, "entries": {"files": [{"name": f} for f in files[:LIST_CAP]],
                                                "folders": folders[:LIST_CAP]}}
        raise AssertionError(verb)


@pytest.fixture
def fs(home, monkeypatch):
    fake = FakeFS()
    real = spaces.Remote
    monkeypatch.setattr(spaces, "Remote", lambda client=None: real(fake))
    monkeypatch.setenv("AOS_AS", "alice")
    spaces._ME.clear()
    return fake


def run(monkeypatch, folder, *argv, who=None):
    monkeypatch.chdir(folder)
    if who:
        monkeypatch.setenv("AOS_AS", who)
    try:
        return spaces.main(list(argv))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def two_folders(tmp_path, monkeypatch, name="s"):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert run(monkeypatch, a, "link", name) == 0
    assert run(monkeypatch, b, "link", name) == 0
    return a, b


def test_push_pull_round_trip(fs, tmp_path, monkeypatch):
    a, b = two_folders(tmp_path, monkeypatch)
    (a / "notes.md").write_text("hello\n")
    (a / "sub").mkdir()
    (a / "sub" / "x.json").write_text('{"x": 1}')
    assert run(monkeypatch, a, "push", "-m", "first") == 0
    assert run(monkeypatch, b, "pull") == 0
    assert (b / "notes.md").read_text() == "hello\n"
    assert (b / "sub" / "x.json").read_text() == '{"x": 1}'


def test_conflict_blocks_push_until_resolved_and_their_edit_survives(fs, tmp_path, monkeypatch, capsys):
    a, b = two_folders(tmp_path, monkeypatch)
    (a / "notes.md").write_text("v1\n")
    run(monkeypatch, a, "push")
    run(monkeypatch, b, "pull")
    (a / "notes.md").write_text("v1\nfrom A\n")
    assert run(monkeypatch, a, "push", who="alice") == 0
    (b / "notes.md").write_text("v1\nfrom B\n")
    assert run(monkeypatch, b, "pull", who="bob") == 1
    assert (b / "notes.md.theirs").read_text() == "v1\nfrom A\n"
    capsys.readouterr()
    # diff compares with the real latest (A's text), not the stale base
    run(monkeypatch, b, "diff", who="bob")
    out = capsys.readouterr().out
    assert "-from A" in out and "+from B" in out
    # the old bug: this push silently replaced A's edit
    assert run(monkeypatch, b, "push", who="bob") == 1
    assert json.loads(fs.files["/aos/s/versions/v000002.json"])["n"] == 2
    assert "/aos/s/versions/v000003.json" not in fs.files
    (b / "notes.md").write_text("v1\nfrom A\nfrom B\n")
    assert run(monkeypatch, b, "resolve", "notes.md", who="bob") == 0
    assert not (b / "notes.md.theirs").exists()
    assert run(monkeypatch, b, "push", who="bob") == 0
    assert run(monkeypatch, a, "pull", who="alice") == 0
    assert (a / "notes.md").read_text() == "v1\nfrom A\nfrom B\n"


def test_big_file_round_trips_through_the_reply_cap(fs, tmp_path, monkeypatch):
    import os
    a, b = two_folders(tmp_path, monkeypatch)
    blob = os.urandom(120_000)  # incompressible: ~160 KB of base64
    (a / "big.bin").write_bytes(blob)
    assert run(monkeypatch, a, "push") == 0
    parts = [p for p in fs.files if p.startswith("/aos/s/objects/") and p.count(".") == 2]
    assert len(parts) > 5
    assert run(monkeypatch, b, "pull") == 0
    assert (b / "big.bin").read_bytes() == blob


def test_big_tree_version_round_trips(fs, tmp_path, monkeypatch):
    a, b = two_folders(tmp_path, monkeypatch)
    for i in range(400):  # a version record of ~45 KB
        (a / f"file-{i:04d}.txt").write_text(f"{i}\n")
    assert run(monkeypatch, a, "push") == 0
    assert fs.files["/aos/s/versions/v000001.json"].startswith(spaces.CHUNK_HEADER)
    assert run(monkeypatch, b, "pull") == 0
    assert len(list(b.glob("file-*.txt"))) == 400


def test_more_versions_and_events_than_a_listing_shows(fs, tmp_path, monkeypatch, capsys):
    a, b = two_folders(tmp_path, monkeypatch)
    for i in range(13):
        (a / "n.txt").write_text(str(i))
        assert run(monkeypatch, a, "push") == 0
        assert run(monkeypatch, a, "claim", f"t{i}", who="alice") == 0
    capsys.readouterr()
    run(monkeypatch, b, "status", who="bob")
    out = capsys.readouterr().out
    assert "latest v13" in out
    assert "t12: alice" in out  # the 13th claim, past any listing cut
    assert run(monkeypatch, b, "claim", "t12", who="bob") == 3


def test_two_agents_on_one_computer_are_two_people(fs, tmp_path, monkeypatch):
    a, b = two_folders(tmp_path, monkeypatch)
    monkeypatch.delenv("AOS_AS")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaaaaaaa-1111")
    spaces._ME.clear()
    assert run(monkeypatch, a, "claim", "notes") == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "bbbbbbbb-2222")
    spaces._ME.clear()
    assert run(monkeypatch, b, "claim", "notes") == 3


def test_second_pull_reads_from_the_cache(fs, tmp_path, monkeypatch):
    a, b = two_folders(tmp_path, monkeypatch)
    (a / "x.txt").write_text("x")
    run(monkeypatch, a, "push")
    run(monkeypatch, b, "pull")
    (b / "x.txt").unlink()
    fs.calls.clear()
    assert run(monkeypatch, b, "restore", "x.txt") == 0
    assert (b / "x.txt").read_text() == "x"
    assert "cat" not in fs.calls


def test_release_of_someone_elses_claim_needs_force(fs, tmp_path, monkeypatch):
    a, b = two_folders(tmp_path, monkeypatch)
    assert run(monkeypatch, a, "claim", "notes", who="alice") == 0
    assert run(monkeypatch, b, "release", "notes", who="bob") == 3
    assert run(monkeypatch, b, "release", "notes", "--force", who="bob") == 0
    assert run(monkeypatch, b, "claim", "notes", who="bob") == 0


def test_handoff_releases_my_claims_and_wait_sees_it(fs, tmp_path, monkeypatch, capsys):
    a, b = two_folders(tmp_path, monkeypatch)
    run(monkeypatch, a, "claim", "one", who="alice")
    run(monkeypatch, a, "claim", "two", who="alice")
    assert run(monkeypatch, a, "handoff", "-m", "yours", "--to", "bob", who="alice") == 0
    capsys.readouterr()
    run(monkeypatch, b, "status", who="bob")
    assert "claims:" not in capsys.readouterr().out
    assert run(monkeypatch, b, "claim", "one", who="bob") == 0
    kinds = [json.loads(v)["kind"] for k, v in sorted(fs.files.items()) if "/events/e" in k]
    assert kinds == ["claim", "claim", "release", "release", "handoff", "claim"]


def test_progress_steps_once_per_object_and_is_silent_without_a_terminal(fs, tmp_path, monkeypatch, capsys):
    a, b = two_folders(tmp_path, monkeypatch)
    for i in range(8):
        (a / f"f{i}.txt").write_text(str(i))
    steps = []
    real_bar = spaces._bar

    def counting_bar(label, total):
        steps.append(("start", label, total))
        return real_bar(label, total)

    monkeypatch.setattr(spaces, "_bar", counting_bar)
    sp_put = spaces.Space.put_objects

    def put(self, items, step=None):
        calls = []
        n = sp_put(self, items, lambda: calls.append(1))
        steps.append(("steps", len(calls)))
        return n

    monkeypatch.setattr(spaces.Space, "put_objects", put)
    assert run(monkeypatch, a, "push") == 0
    assert ("steps", 8) in steps and steps[0][2] == 8 and "Uploading 8 files" in steps[0][1]
    assert "Uploading" not in capsys.readouterr().err  # not a terminal: no bar
    assert run(monkeypatch, b, "pull") == 0
    assert any(s[0] == "start" and s[1].startswith("Downloading 8 files") for s in steps)


def test_wait_label_says_what_and_where():
    import argparse
    a = argparse.Namespace(for_="handoff", from_="alice", to=None, path=None)
    assert spaces._wait_label(a, "deck", False) == "Waiting for a handoff from alice in 'deck'"
    a = argparse.Namespace(for_=None, from_=None, to=None, path="src/")
    assert spaces._wait_label(a, "deck", True).startswith("Watching for a push, claim, release or handoff touching src/")
