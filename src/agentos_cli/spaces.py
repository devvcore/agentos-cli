#!/usr/bin/env python3
"""aos spaces (same code as plugins/claude-code-agentos/scripts/aos_spaces.py): Git-shaped verbs for working in an AgentOS space.

A space is where a piece of work lives. Its state is kept in workspace Files
under /aos/<space>/, so any person or agent with AgentOS access can use it:

  objects/<sha256>     file contents, gzip+base64, content-addressed
  versions/vNNNNNN.json  {n, parent, author, message, at, tree: {path: sha}}
  events/<stamp>.json    claims, releases and handoffs, append-only

Locally, a linked folder holds .aos/state.json: the space, the version the
folder is based on, and the tree of that version, so status/diff/pull work
without a round trip and pull fetches only changed objects.

No merges. Pull never overwrites a file you changed: the incoming copy lands
next to it as <file>.theirs and the conflict is reported. Claims are how
agents avoid the conflict in the first place.
"""
from __future__ import annotations

import argparse
import base64
import difflib
import fnmatch
import getpass
import gzip
import hashlib
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = "/aos"
STATE_DIR = ".aos"
DEFAULT_IGNORE = [".git", ".aos", "node_modules", "__pycache__", ".DS_Store", "*.theirs", ".venv", "venv", "dist", "build", ".next"]
MAX_FILE_BYTES = 5 * 1024 * 1024
CLAIM_TTL_MIN = 60


# ---------------------------------------------------------------- remote

class Remote:
    """Space storage on workspace Files, through the AgentOS MCP endpoint."""

    def __init__(self, client=None):
        if client is None:
            from . import mcp
            client = mcp.connect(timeout=60)
        self.c = client

    def _fs(self, cmd, content=None):
        args = {"cmd": cmd}
        if content is not None:
            args["content"] = content
        res = self.c.call("fs", **args)
        if isinstance(res, dict) and res.get("ok") is False:
            raise FileNotFoundError(res.get("error") or res.get("output") or cmd)
        return res

    def read(self, path):
        res = self._fs(f"cat {path}")
        if not isinstance(res, dict) or "output" not in res:
            raise FileNotFoundError(path)
        if res.get("truncated"):
            raise RuntimeError(f"{path} came back truncated")
        return res["output"]

    def write(self, path, text):
        self._fs(f"write {path}", text)

    def exists(self, path):
        try:
            res = self._fs(f"stat {path}")
        except (FileNotFoundError, RuntimeError):
            return False
        return isinstance(res, dict) and bool(res.get("stat"))

    def ls(self, path):
        try:
            res = self._fs(f"ls {path}")
        except (FileNotFoundError, RuntimeError):
            return [], []
        ent = (res or {}).get("entries") or {}
        files = [f["name"] for f in ent.get("files", [])]
        folders = [f["name"] if isinstance(f, dict) else f for f in ent.get("folders", [])]
        return folders, files


class Space:
    def __init__(self, remote: Remote, name: str):
        self.r = remote
        self.name = name
        self.base = f"{ROOT}/{name}"

    # objects
    def put_object(self, sha, data: bytes):
        path = f"{self.base}/objects/{sha}"
        if self.r.exists(path):
            return False
        self.r.write(path, base64.b64encode(gzip.compress(data)).decode())
        return True

    def get_object(self, sha) -> bytes:
        data = gzip.decompress(base64.b64decode(self.r.read(f"{self.base}/objects/{sha}")))
        if hashlib.sha256(data).hexdigest() != sha:
            raise RuntimeError(f"object {sha[:12]} failed its checksum")
        return data

    # versions
    def version_numbers(self):
        _, files = self.r.ls(f"{self.base}/versions")
        return sorted(int(m.group(1)) for f in files if (m := re.match(r"v(\d+)\.json$", f)))

    def head(self):
        nums = self.version_numbers()
        return nums[-1] if nums else 0

    def version(self, n):
        if n == 0:
            return {"n": 0, "tree": {}, "author": None, "message": "(empty)", "at": None, "parent": None}
        return json.loads(self.r.read(f"{self.base}/versions/v{n:06d}.json"))

    def commit(self, parent, author, message, tree):
        n = parent + 1
        path = f"{self.base}/versions/v{n:06d}.json"
        if self.r.exists(path):
            return None  # someone else pushed first
        nonce = secrets.token_hex(4)
        rec = {"n": n, "parent": parent, "author": author, "message": message,
               "at": now_iso(), "nonce": nonce, "tree": tree}
        self.r.write(path, json.dumps(rec, indent=1, sort_keys=True))
        # read back: if two writers raced, only one nonce survives
        if json.loads(self.r.read(path)).get("nonce") != nonce:
            return None
        return rec

    # events
    def emit(self, kind, author, **fields):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        rec = {"kind": kind, "author": author, "at": now_iso(), **fields}
        self.r.write(f"{self.base}/events/{stamp}-{secrets.token_hex(3)}.json", json.dumps(rec, sort_keys=True))
        return rec

    def event_names(self):
        _, files = self.r.ls(f"{self.base}/events")
        return sorted(f for f in files if f.endswith(".json"))

    def event(self, name):
        rec = json.loads(self.r.read(f"{self.base}/events/{name}"))
        rec["id"] = name[:-5]
        return rec

    def active_claims(self):
        claims = {}
        for name in self.event_names():
            ev = self.event(name)
            if ev["kind"] == "claim":
                claims[ev["topic"]] = ev
            elif ev["kind"] == "release":
                claims.pop(ev["topic"], None)
        t = datetime.now(timezone.utc)
        return {k: v for k, v in claims.items() if parse_iso(v["expires"]) > t}


# ---------------------------------------------------------------- local

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def whoami(args):
    return (getattr(args, "as_", None) or os.environ.get("AOS_AS")
            or f"{getpass.getuser()}@{socket.gethostname().split('.')[0]}")


def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if not s:
        raise SystemExit("aos: a space needs a name with letters or digits")
    return s


def find_root(start: Path):
    p = start.resolve()
    for d in [p, *p.parents]:
        if (d / STATE_DIR / "state.json").exists():
            return d
    return None


def load_state(root):
    return json.loads((root / STATE_DIR / "state.json").read_text())


def save_state(root, st):
    (root / STATE_DIR).mkdir(exist_ok=True)
    tmp = root / STATE_DIR / "state.json.tmp"
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True))
    tmp.replace(root / STATE_DIR / "state.json")


def ignores(root):
    pats = list(DEFAULT_IGNORE)
    f = root / ".aosignore"
    if f.exists():
        pats += [l.strip() for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")]
    return pats


def ignored(rel, pats):
    parts = rel.split("/")
    return any(fnmatch.fnmatch(p, pat) for p in parts for pat in pats) or any(fnmatch.fnmatch(rel, pat) for pat in pats)


def scan(root):
    """{relative path: sha256} for every tracked file under root."""
    pats = ignores(root)
    tree = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
        dirnames[:] = [d for d in dirnames if not ignored(rel_dir + d, pats)]
        for fn in filenames:
            rel = rel_dir + fn
            if ignored(rel, pats):
                continue
            p = Path(dirpath) / fn
            if p.is_symlink() or p.stat().st_size > MAX_FILE_BYTES:
                continue
            tree[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return tree


def tree_diff(old, new):
    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = sorted(k for k in new if k in old and old[k] != new[k])
    return added, changed, removed


def print_diff(added, changed, removed, indent="  "):
    for k in added:
        print(f"{indent}+ {k}")
    for k in changed:
        print(f"{indent}~ {k}")
    for k in removed:
        print(f"{indent}- {k}")
    if not (added or changed or removed):
        print(f"{indent}(no changes)")


def need_root():
    root = find_root(Path.cwd())
    if not root:
        raise SystemExit("aos: this folder isn't linked to a space. Run `aos link <space>` first.")
    return root


def open_space(st, remote=None):
    return Space(remote or Remote(), st["space"])


def covers(topic, rel):
    """A claim's topic covers a path when it is that path or a folder above it."""
    t = topic.strip("/")
    return rel == t or rel.startswith(t + "/")


def claimed_by_others(claims, paths, me):
    """{path: claim} for paths someone else holds a live claim on."""
    out = {}
    for rel in paths:
        for topic, c in claims.items():
            if c["author"] != me and covers(topic, rel):
                out[rel] = c
                break
    return out


def _text(data: bytes):
    try:
        return data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def print_line_diff(rel, old: bytes, new: bytes, a_label, b_label):
    a, b = _text(old), _text(new)
    if a is None or b is None:
        print(f"  (binary file {rel} differs)")
        return
    for line in difflib.unified_diff(a, b, f"{a_label}/{rel}", f"{b_label}/{rel}"):
        print(line, end="" if line.endswith("\n") else "\n")


def version_or_exit(sp, n):
    if n != 0 and n not in sp.version_numbers():
        raise SystemExit(f"aos: there is no v{n} in '{sp.name}' (latest is v{sp.head()}).")
    return sp.version(n)


# ---------------------------------------------------------------- verbs

def cmd_link(a):
    root = Path(a.dir or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = slug(a.space or root.name)
    sp = Space(Remote(), name)
    head = sp.head()
    save_state(root, {"space": name, "version": 0, "tree": {}, "linked_at": now_iso()})
    print(f"Linked {root} to space '{name}' (latest version: {head or 'none yet'}).")
    if head:
        print("Run `aos pull` to bring it down.")
    return 0


def cmd_spaces(a):
    folders, _ = Remote().ls(ROOT)
    for f in sorted(folders):
        print(f)
    if not folders:
        print("(no spaces yet)")
    return 0


def cmd_status(a):
    root = need_root()
    st = load_state(root)
    sp = open_space(st)
    local = scan(root)
    head = sp.head()
    print(f"space {st['space']}  ·  you are on v{st['version']}  ·  latest v{head}")
    if head > st["version"]:
        hv = sp.version(head)
        print(f"  behind by {head - st['version']}: latest by {hv['author']} — {hv['message']}")
    print("local changes since v%d:" % st["version"])
    added, changed, removed = tree_diff(st["tree"], local)
    print_diff(added, changed, removed)
    claims = sp.active_claims()
    for rel, c in sorted(claimed_by_others(claims, added + changed + removed, whoami(a)).items()):
        print(f"  ! {rel}: you changed a file which {c['author']} has claimed (until {c['expires']}). "
              "`aos push` will refuse it; talk to them or pass --force.")
    if claims:
        print("claims:")
        for t, c in sorted(claims.items()):
            print(f"  {t}: {c['author']} until {c['expires']}" + (f" — {c['note']}" if c.get("note") else ""))
    return 0


def _stage_zip(path):
    # resolve(): on macOS the temp dir is /var/... but resolves to /private/var/...
    tmp = Path(tempfile.mkdtemp(prefix="aos-zip-")).resolve()
    with zipfile.ZipFile(path) as z:
        for m in z.infolist():
            target = (tmp / m.filename).resolve()
            if target != tmp and not target.is_relative_to(tmp):
                raise SystemExit(f"aos: refusing unsafe path in zip: {m.filename}")
        z.extractall(tmp)
    return tmp


def cmd_push(a):
    root = need_root()
    st = load_state(root)
    src = root
    if a.zip:
        src = _stage_zip(a.zip)
    sp = open_space(st)
    head = sp.head()
    if head != st["version"]:
        raise SystemExit(f"aos: the space moved to v{head} (you're on v{st['version']}). Run `aos pull` first.")
    local = scan(src)
    added, changed, removed = tree_diff(st["tree"], local)
    if not (added or changed or removed):
        print("Nothing to push.")
        return 0
    held = claimed_by_others(sp.active_claims(), added + changed + removed, whoami(a))
    if held and not a.force:
        for rel, c in sorted(held.items()):
            print(f"aos: {rel} is claimed by {c['author']} until {c['expires']}"
                  + (f" — {c['note']}" if c.get("note") else ""), file=sys.stderr)
        print("aos: nothing pushed. Ask them to release it, or pass --force.", file=sys.stderr)
        return 3
    uploaded = 0
    for rel in added + changed:
        if sp.put_object(local[rel], (src / rel).read_bytes()):
            uploaded += 1
    rec = sp.commit(head, whoami(a), a.message or "(no message)", local)
    if rec is None:
        raise SystemExit("aos: someone pushed at the same moment. Run `aos pull`, then push again.")
    if not a.zip:
        st.update(version=rec["n"], tree=local)
        save_state(root, st)
    print(f"Pushed v{rec['n']} to '{st['space']}': {len(added)} added, {len(changed)} changed, {len(removed)} removed ({uploaded} new objects).")
    return 0


def cmd_pull(a):
    root = need_root()
    st = load_state(root)
    sp = open_space(st)
    target = a.version or sp.head()
    if target == st["version"]:
        print(f"Already on v{target}.")
        return 0
    theirs = sp.version(target)["tree"]
    base = st["tree"]
    local = scan(root)
    added, changed, removed = tree_diff(base, theirs)
    conflicts, fetched = [], 0
    for rel in added + changed:
        p = root / rel
        mine = local.get(rel)
        if mine == theirs[rel]:
            continue
        if mine is not None and mine != base.get(rel):  # you changed it too
            (root / (rel + ".theirs")).write_bytes(sp.get_object(theirs[rel]))
            conflicts.append(rel)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(sp.get_object(theirs[rel]))
        fetched += 1
    for rel in removed:
        p = root / rel
        if p.exists() and local.get(rel) == base.get(rel):
            p.unlink()
        elif p.exists():
            conflicts.append(rel + " (deleted upstream, changed here)")
    new_tree = dict(theirs)
    for rel in conflicts:
        rel = rel.split(" (")[0]
        if rel in base:
            new_tree[rel] = base[rel]  # keep your edit showing as a local change
        else:
            new_tree.pop(rel, None)
    st.update(version=target, tree=new_tree)
    save_state(root, st)
    print(f"Pulled v{target}: fetched {fetched} file(s) that changed ({len(added)} added, {len(changed)} changed, {len(removed)} removed upstream).")
    for c in conflicts:
        print(f"  conflict: {c} — your copy kept; theirs is next to it as .theirs")
    return 1 if conflicts else 0


def cmd_diff(a):
    """`aos diff`: local vs your version. `aos diff N`: vN vs local. `aos diff N M`: vN vs vM."""
    root = need_root()
    st = load_state(root)
    sp = open_space(st)
    if a.v1 is None or a.v2 is None:
        base_n = st["version"] if a.v1 is None else a.v1
        old = st["tree"] if a.v1 is None else version_or_exit(sp, a.v1)["tree"]
        new = scan(root)
        print(f"local vs v{base_n}:")
        added, changed, removed = tree_diff(old, new)
        print_diff(added, changed, removed)
        for rel in changed:
            print_line_diff(rel, sp.get_object(old[rel]), (root / rel).read_bytes(), f"v{base_n}", "local")
        return 0
    old, new = version_or_exit(sp, a.v1)["tree"], version_or_exit(sp, a.v2)["tree"]
    print(f"v{a.v1} → v{a.v2}:")
    added, changed, removed = tree_diff(old, new)
    print_diff(added, changed, removed)
    for rel in changed:
        print_line_diff(rel, sp.get_object(old[rel]), sp.get_object(new[rel]), f"v{a.v1}", f"v{a.v2}")
    return 0


def cmd_restore(a):
    """Put files back as they were in a version (default: the one you're on). Local only; push to share."""
    root = need_root()
    st = load_state(root)
    sp = open_space(st)
    n = st["version"] if a.version is None else a.version
    tree = st["tree"] if a.version is None else version_or_exit(sp, n)["tree"]
    restored, removed = 0, 0
    for rel in a.paths:
        rel = rel.strip("/")
        hits = [k for k in tree if covers(rel, k)]
        p = root / rel
        if not hits:
            if p.is_file():
                p.unlink()
                removed += 1
                continue
            raise SystemExit(f"aos: {rel} isn't in v{n}.")
        for k in hits:
            q = root / k
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_bytes(sp.get_object(tree[k]))
            restored += 1
    print(f"Restored {restored} file(s) from v{n}" + (f", removed {removed} not in v{n}" if removed else "")
          + ". Run `aos push` to share it.")
    return 0


def cmd_log(a):
    st = load_state(need_root()) if not a.space else {"space": slug(a.space)}
    sp = open_space(st)
    items = []
    for n in sp.version_numbers():
        v = sp.version(n)
        items.append((v["at"], f"v{n:<4} push     {v['author']}: {v['message']}"))
    for name in sp.event_names():
        e = sp.event(name)
        extra = e.get("note") or e.get("message") or ""
        topic = f" [{e['topic']}]" if e.get("topic") else ""
        items.append((e["at"], f"      {e['kind']:<8} {e['author']}{topic}" + (f": {extra}" if extra else "")))
    for at, line in sorted(items)[-a.n:]:
        print(f"{at}  {line}")
    if not items:
        print("(nothing yet)")
    return 0


def cmd_claim(a):
    st = load_state(need_root())
    sp = open_space(st)
    me = whoami(a)
    held = sp.active_claims().get(a.topic)
    if held and held["author"] != me:
        print(f"'{a.topic}' is claimed by {held['author']} until {held['expires']}" + (f" — {held['note']}" if held.get("note") else ""))
        return 3
    exp = (datetime.now(timezone.utc) + timedelta(minutes=a.ttl)).isoformat(timespec="seconds").replace("+00:00", "Z")
    sp.emit("claim", me, topic=a.topic, expires=exp, note=a.message or "")
    print(f"Claimed '{a.topic}' until {exp}.")
    return 0


def cmd_release(a):
    st = load_state(need_root())
    sp = open_space(st)
    sp.emit("release", whoami(a), topic=a.topic, note=a.message or "")
    print(f"Released '{a.topic}'.")
    return 0


def cmd_handoff(a):
    st = load_state(need_root())
    sp = open_space(st)
    me = whoami(a)
    for topic, c in sp.active_claims().items():
        if c["author"] == me:
            sp.emit("release", me, topic=topic, note="handoff")
    sp.emit("handoff", me, message=a.message, to=a.to or "", version=st["version"])
    print(f"Handed off at v{st['version']}" + (f" to {a.to}" if a.to else "") + ".")
    return 0


def _matches(ev, a, me):
    if a.for_ and ev["kind"] != a.for_:
        return False
    if a.from_ and a.from_ not in ev.get("author", ""):
        return False
    if not a.include_mine and ev.get("author") == me:
        return False
    if a.to and ev.get("to") and a.to != ev.get("to"):
        return False
    if a.path:
        paths = ev.get("paths") or []
        if not any(p.startswith(a.path) for p in paths):
            return False
    return True


def _new_events(sp, seen_versions, seen_events):
    out = []
    for n in sp.version_numbers():
        if n in seen_versions:
            continue
        seen_versions.add(n)
        v = sp.version(n)
        prev = sp.version(v["parent"])["tree"] if v.get("parent") else {}
        ad, ch, rm = tree_diff(prev, v["tree"])
        out.append({"kind": "push", "author": v["author"], "at": v["at"], "version": n,
                    "message": v["message"], "paths": ad + ch + rm})
    for name in sp.event_names():
        if name in seen_events:
            continue
        seen_events.add(name)
        out.append(sp.event(name))
    return out


def cmd_wait(a, forever=False):
    st = load_state(need_root()) if not a.space else {"space": slug(a.space)}
    sp = open_space(st)
    me = whoami(a)
    seen_v, seen_e = set(sp.version_numbers()), set(sp.event_names())
    deadline = time.time() + a.timeout if a.timeout else None
    while True:
        for ev in _new_events(sp, seen_v, seen_e):
            if _matches(ev, a, me):
                print(json.dumps(ev, sort_keys=True), flush=True)
                if not forever:
                    return 0
        if deadline and time.time() > deadline:
            print("aos: timed out waiting", file=sys.stderr)
            return 4
        time.sleep(a.interval)


# ---------------------------------------------------------------- cli

def build_parser():
    p = argparse.ArgumentParser(prog="aos", description="Work in AgentOS spaces with Git-shaped verbs.")
    p.add_argument("--as", dest="as_", help="who you are in the log (default: $AOS_AS or user@host)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("link", help="tie this folder to a space (created on first push)")
    s.add_argument("space", nargs="?")
    s.add_argument("--dir")
    s.set_defaults(fn=cmd_link)

    sub.add_parser("spaces", help="list spaces").set_defaults(fn=cmd_spaces)
    sub.add_parser("status", help="local changes, how far behind, open claims").set_defaults(fn=cmd_status)

    s = sub.add_parser("push", help="upload a new version")
    s.add_argument("-m", "--message")
    s.add_argument("--zip", help="push the contents of a zip instead of this folder")
    s.add_argument("--force", action="store_true", help="push even files someone else has claimed")
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("pull", help="download only what changed")
    s.add_argument("--version", type=int)
    s.set_defaults(fn=cmd_pull)

    s = sub.add_parser("diff", help="local vs your version, or between two versions")
    s.add_argument("v1", nargs="?", type=int)
    s.add_argument("v2", nargs="?", type=int)
    s.set_defaults(fn=cmd_diff)

    s = sub.add_parser("restore", help="put files back as they were in a version (local only)")
    s.add_argument("paths", nargs="+")
    s.add_argument("--version", type=int, help="default: the version you're on")
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("log", help="pushes, claims and handoffs, oldest first")
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--space")
    s.set_defaults(fn=cmd_log)

    for name, fn, h in [("claim", cmd_claim, "say you're working on something (expires)"),
                        ("release", cmd_release, "drop a claim")]:
        s = sub.add_parser(name, help=h)
        s.add_argument("topic")
        s.add_argument("-m", "--message")
        if name == "claim":
            s.add_argument("--ttl", type=int, default=CLAIM_TTL_MIN, help="minutes (default 60)")
        s.set_defaults(fn=fn)

    s = sub.add_parser("handoff", help="stop, release your claims, leave a note")
    s.add_argument("-m", "--message", required=True)
    s.add_argument("--to")
    s.set_defaults(fn=cmd_handoff)

    for name, h in [("wait", "block until a matching event, print it as JSON, exit"),
                    ("watch", "print matching events as they happen")]:
        s = sub.add_parser(name, help=h)
        s.add_argument("--for", dest="for_", choices=["push", "claim", "release", "handoff"])
        s.add_argument("--from", dest="from_")
        s.add_argument("--to")
        s.add_argument("--path")
        s.add_argument("--space")
        s.add_argument("--include-mine", action="store_true")
        s.add_argument("--interval", type=float, default=5.0)
        s.add_argument("--timeout", type=float, default=0)
        s.set_defaults(fn=(lambda a: cmd_wait(a)) if name == "wait" else (lambda a: cmd_wait(a, forever=True)))
    return p


VERBS = {"link", "spaces", "status", "push", "pull", "diff", "restore", "log", "claim", "release", "handoff", "wait", "watch"}


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # never show tracebacks; they can carry URLs or bodies
        if type(e).__name__ in ("NoToken", "NotSignedIn"):
            raise SystemExit("aos: not signed in to AgentOS. Run `aos login` (or set AGENTOS_PAT).")
        if isinstance(e, (OSError, RuntimeError)) and not isinstance(e, FileNotFoundError):
            raise SystemExit(f"aos: couldn't reach AgentOS ({type(e).__name__}). Nothing was retried.")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
