"""`aos setup`: one screen, one keypress.

It finds your coding agents, shows what it will do in a few lines, and does it
when you press Enter: sign in if needed, add the AgentOS MCP server to each
agent, install the AgentOS skills, and (only if you say yes) add the skills you wrote to your
workspace. "Customize" opens the pickers; "Show file changes" shows the exact
diffs. Every file is backed up first and `aos setup --undo` takes it all out.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from . import agents as A, auth, paths, skills as S, ui


# ---------------------------------------------------------------- files

def _backup(path: Path, stamp: str) -> str | None:
    if not path.exists():
        return None
    try:
        rel = path.relative_to(paths.home()).as_posix()
    except ValueError:
        rel = path.as_posix().lstrip("/")
    dest = paths.backups_dir() / stamp / rel.replace("/", "__")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return str(dest)


def _apply_change(ch: A.Change, stamp: str, replan) -> dict:
    # Re-read right before writing: Claude Code rewrites ~/.claude.json often,
    # so if the file moved since the review, plan again against what's there now.
    now = ch.path.read_text() if ch.path.exists() else None
    if now != ch.before:
        fresh = replan()
        if fresh is None:
            return {"agent": ch.agent, "path": str(ch.path), "status": "already done"}
        ch = fresh
    backup = _backup(ch.path, stamp)
    paths.write_text(ch.path, ch.after or "")
    return {"agent": ch.agent, "path": str(ch.path), "backup": backup, "status": "written"}


def _record(entry: dict) -> None:
    m = paths.read_json(paths.manifest_path(), {"runs": []})
    m.setdefault("runs", []).append(entry)
    paths.write_json(paths.manifest_path(), m)


# ---------------------------------------------------------------- plan

@dataclass
class Choices:
    agents: list
    mcp: bool = True
    bundled: bool = True
    upload: bool = False
    upload_only: list | None = None   # slugs the person picked; None = all of them
    ws_slugs: list = field(default_factory=list)
    compression: bool = False


@dataclass
class Plan:
    changes: list = field(default_factory=list)          # A.Change
    links: list = field(default_factory=list)            # Claude desktop connector page
    notes: list = field(default_factory=list)
    skills: list = field(default_factory=list)           # (Skill, base, users, status, dest)
    uploads: list = field(default_factory=list)          # local skill dicts
    upload_pending: int = 0                              # found, but we can't check the workspace until sign-in
    compression: bool = False


def _signed_in() -> tuple[bool, str]:
    try:
        return True, auth.token_source()[1]
    except auth.NotSignedIn:
        return False, ""
    except Exception:
        return False, ""


def _client():
    from .work import connect
    return connect()


def _local(c: "Choices") -> list[dict]:
    return S.local_skills([d for d, _ in S.target_dirs([a.id for a in c.agents])] + [paths.home() / ".codex" / "skills"])


def ask_upload(c: "Choices", ws_cache: dict) -> bool | None:
    """Ask once whether to add the person's own skills to the shared workspace.
    Default is no: it puts their files where their team and its bots can read them."""
    import questionary
    cands = _local(c)
    if "list" in ws_cache:
        cands = S.not_in_workspace(cands, ws_cache["list"])
    if not cands:
        return False
    n = len(cands)
    ui.console.print(f"You have [bold]{n}[/] skill{'s' * (n != 1)} of your own here "
                     f"[dim]({_names((s['name'] for s in cands), 3)})[/].")
    pick = questionary.select(
        "Add them to your AgentOS workspace? Your team and its bots will be able to see and use them.",
        choices=["Not now", f"Yes, add all {n}", "Choose which…"], default="Not now",
        style=ui.qstyle(), qmark="", instruction=" ").ask()
    if pick is None:
        return None
    if pick == "Not now":
        c.upload, c.upload_only = False, None
    elif pick.startswith("Yes"):
        c.upload, c.upload_only = True, None
    else:
        got = _checkbox("Which skills?", [questionary.Choice(s["name"], value=s["slug"], checked=False) for s in cands])
        if got is None:
            return None
        c.upload, c.upload_only = bool(got), list(got)
    ui.console.print()
    return True


def build_plan(c: Choices, force: bool, client=None, ws_cache: dict | None = None) -> Plan:
    p = Plan(compression=c.compression)
    if c.mcp:
        for a in c.agents:
            try:
                ch = A.plan_mcp_add(a, force=force)
            except (FileExistsError, ValueError) as e:
                p.notes.append(str(e))
                continue
            if ch:
                p.changes.append(ch)
    to_install: list[S.Skill] = list(S.bundled()) if c.bundled else []
    ws_cache = ws_cache if ws_cache is not None else {}
    if c.ws_slugs and client:
        meta = {w["slug"]: w for w in ws_cache.get("list", [])}
        status = ui.console.status(f"Fetching {len(c.ws_slugs)} workspace skill(s)…", spinner="dots")
        status.start()
        for slug in c.ws_slugs:
            try:
                to_install.append(S.workspace_skill(client, slug, meta.get(slug)))
            except Exception as e:
                p.notes.append(f"skipped workspace skill {slug} ({type(e).__name__})")
        status.stop()
    for base, users in S.target_dirs([a.id for a in c.agents]):
        for sk in to_install:
            status, dest = S.plan_install(sk, base, force)
            p.skills.append((sk, base, users, status, dest))
    if c.upload:
        local = _local(c)
        if c.upload_only is not None:
            local = [s for s in local if s["slug"] in c.upload_only]
        if client is not None and "list" in ws_cache:
            p.uploads = S.not_in_workspace(local, ws_cache["list"])
        else:
            p.upload_pending = len(local)
    return p


def _names(items, n=4) -> str:
    items = list(items)
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _agent_skill_counts(p: Plan) -> dict[str, int]:
    """Agent name -> AgentOS skills it gets in this run (shared folders count for each agent)."""
    out: dict[str, int] = {}
    for sk, base, users, status, dest in p.skills:
        if status in ("new", "update"):
            for u in users:
                out[u] = out.get(u, 0) + 1
    return out


def _skill_names(p: Plan) -> list[str]:
    return sorted({(x[4].name if x[4] else x[0].name) for x in p.skills if x[3] in ("new", "update")})


def render(p: Plan, c: Choices, signed: bool, skipped_claude: bool) -> bool:
    """One line per agent saying exactly what it gets. Returns True when there's anything to do."""
    from rich.table import Table
    t = Table.grid(padding=(0, 3))
    t.add_column(no_wrap=True)
    t.add_column()
    t.add_column(style="dim", overflow="fold")
    changes = {ch.agent: ch for ch in p.changes}
    counts = _agent_skill_counts(p)
    anything = False
    for a in c.agents:
        what = []
        if a.id in changes:
            what.append("add the AgentOS MCP server" + (" (through Node)" if a.id == "claude-desktop" else ""))
        elif c.mcp and A.mcp_status(a) == "ours":
            what.append("[dim]AgentOS MCP server already added[/]")
        n = counts.get(a.name, 0)
        if n:
            what.append(f"{n} AgentOS skill{'s' * (n != 1)}")
        real = a.id in changes or n
        anything = anything or bool(real)
        where = ui.short(changes[a.id].path) if a.id in changes else ""
        t.add_row(("[green]+[/] " if real else "[dim]·[/] ") + a.name, " · ".join(what) or "[dim]nothing to do[/]", where)
    if p.uploads or p.upload_pending:
        n = len(p.uploads) or p.upload_pending
        t.add_row("[green]+[/] Your workspace", f"add {n} of your skills", _names((s["name"] for s in p.uploads), 3))
        anything = True
    if p.compression:
        t.add_row("[green]+[/] Compression", "turn on", "fewer tokens from tool output (agentos-capture)")
        anything = True
    if not signed and (c.upload or c.ws_slugs):
        t.add_row("[green]+[/] Sign in", "in your browser, once", "")
    ui.console.print("[bold]Setup will[/]")
    ui.console.print(t)
    names = _skill_names(p)
    if names:
        ui.dim(f"  Skills: {', '.join(names)}")
    if p.changes:
        ui.dim("  Each file is backed up first; `aos setup --undo` puts them back.")
    if skipped_claude:
        ui.dim("  Claude Code skipped: OpenBot shares its settings on this computer (Customize to include it).")
    for n in p.notes:
        ui.warn(n)
    return anything


# ---------------------------------------------------------------- customize

def _checkbox(question, choices):
    import questionary
    return questionary.checkbox(question, choices=choices, style=ui.qstyle(),
                                instruction="(space toggles, enter continues)").ask()


def customize(c: Choices, found, signed: bool, ws_cache: dict) -> Choices | None:
    import questionary
    picked = _checkbox("Agents", [questionary.Choice(a.name, value=a.id, checked=a in c.agents) for a, _ in found])
    if picked is None:
        return None
    c.agents = [A.BY_ID[i] for i in picked]
    cap = bool(shutil.which("agentos-capture"))
    parts = [
        questionary.Choice("AgentOS MCP server", value="mcp", checked=c.mcp),
        questionary.Choice("AgentOS skills", value="bundled", checked=c.bundled),
        questionary.Choice("Add my own skills to the workspace", value="upload", checked=c.upload),
        questionary.Choice("Pick skills from the workspace library", value="ws", checked=bool(c.ws_slugs)),
        questionary.Choice("Compression (fewer tokens from tool output)", value="compression",
                           checked=c.compression and cap, disabled=None if cap else "coming soon"),
    ]
    picked = _checkbox("Add", parts)
    if picked is None:
        return None
    c.mcp, c.bundled, c.upload, c.compression = ("mcp" in picked, "bundled" in picked, "upload" in picked,
                                                 "compression" in picked)
    if "ws" in picked:
        listing = ws_cache.get("list")
        if listing is None:
            ui.dim("Sign in first to pick from your workspace library (it happens when you choose Set up).")
        elif listing:
            got = _checkbox("Workspace skills", [
                questionary.Choice(f"{w['name'][:44]:<44}  {w['description'][:50]}", value=w["slug"],
                                   checked=w["slug"] in c.ws_slugs) for w in listing])
            c.ws_slugs = got or []
    else:
        c.ws_slugs = []
    return c


# ---------------------------------------------------------------- run

def run(args) -> int:
    inter = ui.interactive() and not args.yes
    if not inter and not args.yes and not args.dry_run:
        ui.fail("`aos setup` needs a terminal. Re-run with --yes to use the defaults (see --help for flags).")
        return 2
    if args.undo:
        return undo(args, inter)

    signed, src = _signed_in()
    found = A.detected()
    shares = A.openbot_shares_claude()
    if getattr(args, "agents", None):
        wanted = [x.strip() for x in args.agents.split(",") if x.strip()]
        bad = [w for w in wanted if w not in A.BY_ID]
        if bad:
            ui.fail(f"unknown agent(s): {', '.join(bad)}. Known: {', '.join(A.BY_ID)}")
            return 2
        agents = [A.BY_ID[w] for w in wanted]
        skipped_claude = False
    else:
        agents = [a for a, _ in found if not (a.id == "claude-code" and shares)]
        skipped_claude = shares and any(a.id == "claude-code" for a, _ in found)
    skills_flag = getattr(args, "skills", "agentos")
    c = Choices(
        agents=agents, mcp=not getattr(args, "no_mcp", False),
        bundled=skills_flag in ("agentos", "all"),
        # Adding the person's skills to the shared workspace only happens when they
        # say so: --upload, or "yes" to the one question setup asks.
        upload=bool(getattr(args, "upload", None)),
        ws_slugs=[x.strip() for x in (getattr(args, "workspace_skills", "") or "").split(",") if x.strip()],
        compression=bool(getattr(args, "compression", False) or getattr(args, "capture", False)))

    who = f"signed in ({src})" if signed else "not signed in"
    ui.header("AgentOS setup", who)
    if not found and not getattr(args, "agents", None):
        ui.warn("No coding agents found. Install Claude Code, Codex, Cursor, opencode or Gemini CLI, then run `aos setup` again.")
        return 1
    ui.console.print("[dim]Found:[/] " + " · ".join(a.name for a, _ in found))
    ui.console.print()

    client, ws_cache = None, {}

    def load_workspace():
        nonlocal client
        if client is None:
            with ui.console.status("Checking your workspace…", spinner="dots"):
                client = _client()
                ws_cache["list"] = S.workspace_list(client)
        if skills_flag in ("workspace", "all") and not c.ws_slugs:
            c.ws_slugs = [w["slug"] for w in ws_cache["list"]]

    will_ask = inter and getattr(args, "upload", None) is None
    if signed and (c.upload or will_ask or c.ws_slugs or skills_flag in ("workspace", "all")):
        try:
            load_workspace()
        except Exception as e:
            ui.warn(f"Couldn't read your workspace ({type(e).__name__}); skills upload skipped.")
            c.upload, c.ws_slugs, will_ask = False, [], False
    if will_ask and ask_upload(c, ws_cache) is None:
        ui.dim("Nothing was changed.")
        return 130

    while True:
        plan = build_plan(c, args.force, client, ws_cache)
        anything = render(plan, c, signed, skipped_claude)
        if not anything:
            ui.console.print()
            ui.ok("Everything is already set up.")
            return 0
        if not inter:
            break
        import questionary
        ui.console.print()
        pick = questionary.select("", choices=["Set up", "Customize…", "Show file changes", "Quit"],
                                  style=ui.qstyle(), qmark="", instruction=" ").ask()
        if pick in (None, "Quit"):
            ui.dim("Nothing was changed.")
            return 1
        if pick == "Set up":
            break
        if pick == "Show file changes":
            for ch in plan.changes:
                ui.console.print(f"[bold]{ui.short(ch.path)}[/]")
                ui.diff(ch.before, ch.after, ui.short(ch.path), max_lines=30)
            continue
        if customize(c, found, signed, ws_cache if client else {}) is None:
            return 130
        ui.console.print()

    if args.dry_run:
        ui.dim("Dry run: nothing was changed.")
        if args.json:
            print(json.dumps({"changes": [{"agent": ch.agent, "path": str(ch.path)} for ch in plan.changes],
                              "skills": [{"skill": x[0].name, "dir": str(x[1]), "status": x[3]} for x in plan.skills],
                              "uploads": [s["name"] for s in plan.uploads]}))
        return 0

    ui.console.print()
    # Sign in only when something needs it (uploads, workspace skills).
    if not signed and (c.upload or c.ws_slugs):
        if not inter:
            ui.warn("Not signed in: skipping skill upload. Run `aos login`, then `aos setup` again.")
            c.upload, c.ws_slugs = False, []
        else:
            ui.console.print("Signing you in. Your browser will open.")
            try:
                auth.login(say=lambda url: ui.dim(f"  If it doesn't open: {url[:80]}…"))
                signed = True
                ui.ok("Signed in")
                load_workspace()
            except Exception as e:
                ui.warn(f"Sign-in didn't finish ({e}); skipping skill upload.")
                c.upload, c.ws_slugs = False, []
        plan = build_plan(c, args.force, client, ws_cache)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    rec = {"at": stamp, "mcp": [], "skills": [], "uploaded": []}
    result: dict[str, list[str]] = {x.name: [] for x in c.agents}
    errors: dict[str, str] = {}
    files: dict[str, str] = {}
    with ui.console.status("Setting up your agents…", spinner="dots"):
        for ch in plan.changes:
            name = A.BY_ID[ch.agent].name
            try:
                r = _apply_change(ch, stamp, lambda ch=ch: A.plan_mcp_add(A.BY_ID[ch.agent], force=args.force))
                rec["mcp"].append(r)
                result[name].append("AgentOS MCP server added")
                files[name] = ui.short(ch.path)
            except Exception as e:
                errors[name] = f"couldn't write {ui.short(ch.path)} ({e})"
        held = []
        got: dict[str, int] = {}
        for sk, base, users, status, dest in plan.skills:
            if status not in ("new", "update"):
                if status in ("edited", "clash"):
                    held.append(f"{sk.name} ({'you edited it' if status == 'edited' else 'name taken'})")
                continue
            try:
                st, d = S.install(sk, base, args.force)
                if st in ("new", "update"):
                    rec["skills"].append({"path": str(d), "source": sk.source, "id": sk.id})
                    for u in users:
                        got[u] = got.get(u, 0) + 1
            except Exception as e:
                ui.fail(f"skill {sk.name}: {e}")
    if c.bundled:
        for d in S.retire([b for b, _ in S.target_dirs([x.id for x in c.agents])]):
            rec.setdefault("retired", []).append(str(d))
    for name, n in got.items():
        result.setdefault(name, []).append(f"{n} skill{'s' * (n != 1)}")
    from rich.table import Table
    t = Table.grid(padding=(0, 3))
    t.add_column(no_wrap=True)
    t.add_column()
    t.add_column(style="dim")
    for name, parts in result.items():
        if name in errors:
            t.add_row(f"[red]✕[/] {name}", errors[name], "")
        elif parts:
            t.add_row(f"[green]✓[/] {name}", " · ".join(parts), files.get(name, ""))
    ui.console.print(t)
    if rec["mcp"]:
        ui.dim(f"  Backups: {ui.short(paths.backups_dir() / stamp)}")
    if held:
        ui.dim(f"  Left alone: {_names(held)}")

    if plan.uploads and client is not None:
        up, failed, stopped = _upload(client, plan.uploads)
        rec["uploaded"] = up
        if stopped:
            _record(rec)
            return 130
        if up:
            extra = sum(1 for s in plan.uploads if s["name"] in up and s["extra_files"] > 0)
            ui.ok(f"Uploaded {len(up)} of your skills to your workspace  [dim]{_names(up)}[/]")
            if extra:
                ui.dim(f"  {extra} of them have extra files next to SKILL.md; only SKILL.md was uploaded.")
        if failed:
            ui.warn(f"Couldn't upload {_names(failed)}")
    _record(rec)

    if plan.compression:
        exe = shutil.which("agentos-capture")
        if not exe:
            ui.warn("Compression needs agentos-capture, which isn't installed.")
        else:
            # -y keeps every other capture setting as it is; this only turns compression on.
            r = subprocess.run([exe, "setup", "-y", "--compression"], capture_output=True, text=True)
            (ui.ok if r.returncode == 0 else ui.warn)(
                "Compression on" if r.returncode == 0 else f"Compression: agentos-capture setup failed ({r.returncode})")

    _next_steps([a for a in c.agents if a.id in {ch.agent for ch in plan.changes}])
    return 0


UPLOAD_WORKERS = 8


def _upload(client, todo: list[dict]) -> tuple[list[str], list[str], bool]:
    """Upload skills in parallel with a progress bar. Ctrl-C stops cleanly: what went
    up stays up and is reported; nothing half-written (each skill is one insert)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
    up, failed = [], []

    def one(s):
        res = S.push(client, s)
        if isinstance(res, dict) and res.get("ok") is False:
            raise RuntimeError(res.get("error") or "refused")
        return s["name"]

    cols = (TextColumn("  Uploading your skills"), BarColumn(bar_width=30), MofNCompleteColumn(), TimeElapsedColumn())
    pool = ThreadPoolExecutor(max_workers=UPLOAD_WORKERS)
    try:
        with Progress(*cols, console=ui.console, transient=True) as bar:
            task = bar.add_task("upload", total=len(todo))
            futures = {pool.submit(one, s): s for s in todo}
            for f in as_completed(futures):
                try:
                    up.append(f.result())
                except Exception:
                    failed.append(futures[f]["name"])
                bar.advance(task)
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        ui.warn(f"Stopped. {len(up)} of {len(todo)} skills were uploaded; run `aos setup` again to finish.")
        return up, failed, True
    pool.shutdown(wait=True)
    return up, failed, False


def _next_steps(agents) -> None:
    if not agents:
        return
    ui.console.print()
    ui.console.print("[bold]Last step[/]: each agent signs in to AgentOS once")
    for a in agents:
        ui.console.print(f"  [dim]•[/] {a.name}: [dim]{a.sign_in}[/]")
    ui.dim("Then ask your agent to “set up AgentOS”. Undo: aos setup --undo")


def undo(args, inter: bool) -> int:
    ui.header("Remove AgentOS from your agents")
    chosen = [A.BY_ID[x] for x in args.agents.split(",")] if args.agents else [a for a, _ in A.detected()]
    changes = [c for a in chosen if (c := A.plan_mcp_remove(a, force=args.force))]
    m = paths.read_json(paths.manifest_path(), {"runs": []})
    recorded = []
    for r in m.get("runs", []):
        for s in r.get("skills", []):
            p = Path(s["path"])
            if p.exists() and S.read_marker(p) and p not in recorded:
                recorded.append(p)
    for ch in changes:
        ui.console.print(f"  [red]-[/] {ch.summary}  [dim]{ui.short(ch.path)}[/]")
        ui.diff(ch.before, ch.after, ui.short(ch.path), max_lines=16)
    for p in recorded:
        ui.console.print(f"  [red]-[/] skill {p.name}  [dim]{ui.short(p)}[/]")
    if not (changes or recorded):
        ui.ok("Nothing from AgentOS to remove.")
        return 0
    if args.dry_run:
        ui.dim("Dry run: nothing was changed.")
        return 0
    if inter:
        import questionary
        if not questionary.confirm("Remove these?", default=False, style=ui.qstyle()).ask():
            ui.dim("Nothing was changed.")
            return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for ch in changes:
        r = _apply_change(ch, stamp, lambda ch=ch: A.plan_mcp_remove(A.BY_ID[ch.agent], force=args.force))
        ui.ok(f"{ch.summary}  [dim]{r['status']}" + (f" · backup {ui.short(r['backup'])}" if r.get("backup") else "") + "[/]")
    for p in recorded:
        st = S.remove(p.parent, p.name, args.force)
        (ui.ok if st == "removed" else ui.warn)(f"skill {p.name}: {st}")
    return 0
