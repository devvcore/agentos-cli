"""`aos sync`: keep a linked folder and its space in step, without running `aos push` by hand.

It watches the folder and the space:
- a change here, once the folder has been quiet for a moment, is pushed;
- a newer version in the space is pulled first;
- a conflict (you both changed a file) stops sync, so nothing is overwritten:
  merge, `aos resolve <file>`, and start it again;
- a file someone else has claimed is left alone (said once) until they release it.

It runs in the foreground (Ctrl-C stops it), or with --background as a macOS
login item that runs `aos sync` itself. There is no separate script: an agent
writing its own unsigned `auto-sync.sh` LaunchAgent is what made macOS ask about
an "unidentified developer". macOS still shows a "Background Items Added" notice
for any login item that isn't from a signed app, so --background says so, and
what it is, before it installs.
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
        _say(f"conflict in {', '.join(st['conflicts'])}: merge, `aos resolve <file>`, then `aos sync` again.")
        return 1
    sp = spaces.open_space(st)
    if remote_due:
        head = sp.head()
        if head > st["version"]:
            rc = spaces.cmd_pull(_ns())
            if rc == 1:
                _say("stopped so nothing is overwritten. Merge, `aos resolve <file>`, then `aos sync` again.")
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
        if "Run `aos pull` first" in str(e) or "same moment" in str(e):
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


EXPLAIN = """Background sync runs `aos sync` for this folder whenever you're logged in to this Mac.
  macOS will show "Background Items Added" for it, maybe with "unidentified developer":
  that is aos (installed with uv, not a signed app), and it's expected. Turn it off any time
  with `aos sync --stop`, or in System Settings > General > Login Items & Extensions."""


def start_background(root: Path, yes: bool) -> int:
    if sys.platform != "darwin":
        print("aos: background sync is set up for macOS only. Elsewhere, run `aos sync` under your own\n"
              "  service manager (systemd, tmux, nohup) in this folder.", file=sys.stderr)
        return 1
    exe = shutil.which("aos") or sys.argv[0]
    if not exe or not os.path.exists(exe):
        print("aos: couldn't find the aos command to run in the background.", file=sys.stderr)
        return 1
    print(EXPLAIN)
    if not yes:
        if not sys.stdin.isatty():
            print("aos: pass --yes to turn on background sync without a question.", file=sys.stderr)
            return 2
        if input("Turn it on? [Y/n] ").strip().lower() not in ("", "y", "yes"):
            print("Nothing changed.")
            return 0
    plist = {
        "Label": _label(root),
        "ProgramArguments": [os.path.realpath(exe), "sync", "--dir", str(root)],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        # Restarted after a crash, not after a stop: a conflict exits 0 in the background (see main).
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": str(_log_path(root)),
        "StandardErrorPath": str(_log_path(root)),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "AOS_SYNC_BACKGROUND": "1"},
    }
    p = _plist_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], capture_output=True)
    p.write_bytes(plistlib.dumps(plist))
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(p)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"aos: launchd didn't start it ({r.stderr.strip() or r.returncode}).", file=sys.stderr)
        return 1
    print(f"Background sync is on for {root}. Log: {_log_path(root)}  ·  stop: aos sync --stop")
    return 0


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
    p = argparse.ArgumentParser(prog="aos sync", description="Keep this linked folder and its space in step.")
    p.add_argument("--dir", help="the linked folder (default: this one)")
    p.add_argument("--once", action="store_true", help="pull and push once, then exit")
    p.add_argument("--background", action="store_true", help="keep syncing while you're logged in (macOS)")
    p.add_argument("--stop", action="store_true", help="turn background sync off for this folder")
    p.add_argument("--yes", "-y", action="store_true")
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
        print("aos: this folder isn't linked to a space. Run `aos link <space>` here first.", file=sys.stderr)
        return 1
    if a.stop:
        return stop_background(root)
    if a.background:
        return start_background(root, a.yes)
    try:
        rc = run(root, once=a.once, remote_every=a.interval)
        if rc == 1 and os.environ.get("AOS_SYNC_BACKGROUND"):
            _say("background sync paused until you fix this and run `aos sync --background` again.")
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{_label(root)}"], capture_output=True)
            return 0
        return rc
    except KeyboardInterrupt:
        _say("sync stopped.")
        return 0
    finally:
        for r in spaces._REMOTE:
            r.close()
        spaces._REMOTE.clear()
