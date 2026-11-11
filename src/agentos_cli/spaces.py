#!/usr/bin/env python3
"""aos spaces: Git-shaped verbs for working in an AgentOS space.

A space is where a piece of work lives. Its state is kept in workspace Files
under /aos/<space>/, so any person or agent with AgentOS access can use it:

  objects/<sha256>        file contents, gzip+base64, content-addressed
  versions/vNNNNNN.json   {n, parent, author, message, at, nonce, tree: {path: sha}}
  events/eNNNNNN.json     claims, releases and handoffs, numbered, append-only

Versions and events are numbered without gaps, so the newest one is found by
checking which names exist (a handful of parallel `stat`s), never by listing a
folder: a Files listing is cut to ~10 entries once a reply passes the MCP
transport budget. For the same reason, anything longer
than CHUNK_CHARS (a big file, a version of a big tree) is stored in parts:

  <name>                        "aos-chunks:<set>:<n>:<sha256 of the whole text>"
  <name>.<set>.0000 … .<n-1>    the parts, written before the header

Versions, events and objects never change once written, so they are cached on
this computer (~/.agentos/cache/spaces/…) and fetched once. Uploads and
downloads run in parallel; each thread keeps one HTTPS connection open.

Locally, a linked folder holds .aos/state.json: the space, the version the
folder is based on, the tree of that version, and any unresolved conflicts.

No merges. Pull never overwrites a file you changed: the incoming copy lands
next to it as <file>.theirs, the conflict is recorded, and push refuses until
you merge and run `aos resolve <file>`. Claims are how agents avoid the
conflict in the first place; each agent session claims under its own name.
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
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = "/aos"
STATE_DIR = ".aos"
DEFAULT_IGNORE = [".git", ".aos", "node_modules", "__pycache__", ".DS_Store", "*.theirs", ".venv", "venv", "dist", "build", ".next"]
MAX_FILE_BYTES = 5 * 1024 * 1024
CLAIM_TTL_MIN = 60
# One `fs cat` reply must fit the MCP transport budget: 24,000 characters, which the
# reply spends twice (text content and structuredContent), so a file over ~11,500
# characters comes back cut (measured on tryagentos.net, Nov 2026). Parts stay well under.
CHUNK_CHARS = 10_000
CHUNK_HEADER = "aos-chunks:"
WORKERS = 8
PROBE_POINTS = 4


# ---------------------------------------------------------------- remote

def _missing(e: Exception) -> bool:
    return isinstance(e, FileNotFoundError) or (isinstance(e, RuntimeError) and "no such file" in str(e))


class Remote:
    """Space storage on workspace Files, through the AgentOS MCP endpoint."""

    def __init__(self, client=None):
        if client is None:
            from . import mcp
            client = mcp.connect(timeout=60)
        self.c = client
        # Two pools so a task in the first can wait on parts in the second without
        # starving it (objects -> chunks). Threads keep their own keep-alive connection.
        self.pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="aos")
        self.parts = ThreadPoolExecutor(WORKERS, thread_name_prefix="aos-part")

    def _fs(self, cmd, content=None):
        args = {"cmd": cmd}
        if content is not None:
            args["content"] = content
        res = self.c.call("fs", **args)
        if isinstance(res, dict) and res.get("ok") is False:
            msg = res.get("error") or res.get("output") or cmd
            raise FileNotFoundError(msg) if "no such file" in str(msg) else RuntimeError(msg)
        return res

    def map(self, fn, items, pool=None):
        items = list(items)
        if len(items) <= 1:
            return [fn(x) for x in items]
        return list((pool or self.pool).map(fn, items))

    def read(self, path):
        try:
            res = self._fs(f"cat {path}")
        except Exception as e:
            if _missing(e):
                raise FileNotFoundError(path) from None
            raise
        if not isinstance(res, dict) or "output" not in res:
            raise FileNotFoundError(path)
        if res.get("truncated") or res.get("_truncated"):
            raise RuntimeError(f"{path} came back cut off")
        return res["output"]

    def write(self, path, text):
        self._fs(f"write {path}", text)

    def exists(self, path):
        try:
            res = self._fs(f"stat {path}")
        except Exception as e:
            if _missing(e):
                return False
            raise
        return isinstance(res, dict) and bool(res.get("stat"))

    def ls(self, path):
        try:
            res = self._fs(f"ls {path}")
        except Exception as e:
            if _missing(e):
                return [], []
            raise
        ent = (res or {}).get("entries") or {}
        files = [f["name"] if isinstance(f, dict) else f for f in ent.get("files", [])]
        folders = [f["name"] if isinstance(f, dict) else f for f in ent.get("folders", [])]
        return folders, files

    # Text of any length: small texts are one file, long ones are parts plus a header.
    def write_blob(self, path, text) -> str:
        if len(text) <= CHUNK_CHARS:
            self.write(path, text)
            return text
        set_id = secrets.token_hex(4)
        parts = [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)]
        self.map(lambda ip: self.write(f"{path}.{set_id}.{ip[0]:04d}", ip[1]), enumerate(parts), self.parts)
        header = f"{CHUNK_HEADER}{set_id}:{len(parts)}:{hashlib.sha256(text.encode()).hexdigest()}"
        self.write(path, header)  # last: a reader never finds a header whose parts are missing
        return header

    def read_blob(self, path) -> str:
        head = self.read(path)
        if not head.startswith(CHUNK_HEADER):
            return head
        try:
            set_id, n, digest = head[len(CHUNK_HEADER):].strip().split(":")
            n = int(n)
        except ValueError:
            raise RuntimeError(f"{path} has a damaged header") from None
        text = "".join(self.map(lambda i: self.read(f"{path}.{set_id}.{i:04d}"), range(n), self.parts))
        if hashlib.sha256(text.encode()).hexdigest() != digest:
            raise RuntimeError(f"{path} failed its checksum")
        return text

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.parts.shutdown(wait=False, cancel_futures=True)


class Cache:
    """Immutable space records kept on this computer: versions, events and objects."""

    def __init__(self, space: str, scope: str):
        from . import paths
        self.dir = paths.state_dir() / "cache" / "spaces" / scope / space

    def _p(self, *parts):
        return self.dir.joinpath(*parts)

    def _write(self, p: Path, data: bytes):
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.{secrets.token_hex(2)}.tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get_json(self, kind, key):
        try:
            return json.loads(self._p(kind, f"{key}.json").read_text())
        except (OSError, ValueError):
            return None

    def put_json(self, kind, key, rec):
        try:
            self._write(self._p(kind, f"{key}.json"), json.dumps(rec, sort_keys=True).encode())
        except OSError:
            pass

    def get_object(self, sha):
        try:
            data = self._p("objects", sha).read_bytes()
        except OSError:
            return None
        return data if hashlib.sha256(data).hexdigest() == sha else None

    def put_object(self, sha, data: bytes):
        try:
            if not self._p("objects", sha).exists():
                self._write(self._p("objects", sha), data)
        except OSError:
            pass

    def known(self, kind) -> int:
        try:
            return int(json.loads(self._p("known.json").read_text()).get(kind, 0))
        except (OSError, ValueError):
            return 0

    def set_known(self, kind, n: int):
        try:
            p = self._p("known.json")
            cur = json.loads(p.read_text()) if p.exists() else {}
            if n > int(cur.get(kind, 0)):
                cur[kind] = n
                self._write(p, json.dumps(cur).encode())
        except (OSError, ValueError):
            pass


def _cache_scope(client) -> str:
    """Spaces with the same name in two workspaces must not share a cache."""
    ws = None
    try:
        from . import auth
        login = auth.load_login() or {}
        if not os.environ.get("AGENTOS_PAT"):
            ws = login.get("workspace_id")
    except Exception:
        pass
    ident = ws or hashlib.sha256(str(getattr(client, "_token", "")).encode()).hexdigest()
    return hashlib.sha256(f"{getattr(client, 'url', '')}|{ident}".encode()).hexdigest()[:16]


class Space:
    def __init__(self, remote: Remote, name: str, cache: Cache | None = None):
        self.r = remote
        self.name = name
        self.base = f"{ROOT}/{name}"
        self.cache = cache or Cache(name, _cache_scope(remote.c))

    # ---- names
    def version_path(self, n):
        return f"{self.base}/versions/v{n:06d}.json"

    def event_path(self, n):
        return f"{self.base}/events/e{n:06d}.json"

    def object_path(self, sha):
        return f"{self.base}/objects/{sha}"

    def _last(self, path_fn, known: int) -> int:
        """Largest n >= known with 1..n all present (names are dense). Galloping, then a
        k-ary search: each round checks PROBE_POINTS names in parallel."""
        lo, hi, span = known, None, 1
        while hi is None:
            pts = [lo + span * (i + 1) for i in range(PROBE_POINTS)]
            hits = self.r.map(lambda n: self.r.exists(path_fn(n)), pts)
            for p, h in zip(pts, hits):
                if h:
                    lo = p
                else:
                    hi = p
                    break
            span *= PROBE_POINTS + 1
        while hi - lo > 1:
            step = max(1, (hi - lo) // (PROBE_POINTS + 1))
            pts = [p for p in (lo + step * (i + 1) for i in range(PROBE_POINTS)) if p < hi]
            hits = self.r.map(lambda n: self.r.exists(path_fn(n)), pts)
            for p, h in zip(pts, hits):
                if h:
                    lo = p
                else:
                    hi = p
                    break
        return lo

    # ---- objects
    def put_objects(self, items: dict) -> int:
        """Upload {sha: path or bytes}; skips what the space already has. Returns how many were sent."""
        todo = [(sha, src) for sha, src in items.items() if self.cache.get_object(sha) is None]

        def one(item):
            sha, src = item
            data = src if isinstance(src, bytes) else Path(src).read_bytes()
            text = base64.b64encode(gzip.compress(data)).decode()
            sent = False
            # Content-addressed, so rewriting is harmless: a small object is just written
            # (a stat costs as much); a big one is checked first so it isn't sent twice.
            if len(text) <= CHUNK_CHARS or not self.r.exists(self.object_path(sha)):
                self.r.write_blob(self.object_path(sha), text)
                sent = True
            self.cache.put_object(sha, data)
            return sent

        return sum(self.r.map(one, todo))

    def get_objects(self, shas) -> dict:
        out, todo = {}, []
        for sha in dict.fromkeys(shas):
            data = self.cache.get_object(sha)
            if data is None:
                todo.append(sha)
            else:
                out[sha] = data

        def one(sha):
            data = gzip.decompress(base64.b64decode(self.r.read_blob(self.object_path(sha))))
            if hashlib.sha256(data).hexdigest() != sha:
                raise RuntimeError(f"object {sha[:12]} failed its checksum")
            self.cache.put_object(sha, data)
            return sha, data

        out.update(dict(self.r.map(one, todo)))
        return out

    def get_object(self, sha) -> bytes:
        return self.get_objects([sha])[sha]

    # ---- versions
    def head(self) -> int:
        n = self._last(self.version_path, self.cache.known("version"))
        self.cache.set_known("version", n)
        return n

    def version(self, n):
        if n == 0:
            return {"n": 0, "tree": {}, "author": None, "message": "(empty)", "at": None, "parent": None}
        rec = self.cache.get_json("versions", n)
        if rec is None:
            rec = json.loads(self.r.read_blob(self.version_path(n)))
            self.cache.put_json("versions", n, rec)
        return rec

    def versions(self, numbers) -> dict:
        numbers = list(numbers)
        missing = [n for n in numbers if n and self.cache.get_json("versions", n) is None]
        self.r.map(self.version, missing)
        return {n: self.version(n) for n in numbers}

    def commit(self, parent, author, message, tree):
        n = parent + 1
        path = self.version_path(n)
        if self.r.exists(path):
            return None  # someone else pushed first
        rec = {"n": n, "parent": parent, "author": author, "message": message,
               "at": now_iso(), "nonce": secrets.token_hex(4), "tree": tree}
        written = self.r.write_blob(path, json.dumps(rec, indent=1, sort_keys=True))
        # Read back: if two writers raced, only one header (or nonce) survives.
        if self.r.read(path) != written:
            return None
        self.cache.put_json("versions", n, rec)
        self.cache.set_known("version", n)
        return rec

    # ---- events
    def _legacy_events(self) -> list[dict]:
        """Events from before numbering (events/<timestamp>-<hex>.json), read once and cached."""
        cached = self.cache.get_json("events", "legacy")
        if cached is not None:
            return cached
        _, files = self.r.ls(f"{self.base}/events")
        names = sorted(f for f in files if f.endswith(".json") and not re.match(r"e\d{6}\.json$", f))

        def one(name):
            rec = json.loads(self.r.read(f"{self.base}/events/{name}"))
            rec["id"] = name[:-5]
            return rec

        recs = self.r.map(one, names)
        self.cache.put_json("events", "legacy", recs)
        return recs

    def event(self, n):
        rec = self.cache.get_json("events", n)
        if rec is None:
            rec = json.loads(self.r.read(self.event_path(n)))
            rec["id"] = f"e{n:06d}"
            self.cache.put_json("events", n, rec)
        return rec

    def last_event(self) -> int:
        n = self._last(self.event_path, self.cache.known("event"))
        self.cache.set_known("event", n)
        self._last_seen = n
        return n

    def events(self, since: int = 0, upto: int | None = None) -> list[dict]:
        """Events after number `since` (all, legacy first, when since is 0), oldest first."""
        upto = self.last_event() if upto is None else upto
        nums = list(range(since + 1, upto + 1))
        missing = [n for n in nums if self.cache.get_json("events", n) is None]
        self.r.map(self.event, missing)
        out = self._legacy_events() if since == 0 else []
        return out + [self.event(n) for n in nums]

    def emit(self, kind, author, **fields):
        # Just looked? Start there; the exists() check below catches anything newer.
        n = (getattr(self, "_last_seen", None) if getattr(self, "_last_seen", None) is not None else self.last_event()) + 1
        for _ in range(8):
            path = self.event_path(n)
            if self.r.exists(path):
                n += 1
                continue
            rec = {"kind": kind, "author": author, "at": now_iso(), "nonce": secrets.token_hex(4), "n": n, **fields}
            text = json.dumps(rec, sort_keys=True)
            self.r.write(path, text)
            if self.r.read(path) == text:
                rec["id"] = f"e{n:06d}"
                self.cache.put_json("events", n, rec)
                self.cache.set_known("event", n)
                self._last_seen = n
                return rec
            n += 1  # lost a race for this number; take the next
        raise RuntimeError("couldn't record the event: the space is very busy, try again")

    def emit_many(self, items: list[tuple[str, str, dict]]) -> list[dict]:
        """Record several events at once: numbers are taken together and written in parallel.
        Any number lost to a concurrent writer is retried one by one with emit()."""
        if len(items) <= 1:
            return [self.emit(k, a, **f) for k, a, f in items]
        start = (self._last_seen if getattr(self, "_last_seen", None) is not None else self.last_event()) + 1
        nums = list(range(start, start + len(items)))
        taken = self.r.map(lambda n: self.r.exists(self.event_path(n)), nums)
        if any(taken):
            return [self.emit(k, a, **f) for k, a, f in items]
        recs = [{"kind": k, "author": a, "at": now_iso(), "nonce": secrets.token_hex(4), "n": n, **f}
                for n, (k, a, f) in zip(nums, items)]
        texts = [json.dumps(r, sort_keys=True) for r in recs]
        self.r.map(lambda nt: self.r.write(self.event_path(nt[0]), nt[1]), zip(nums, texts))
        back = self.r.map(lambda n: self.r.read(self.event_path(n)), nums)
        out = []
        for n, rec, text, got in zip(nums, recs, texts, back):
            if got == text:
                rec["id"] = f"e{n:06d}"
                self.cache.put_json("events", n, rec)
                out.append(rec)
            else:
                self._last_seen = max(nums)
                out.append(self.emit(rec["kind"], rec["author"], **{k: v for k, v in rec.items()
                                                                     if k not in ("kind", "author", "at", "nonce", "n")}))
        self.cache.set_known("event", max(nums))
        self._last_seen = max(self._last_seen or 0, max(nums))
        return out

    def sync(self):
        """(head version, last event number), both found in parallel."""
        with ThreadPoolExecutor(2) as ex:
            h = ex.submit(self.head)
            e = ex.submit(self.last_event)
            return h.result(), e.result()


def active_claims(events) -> dict:
    claims = {}
    for ev in events:
        if ev.get("kind") == "claim":
            claims[ev["topic"]] = ev
        elif ev.get("kind") == "release":
            c = claims.get(ev.get("topic"))
            if c and (c["author"] == ev.get("author") or ev.get("note") == "handoff" or ev.get("force")):
                claims.pop(ev["topic"], None)
            elif c is None:
                claims.pop(ev.get("topic"), None)
    t = datetime.now(timezone.utc)
    return {k: v for k, v in claims.items() if parse_iso(v["expires"]) > t}


# ---------------------------------------------------------------- identity

_AGENT_PROCS = {"claude": "claude-code", "codex": "codex", "cursor-agent": "cursor", "opencode": "opencode",
                "gemini": "gemini", "windsurf": "windsurf", "aider": "aider", "goose": "goose", "amp": "amp"}


def _agent_session() -> str | None:
    """`<agent>:<session>` when aos runs inside a coding agent, so two agents on one computer
    are two people as far as claims and handoffs go."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        return f"claude-code:{sid[:8]}"
    for var, name in (("CODEX_THREAD_ID", "codex"), ("CODEX_SESSION_ID", "codex"), ("CURSOR_SESSION_ID", "cursor")):
        if os.environ.get(var):
            return f"{name}:{os.environ[var][:8]}"
    pid = os.getppid()
    for _ in range(12):  # walk up to the agent process that started this shell
        if pid <= 1:
            break
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            break
        if not out:
            break
        ppid, _, comm = out.partition(" ")
        base = os.path.basename(comm.strip()).lower()
        for proc, name in _AGENT_PROCS.items():
            if base == proc or base.startswith(proc + "-") or base.startswith(proc + "."):
                return f"{name}:{pid}"
        try:
            pid = int(ppid)
        except ValueError:
            break
    return None


_ME: dict[str, str] = {}


def whoami(args) -> str:
    """Who you are in the log and on claims: --as, $AOS_AS, or user@host plus the agent session."""
    explicit = getattr(args, "as_", None) or os.environ.get("AOS_AS")
    if explicit:
        return explicit
    if "me" not in _ME:
        base = f"{getpass.getuser()}@{socket.gethostname().split('.')[0]}"
        agent = _agent_session()
        _ME["me"] = f"{base}/{agent}" if agent else base
    return _ME["me"]


def same_person(a: str | None, b: str | None) -> bool:
    return bool(a) and a == b


# ---------------------------------------------------------------- local

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


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
    st = json.loads((root / STATE_DIR / "state.json").read_text())
    st.setdefault("conflicts", [])
    return st


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


_REMOTE: list[Remote] = []


def open_space(st, remote=None):
    if remote is None:
        if not _REMOTE:
            _REMOTE.append(Remote())
        remote = _REMOTE[0]
    return Space(remote, st["space"])


def covers(topic, rel):
    """A claim's topic covers a path when it is that path or a folder above it."""
    t = topic.strip("/")
    return rel == t or rel.startswith(t + "/")


def claimed_by_others(claims, paths, me):
    """{path: claim} for paths someone else holds a live claim on."""
    out = {}
    for rel in paths:
        for topic, c in claims.items():
            if not same_person(c["author"], me) and covers(topic, rel):
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


def version_or_exit(sp, n, head=None):
    head = sp.head() if head is None else head
    if n < 0 or n > head:
        raise SystemExit(f"aos: there is no v{n} in '{sp.name}' (latest is v{head}).")
    return sp.version(n)


def _progress(msg):
    if sys.stderr.isatty():
        print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- verbs

def cmd_link(a):
    root = Path(a.dir or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = slug(a.space or root.name)
    sp = open_space({"space": name})
    head = sp.head()
    save_state(root, {"space": name, "version": 0, "tree": {}, "linked_at": now_iso(), "conflicts": []})
    print(f"Linked {root} to space '{name}' (latest version: {head or 'none yet'}).")
    if head:
        print("Run `aos pull` to bring it down.")
    return 0


def cmd_spaces(a):
    folders, _ = open_space({"space": "_"}).r.ls(ROOT)
    for f in sorted(folders):
        print(f)
    if not folders:
        print("(no spaces yet)")
    return 0


def _print_conflicts(st, root):
    for rel in st.get("conflicts") or []:
        side = " (theirs is next to it as .theirs)" if (root / (rel + ".theirs")).exists() else ""
        print(f"  ! conflict: {rel}{side}")


def cmd_status(a):
    root = need_root()
    st = load_state(root)
    sp = open_space(st)
    head, last = sp.sync()
    local = scan(root)
    print(f"space {st['space']}  ·  you are on v{st['version']}  ·  latest v{head}  ·  you are {whoami(a)}")
    if head > st["version"]:
        hv = sp.version(head)
        print(f"  behind by {head - st['version']}: latest by {hv['author']} — {hv['message']}")
    print("local changes since v%d:" % st["version"])
    added, changed, removed = tree_diff(st["tree"], local)
    print_diff(added, changed, removed)
    if st.get("conflicts"):
        print("unresolved conflicts (merge, then `aos resolve <file>`; push waits until then):")
        _print_conflicts(st, root)
    claims = active_claims(sp.events(0, last))
    me = whoami(a)
    for rel, c in sorted(claimed_by_others(claims, added + changed + removed, me).items()):
        print(f"  ! {rel}: you changed a file which {c['author']} has claimed (until {c['expires']}). "
              "`aos push` will refuse it; talk to them or pass --force.")
    if claims:
        print("claims:")
        for t, c in sorted(claims.items()):
            who = "you" if same_person(c["author"], me) else c["author"]
            print(f"  {t}: {who} until {c['expires']}" + (f" — {c['note']}" if c.get("note") else ""))
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
    if st.get("conflicts") and not a.zip:
        print("aos: you have unresolved conflicts; nothing pushed:", file=sys.stderr)
        for rel in st["conflicts"]:
            print(f"  {rel}", file=sys.stderr)
        print("Merge their changes (in <file>.theirs) into your file, then `aos resolve <file>` and push again.",
              file=sys.stderr)
        return 1
    src = root
    if a.zip:
        src = _stage_zip(a.zip)
    sp = open_space(st)
    head, last = sp.sync()
    if head != st["version"]:
        raise SystemExit(f"aos: the space moved to v{head} (you're on v{st['version']}). Run `aos pull` first.")
    local = scan(src)
    added, changed, removed = tree_diff(st["tree"], local)
    if not (added or changed or removed):
        print("Nothing to push.")
        return 0
    me = whoami(a)
    held = claimed_by_others(active_claims(sp.events(0, last)), added + changed + removed, me)
    if held and not a.force:
        for rel, c in sorted(held.items()):
            print(f"aos: {rel} is claimed by {c['author']} until {c['expires']}"
                  + (f" — {c['note']}" if c.get("note") else ""), file=sys.stderr)
        print("aos: nothing pushed. Ask them to release it, or pass --force.", file=sys.stderr)
        return 3
    todo = {local[rel]: src / rel for rel in added + changed}
    if len(todo) > 20:
        _progress(f"Uploading {len(todo)} files…")
    uploaded = sp.put_objects(todo)
    rec = sp.commit(head, me, a.message or "(no message)", local)
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
    head = sp.head()
    target = a.version or head
    if a.version is not None and not (0 < a.version <= head):
        raise SystemExit(f"aos: there is no v{a.version} in '{sp.name}' (latest is v{head}).")
    if target == st["version"]:
        print(f"Already on v{target}." + (" Conflicts still open: see `aos status`." if st.get("conflicts") else ""))
        return 0
    theirs = sp.version(target)["tree"]
    base = st["tree"]
    local = scan(root)
    added, changed, removed = tree_diff(base, theirs)
    want = [rel for rel in added + changed if local.get(rel) != theirs[rel]]
    if len(want) > 20:
        _progress(f"Downloading {len(want)} files…")
    blobs = sp.get_objects(theirs[rel] for rel in want)
    conflicts, fetched = [], 0
    for rel in want:
        p = root / rel
        mine = local.get(rel)
        if mine is not None and mine != base.get(rel):  # you changed it too
            (root / (rel + ".theirs")).write_bytes(blobs[theirs[rel]])
            conflicts.append(rel)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blobs[theirs[rel]])
        fetched += 1
    deleted_here = []
    for rel in removed:
        p = root / rel
        if p.exists() and local.get(rel) == base.get(rel):
            p.unlink()
        elif p.exists():
            conflicts.append(rel)
            deleted_here.append(rel)
    # The folder is now based on `target` for real: the tree is theirs, so `diff` and `status`
    # compare your copy with what is actually latest, and a conflicted file shows as your change.
    # Push refuses until each conflict is resolved, so their edit can't be overwritten unseen.
    st.update(version=target, tree=dict(theirs), conflicts=sorted(set(st.get("conflicts") or []) | set(conflicts)))
    save_state(root, st)
    print(f"Pulled v{target}: fetched {fetched} file(s) that changed ({len(added)} added, {len(changed)} changed, {len(removed)} removed upstream).")
    for c in conflicts:
        if c in deleted_here:
            print(f"  conflict: {c} — deleted upstream, changed here; your copy kept")
        else:
            print(f"  conflict: {c} — you both changed it; yours kept, theirs is next to it as {c}.theirs")
    if conflicts:
        print("Merge, then `aos resolve <file>` (or `aos resolve --all`). Push waits until then.")
    return 1 if conflicts else 0


def cmd_resolve(a):
    root = need_root()
    st = load_state(root)
    open_ = list(st.get("conflicts") or [])
    if not open_:
        print("No conflicts to resolve.")
        return 0
    targets = open_ if a.all else [p.strip("/") for p in a.paths]
    if not targets:
        raise SystemExit("aos: name the files you merged, or pass --all.")
    unknown = [t for t in targets if t not in open_]
    if unknown:
        raise SystemExit(f"aos: not in conflict: {', '.join(unknown)}. Open conflicts: {', '.join(open_)}")
    for t in targets:
        side = root / (t + ".theirs")
        if side.exists():
            side.unlink()
    st["conflicts"] = [c for c in open_ if c not in targets]
    save_state(root, st)
    left = f" {len(st['conflicts'])} still open." if st["conflicts"] else " Push when ready."
    print(f"Resolved {len(targets)} file(s).{left}")
    return 0


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
        blobs = sp.get_objects(old[rel] for rel in changed)
        for rel in changed:
            print_line_diff(rel, blobs[old[rel]], (root / rel).read_bytes(), f"v{base_n}", "local")
        return 0
    head = sp.head()
    old, new = version_or_exit(sp, a.v1, head)["tree"], version_or_exit(sp, a.v2, head)["tree"]
    print(f"v{a.v1} → v{a.v2}:")
    added, changed, removed = tree_diff(old, new)
    print_diff(added, changed, removed)
    blobs = sp.get_objects([old[r] for r in changed] + [new[r] for r in changed])
    for rel in changed:
        print_line_diff(rel, blobs[old[rel]], blobs[new[rel]], f"v{a.v1}", f"v{a.v2}")
    return 0


def cmd_restore(a):
    """Put files back as they were in a version (default: the one you're on). Local only; push to share."""
    root = need_root()
    st = load_state(root)
    sp = open_space(st)
    n = st["version"] if a.version is None else a.version
    tree = st["tree"] if a.version is None else version_or_exit(sp, n)["tree"]
    plan, removed = [], 0
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
        plan += hits
    blobs = sp.get_objects(tree[k] for k in plan)
    for k in plan:
        q = root / k
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(blobs[tree[k]])
    print(f"Restored {len(plan)} file(s) from v{n}" + (f", removed {removed} not in v{n}" if removed else "")
          + ". Run `aos push` to share it.")
    return 0


def cmd_log(a):
    st = load_state(need_root()) if not a.space else {"space": slug(a.space)}
    sp = open_space(st)
    head, last = sp.sync()
    items = []
    for n, v in sp.versions(range(1, head + 1)).items():
        items.append((v["at"], f"v{n:<4} push     {v['author']}: {v['message']}"))
    for e in sp.events(0, last):
        extra = e.get("note") or e.get("message") or ""
        topic = f" [{e['topic']}]" if e.get("topic") else ""
        to = f" → {e['to']}" if e.get("to") else ""
        items.append((e["at"], f"      {e['kind']:<8} {e['author']}{topic}{to}" + (f": {extra}" if extra else "")))
    for at, line in sorted(items)[-a.n:]:
        print(f"{at}  {line}")
    if not items:
        print("(nothing yet)")
    return 0


def cmd_claim(a):
    st = load_state(need_root())
    sp = open_space(st)
    me = whoami(a)
    held = active_claims(sp.events()).get(a.topic)
    if held and not same_person(held["author"], me):
        print(f"'{a.topic}' is claimed by {held['author']} until {held['expires']}" + (f" — {held['note']}" if held.get("note") else ""))
        return 3
    exp = (datetime.now(timezone.utc) + timedelta(minutes=a.ttl)).isoformat(timespec="seconds").replace("+00:00", "Z")
    sp.emit("claim", me, topic=a.topic, expires=exp, note=a.message or "")
    print(f"Claimed '{a.topic}' until {exp} as {me}.")
    return 0


def cmd_release(a):
    st = load_state(need_root())
    sp = open_space(st)
    me = whoami(a)
    held = active_claims(sp.events()).get(a.topic)
    if held and not same_person(held["author"], me) and not a.force:
        print(f"aos: '{a.topic}' is held by {held['author']}, not you ({me}). Pass --force to release it anyway.",
              file=sys.stderr)
        return 3
    sp.emit("release", me, topic=a.topic, note=a.message or "", **({"force": True} if a.force else {}))
    print(f"Released '{a.topic}'.")
    return 0


def cmd_handoff(a):
    st = load_state(need_root())
    sp = open_space(st)
    me = whoami(a)
    mine = [t for t, c in active_claims(sp.events()).items() if same_person(c["author"], me)]
    sp.emit_many([("release", me, {"topic": t, "note": "handoff"}) for t in mine]
                 + [("handoff", me, {"message": a.message, "to": a.to or "", "version": st["version"]})])
    print(f"Handed off at v{st['version']}" + (f" to {a.to}" if a.to else "") + ".")
    return 0


def _matches(ev, a, me):
    if a.for_ and ev["kind"] != a.for_:
        return False
    if a.from_ and a.from_ not in ev.get("author", ""):
        return False
    if not a.include_mine and same_person(ev.get("author"), me):
        return False
    if a.to and ev.get("to") and a.to not in ev.get("to"):
        return False
    if a.path:
        paths = ev.get("paths") or []
        if not any(p.startswith(a.path) for p in paths):
            return False
    return True


def _push_event(sp, n):
    v = sp.version(n)
    prev = sp.version(v["parent"])["tree"] if v.get("parent") else {}
    ad, ch, rm = tree_diff(prev, v["tree"])
    return {"kind": "push", "author": v["author"], "at": v["at"], "version": n,
            "message": v["message"], "paths": ad + ch + rm}


def cmd_wait(a, forever=False):
    st = load_state(need_root()) if not a.space else {"space": slug(a.space)}
    sp = open_space(st)
    me = whoami(a)
    seen_v, seen_e = sp.sync()
    deadline = time.time() + a.timeout if a.timeout else None
    while True:
        if deadline and time.time() >= deadline:
            print("aos: timed out waiting", file=sys.stderr)
            return 4
        time.sleep(a.interval if not deadline else max(0.0, min(a.interval, deadline - time.time())))
        head, last = sp.sync()
        new = [_push_event(sp, n) for n in range(seen_v + 1, head + 1)] + sp.events(seen_e, last)
        seen_v, seen_e = max(seen_v, head), max(seen_e, last)
        for ev in new:
            if _matches(ev, a, me):
                print(json.dumps(ev, sort_keys=True), flush=True)
                if not forever:
                    return 0


# ---------------------------------------------------------------- cli

def build_parser():
    p = argparse.ArgumentParser(prog="aos", description="Work in AgentOS spaces with Git-shaped verbs.")
    p.add_argument("--as", dest="as_", help="who you are in the log (default: $AOS_AS, or user@host plus the agent session)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("link", help="tie this folder to a space (created on first push)")
    s.add_argument("space", nargs="?")
    s.add_argument("--dir")
    s.set_defaults(fn=cmd_link)

    sub.add_parser("spaces", help="list spaces").set_defaults(fn=cmd_spaces)
    sub.add_parser("status", help="local changes, how far behind, conflicts, open claims").set_defaults(fn=cmd_status)

    s = sub.add_parser("push", help="upload a new version")
    s.add_argument("-m", "--message")
    s.add_argument("--zip", help="push the contents of a zip instead of this folder")
    s.add_argument("--force", action="store_true", help="push even files someone else has claimed")
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("pull", help="download only what changed")
    s.add_argument("--version", type=int)
    s.set_defaults(fn=cmd_pull)

    s = sub.add_parser("resolve", help="mark conflicted files as merged (deletes their .theirs copy)")
    s.add_argument("paths", nargs="*")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_resolve)

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
                        ("release", cmd_release, "drop your claim")]:
        s = sub.add_parser(name, help=h)
        s.add_argument("topic")
        s.add_argument("-m", "--message")
        if name == "claim":
            s.add_argument("--ttl", type=int, default=CLAIM_TTL_MIN, help="minutes (default 60)")
        else:
            s.add_argument("--force", action="store_true", help="release someone else's claim")
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
        s.add_argument("--interval", type=float, default=3.0)
        s.add_argument("--timeout", type=float, default=0)
        s.set_defaults(fn=(lambda a: cmd_wait(a)) if name == "wait" else (lambda a: cmd_wait(a, forever=True)))
    return p


VERBS = {"link", "spaces", "status", "push", "pull", "resolve", "diff", "restore", "log", "claim", "release",
         "handoff", "wait", "watch"}


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except KeyboardInterrupt:
        return 130
    except SystemExit:
        raise
    except Exception as e:  # never show tracebacks; they can carry URLs or bodies
        if type(e).__name__ in ("NoToken", "NotSignedIn"):
            raise SystemExit("aos: not signed in to AgentOS. Run `aos login` (or set AGENTOS_PAT).")
        if isinstance(e, (OSError, RuntimeError, ValueError)):
            from .mcp import friendly_error
            msg = friendly_error(e)
            raise SystemExit("aos: " + (msg if "Bearer" not in msg else type(e).__name__))
        raise
    finally:
        for r in _REMOTE:
            r.close()
        _REMOTE.clear()


if __name__ == "__main__":
    raise SystemExit(main())
