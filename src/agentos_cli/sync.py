"""`aos space sync`: keep a linked folder and its space in step, without running `aos space push` by hand.

It watches the folder and the space:
- a change here, once the folder has been quiet for a moment, is pushed;
- a newer version in the space is pulled first;
- a conflict (you both changed a file) stops sync, so nothing is overwritten:
  merge, `aos space resolve <file>`, and start it again;
- a file someone else has claimed is left alone (said once) until they release it.

It runs in the foreground only (Ctrl-C stops it); leave it open in a terminal tab.
There is no background mode: a login item is what makes macOS show its
"unidentified developer" / "Background Items Added" notice, and needing Apple
signing for it wasn't worth it (decided Nov 12). v0.2.8–0.2.9 had --background;
`aos space sync --stop` still removes a login item those versions installed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import spaces

LOCAL_EVERY_S = 2.0
REMOTE_EVERY_S = 15.0
QUIET_S = 3.0
LABEL_PREFIX = "net.tryagentos.aos.sync."


def _say(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


def _snapshot(root: Path) -> tuple:
    """Cheap change detector: (path, size, mtime) of tracked files. Hashing happens only on push."""
    pats = spaces.ignores(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
        dirnames[:] = [d for d in dirnames if not spaces.ignored(rel_dir + d, pats)]
        for fn in filenames:
            rel = rel_dir + fn
            if spaces.ignored(rel, pats):
                continue
            try:
                st = os.stat(os.path.join(dirpath, fn), follow_symlinks=False)
            except OSError:
                continue
            out.append((rel, st.st_size, st.st_mtime_ns))
    return tuple(sorted(out))


def _ns(**kw) -> argparse.Namespace:
    base = {"as_": None, "message": None, "zip": None, "force": False, "version": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _cycle(root: Path, remote_due: bool, quiet: bool, state: dict) -> int | None:
    """One round. Returns an exit code to stop with, or None to keep going."""
    st = spaces.load_state(root)
    if st.get("conflicts"):
        _say(f"conflict in {', '.join(st['conflicts'])}: merge, `aos space resolve <file>`, then `aos space sync` again.")
        return 1
    sp = spaces.open_space(st)
    if remote_due:
        head = sp.head()
        if head > st["version"]:
            rc = spaces.cmd_pull(_ns())
            if rc == 1:
                _say("stopped so nothing is overwritten. Merge, `aos space resolve <file>`, then `aos space sync` again.")
                return 1
            st = spaces.load_state(root)
    if not quiet:
        return None
    added, changed, removed = spaces.tree_diff(st["tree"], spaces.scan(root))
    if not (added or changed or removed):
        return None
    try:
        rc = spaces.cmd_push(_ns(message=f"aos sync: {len(added)} added, {len(changed)} changed, {len(removed)} removed"))
    except SystemExit as e:  # the space moved between our check and the push
        if "Run `aos space pull` first" in str(e) or "same moment" in str(e):
            state["force_remote"] = True
            return None
        raise
    if rc == 3:
        key = tuple(sorted(added + changed + removed))
        if state.get("claimed") != key:
            _say("someone else has claimed some of these files; waiting until they release them.")
            state["claimed"] = key
        return None
    state.pop("claimed", None)
    return 1 if rc == 1 else None


def run(root: Path, once: bool = False, local_every: float = LOCAL_EVERY_S,
        remote_every: float = REMOTE_EVERY_S, quiet_s: float = QUIET_S) -> int:
    os.chdir(root)
    st = spaces.load_state(root)
    if not once:
        _say(f"syncing {root} with space '{st['space']}' (changes here are pushed; new versions are pulled). "
             "Ctrl-C stops.")
    last_snap, changed_at, last_remote, state = _snapshot(root), 0.0, 0.0, {}
    if once:
        return _cycle(root, True, True, state) or 0
    while True:
        now = time.monotonic()
        snap = _snapshot(root)
        if snap != last_snap:
            last_snap, changed_at = snap, now
        remote_due = state.pop("force_remote", False) or now - last_remote >= remote_every
        if remote_due:
            last_remote = now
        quiet = changed_at and now - changed_at >= quiet_s
        try:
            rc = _cycle(root, remote_due, bool(quiet), state)
        except (OSError, RuntimeError) as e:  # network blips: say it, keep going
            from .mcp import friendly_error
            _say(f"couldn't reach AgentOS ({friendly_error(e).splitlines()[0]}); trying again.")
            rc = None
        if rc is not None:
            return rc
        if quiet:
            changed_at = 0.0
            last_snap = _snapshot(root)
        time.sleep(local_every)


# ---------------------------------------------------------------- background (macOS)

def _label(root: Path) -> str:
    return LABEL_PREFIX + hashlib.sha256(str(root).encode()).hexdigest()[:10]


def _plist_path(root: Path) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_label(root)}.plist"


def _log_path(root: Path) -> Path:
    return root / spaces.STATE_DIR / "sync.log"


def background_status(root: Path) -> str | None:
    """'running', 'stopped' (installed, not running) or None (not installed)."""
    if sys.platform != "darwin" or not _plist_path(root).exists():
        return None
    p = subprocess.run(["launchctl", "list", _label(root)], capture_output=True, text=True)
    return "running" if p.returncode == 0 and '"PID"' in p.stdout else "stopped"


def all_background() -> list[dict]:
    d = Path.home() / "Library" / "LaunchAgents"
    out = []
    for p in sorted(d.glob(LABEL_PREFIX + "*.plist")) if d.exists() else []:
        try:
            args = plistlib.loads(p.read_bytes()).get("ProgramArguments") or []
            root = Path(args[args.index("--dir") + 1]) if "--dir" in args else None
        except Exception:  # noqa: BLE001
            root = None
        if root:
            out.append({"folder": str(root), "state": background_status(root) or "stopped"})
    return out


def stop_background(root: Path) -> int:
    p = _plist_path(root)
    if not p.exists():
        print("Background sync isn't on for this folder.")
        return 0
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], capture_output=True)
    p.unlink()
    print(f"Background sync is off for {root}.")
    return 0


# ---------------------------------------------------------------- cli

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="aos space sync", description="Keep this linked folder and its space in step "
                                "while it runs (foreground; Ctrl-C stops it).")
    p.add_argument("--dir", help="the linked folder (default: this one)")
    p.add_argument("--once", action="store_true", help="pull and push once, then exit")
    p.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--stop", action="store_true", help="remove a background sync left by aos 0.2.8–0.2.9")
    p.add_argument("--interval", type=float, default=REMOTE_EVERY_S, help="seconds between checks for new versions")
    a = p.parse_args(argv)
    try:  # a log file (background) or a pipe must see each line as it happens
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    start = Path(a.dir).expanduser() if a.dir else Path.cwd()
    root = spaces.find_root(start)
    if not root:
        print("aos: this folder isn't linked to a space. Run `aos space link <name>` here first.", file=sys.stderr)
        return 1
    if a.stop:
        return stop_background(root)
    if a.background:
        print("aos: background sync was removed. Run `aos space sync` in a terminal tab and leave it open.",
              file=sys.stderr)
        return 2
    try:
        return run(root, once=a.once, remote_every=a.interval)
    except KeyboardInterrupt:
        _say("sync stopped.")
        return 0
    finally:
        for r in spaces._REMOTE:
            r.close()
        spaces._REMOTE.clear()
