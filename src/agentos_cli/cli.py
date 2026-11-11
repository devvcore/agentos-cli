"""aos: the AgentOS CLI."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import __version__, agents as A, auth, paths, skills as S, ui

HELP = [
    ("Get started", [
        ("login", "sign in (opens your browser; creates an account if you need one)"),
        ("setup", "add AgentOS to every coding agent here: MCP server, skills, compression"),
        ("doctor", "check sign-in, connection and what each agent has"),
    ]),
    ("Work", [
        ("context <subject>", "what AgentOS knows about a person, company, Project or topic"),
        ("tools [--q words]", "find workspace tools; --name <tool> for its schema"),
        ("call <tool> [json]", "run any workspace tool (JSON args, --args-file, or stdin)"),
        ("integrations …", "use connected apps; AgentOS holds the credentials"),
    ]),
    ("Credentials", [
        ("secrets set <name>", "store a key in your workspace (hidden prompt or stdin; never shown again)"),
        ("secrets list", "stored key names and descriptions, never values"),
        ("http <key> <url>", "call an API with a stored key; AgentOS adds it, it never reaches you"),
        ("connect <app>", "connect Gmail, HubSpot, Stripe… with the app's own sign-in"),
    ]),
    ("Spaces", [
        ("link · status · pull · push", "share a folder as a space with versions"),
        ("diff · restore · log", "see and undo changes"),
        ("resolve <file>", "after a pull conflict: mark the file merged so push works again"),
        ("claim · release · handoff", "coordinate with people and other agents"),
        ("wait · watch", "block until a push, claim or handoff happens"),
    ]),
    ("Skills and agents", [
        ("skills <verb>", "list · add · remove · update · outdated · search: skills in your agents"),
        ("agents", "coding agents found on this computer"),
        ("capture …", "compression and work capture (agentos-capture)"),
    ]),
    ("Account", [
        ("whoami", "who you're signed in as"),
        ("auth token · status", "the sign-in for scripts and tools (AgentOS Capture uses it)"),
        ("logout", "sign out on this computer"),
    ]),
]

EXIT_CODES = "Exit codes: 0 ok · 1 error · 2 waiting for approval · 3 claimed by someone else · 4 timed out"


def print_help() -> None:
    from rich.table import Table
    ui.header("aos  ·  AgentOS from your terminal", f"v{__version__}")
    t = Table.grid(padding=(0, 3))
    t.add_column(no_wrap=True)
    t.add_column(style="dim")
    for n, (title, rows) in enumerate(HELP):
        if n:
            t.add_row("", "")
        t.add_row(f"[bold {ui.ACCENT}]{title}[/]", "")
        for cmd, desc in rows:
            t.add_row(f"  aos {cmd}", desc)
    ui.console.print(t)
    ui.console.print()
    ui.dim(EXIT_CODES + "\nOutput is JSON when piped. Run `aos <command> --help` for options.")


def _one_line(text, n: int = 72) -> str:
    """First sentence or n characters of a description, on one line, with an ellipsis when cut."""
    t = " ".join(str(text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    if m and len(m.group(1)) <= n:
        return m.group(1)
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _emit(data, as_json: bool | None = None) -> None:
    if as_json or (as_json is None and not sys.stdout.isatty()):
        print(json.dumps(data, ensure_ascii=False, default=str))
    else:
        ui.console.print_json(json.dumps(data, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- account

def cmd_login(a) -> int:
    scopes = list(auth.DEFAULT_SCOPES) + [f"workspace:{s}" for s in (a.scope or [])]
    ui.header("Sign in to AgentOS", paths.base_url())
    ui.console.print("Opening your browser. New to AgentOS? You can create your account there.")
    ui.dim("If the browser doesn't open, paste this link:")
    try:
        data = auth.login(scopes, say=lambda url: ui.console.print(f"  [link={url}]{url[:90]}…[/link]"),
                          open_browser=(lambda u: None) if a.no_browser else __import__("webbrowser").open)
    except auth.LoginError as e:
        ui.fail(str(e))
        return 1
    ui.ok(f"Signed in. Token saved to the {data['stored_in'] if data['stored_in'] == 'keychain' else ui.short(data['stored_in'])}"
          f" (refreshes itself; scopes: {data.get('scope') or ' '.join(scopes)}).")
    ui.dim("Next: `aos setup` to add AgentOS to your coding agents.")
    return 0


def cmd_logout(a) -> int:
    data = auth.load_login()
    if data:
        auth.revoke(data)
    if auth.clear_login():
        ui.ok("Signed out on this computer.")
    else:
        ui.dim("You weren't signed in with `aos login`.")
    if os.environ.get("AGENTOS_PAT"):
        ui.warn("AGENTOS_PAT is still set in your environment; unset it to sign out completely.")
    return 0


def _short_version(v: str | None) -> str:
    """0.2.0+b771d577c0b6…(64 hex) -> 0.2.0+b771d57"""
    if not v:
        return "?"
    base, _, build = v.partition("+")
    return f"{base}+{build[:7]}" if build else base


def cmd_whoami(a) -> int:
    try:
        _, src = auth.token_source()
    except auth.NotSignedIn as e:
        ui.fail(str(e))
        return 1
    from .work import connect, call
    res = call(connect(timeout=20), "workspace_map", {})
    who = (res or {}).get("connected_as") if isinstance(res, dict) else None
    who = who if isinstance(who, dict) else {}
    login = auth.load_login() or {}
    info = {"user": who.get("user"), "role": who.get("role"), "workspace": who.get("workspace"),
            "workspace_id": who.get("workspace_id") or login.get("workspace_id"),
            "signed_in_with": src, "server": paths.base_url()}
    if src == "aos login":
        info["scopes"] = login.get("scope")
        info["expires_in_min"] = max(0, int((login.get("expires_at", 0) - time.time()) / 60))
    if a.json or not sys.stdout.isatty():
        print(json.dumps(info, ensure_ascii=False))
        return 0
    from rich.markup import escape
    name = escape(info["user"] or "(name not shared)")
    ws = escape(info["workspace"] or info["workspace_id"] or "?")
    ui.ok(f"[bold]{name}[/] · {info['role'] or 'member'} of [bold]{ws}[/]")
    extra = f"signed in with {src}"
    if "expires_in_min" in info:
        extra += f" · token refreshes itself ({info['expires_in_min']} min left)"
    ui.dim(f"  {extra}\n  {info['server']}" + (f" · scopes: {info['scopes']}" if info.get("scopes") else ""))
    return 0


def _auth_info(src: str) -> dict:
    login = (auth.load_login() or {}) if src == "aos login" else {}
    exp = login.get("expires_at")
    return {"signed_in": True, "source": src, "server": paths.base_url(), "api_url": paths.api_url(),
            "workspace_id": login.get("workspace_id"), "user_id": login.get("user_id"),
            "scope": login.get("scope"), "expires_at": int(exp) if exp else None}


def cmd_auth(a) -> int:
    """`aos auth token` is for other tools (AgentOS Capture, scripts), like `gh auth token`:
    the token goes to stdout and nowhere else; refresh is handled here, under a lock."""
    try:
        token, src = auth.token_source()
    except auth.NotSignedIn as e:
        if a.auth_cmd == "status" and (a.json or not sys.stdout.isatty()):
            print(json.dumps({"signed_in": False, "server": paths.base_url(), "error": str(e)}))
        else:
            print(f"aos: {e}", file=sys.stderr)
        return 1
    info = _auth_info(src)
    if a.auth_cmd == "token":
        print(json.dumps({**info, "access_token": token}) if a.json else token)
        return 0
    if a.json or not sys.stdout.isatty():
        print(json.dumps(info))
        return 0
    left = f" · token good for {max(0, int((info['expires_at'] - time.time()) / 60))} min, refreshes itself" if info["expires_at"] else ""
    ui.ok(f"Signed in ({src}) to workspace {info['workspace_id'] or '?'}{left}")
    if info["scope"]:
        ui.dim(f"  scopes: {info['scope']}")
    return 0


# ---------------------------------------------------------------- agents & skills

def cmd_agents(a) -> int:
    rows = []
    for ag in A.AGENTS:
        where = ag.found()
        rows.append({"id": ag.id, "name": ag.name, "found": where, "mcp": A.mcp_status(ag) if where else None,
                     "skills_dir": str(ag.skills_dir()) if ag.skills_dir else None})
    if a.json or not sys.stdout.isatty():
        print(json.dumps(rows))
        return 0
    t = ui.table("", "Agent", "id", "Found at", "AgentOS MCP")
    for r in rows:
        t.add_row("[green]●[/]" if r["found"] else "[dim]○[/]", r["name"], f"[dim]{r['id']}[/]",
                  ui.short(r["found"]) if r["found"] else "[dim]—[/]",
                  {"ours": "[green]connected[/]", "custom": "[yellow]custom[/]", "absent": "—", "link": "in the app",
                   "error": "[red]unreadable[/]"}.get(r["mcp"] or "", ""))
    ui.console.print(t)
    return 0


def _agent_ids(a):
    if not getattr(a, "agent", None):
        return None
    ids = [x.strip() for x in a.agent.split(",") if x.strip()]
    for i in ids:
        if i not in A.BY_ID:
            raise SystemExit(f"aos: unknown agent {i}. Known: {', '.join(A.BY_ID)}")
    return ids


def _ws_client():
    from .work import connect
    return connect()


def cmd_skills(a) -> int:
    dirs = S.target_dirs(_agent_ids(a))
    bases = [d for d, _ in dirs]
    if a.skills_cmd in ("list", "installed", "outdated"):
        inst = S.installed(bases)
        if a.skills_cmd == "installed":
            rows = [{"name": i["folder"], "source": i["source"], "id": i["id"], "path": str(i["path"]), "edited": i["edited"]} for i in inst]
            return _skills_table(rows, a, ("name", "source", "path", "edited"))
        avail = [{"name": s.name, "source": "agentos", "id": s.id, "description": s.description, "sha": s.sha} for s in S.bundled()]
        ws_err = None
        if not a.agentos_only:
            try:
                for w in S.workspace_list(_ws_client()):
                    avail.append({"name": S.safe_name(w["slug"]), "source": "workspace", "id": w["slug"], "description": w["description"] or w["name"]})
            except auth.NotSignedIn:
                ws_err = "sign in (`aos login`) to see your workspace's skills"
            except Exception as e:
                ws_err = f"couldn't read workspace skills ({type(e).__name__})"
        by = {(i["source"], i["id"]): i for i in inst}
        if a.skills_cmd == "outdated":
            client = None
            rows = []
            for i in inst:
                cur = next((s for s in S.bundled() if s.id == i["id"]), None) if i["source"] == "agentos" else None
                if i["source"] == "workspace":
                    try:
                        client = client or _ws_client()
                        cur = S.workspace_skill(client, i["id"])
                    except Exception:
                        cur = None
                if cur and cur.sha != i["sha"]:
                    rows.append({"name": i["folder"], "source": i["source"], "path": str(i["path"]), "edited": i["edited"]})
            if not rows and not a.json and sys.stdout.isatty():
                ui.ok("All installed AgentOS skills are up to date.")
                return 0
            return _skills_table(rows, a, ("name", "source", "path", "edited"))
        for r in avail:
            i = by.get((r["source"], r["id"]))
            r["installed"] = bool(i)
        rc = _skills_table(avail, a, ("name", "source", "installed", "description"))
        if ws_err and sys.stdout.isatty():
            ui.dim(ws_err)
        return rc

    if a.skills_cmd == "push":
        from pathlib import Path
        local = S.local_skills([Path(p).expanduser().parent for p in a.paths] if a.paths else bases + [paths.home() / ".codex" / "skills"])
        if a.paths:
            want = {Path(p).expanduser().resolve() for p in a.paths}
            local = [s for s in local if s["path"].resolve() in want]
        client = _ws_client()
        todo = S.not_in_workspace(local, S.workspace_list(client, include_untitled=True))
        if a.dry_run or not todo:
            rows = [{"name": s["name"], "path": str(s["path"])} for s in todo]
            return _skills_table(rows, a, ("name", "path")) if rows else (ui.ok("Nothing to upload: they're all in your workspace already.") or 0)
        rc = 0
        for s in todo:
            res = S.push(client, s)
            good = not (isinstance(res, dict) and res.get("ok") is False)
            (ui.ok if good else ui.warn)(f"{s['name']}: {'uploaded' if good else 'not uploaded'}  [dim]{ui.short(s['path'])}[/]")
            rc = rc or (0 if good else 1)
        return rc

    if a.skills_cmd == "search":
        res = S.call_tool(_ws_client(), "skills_search", {"q": " ".join(a.query), **({"where": "online"} if a.online else {})})
        _emit(res, a.json)
        return 0

    if a.skills_cmd in ("add", "update"):
        wanted = a.names
        bundled = {s.name: s for s in S.bundled()}
        todo: list[S.Skill] = []
        client = None
        if a.skills_cmd == "update" and not wanted:
            inst = S.installed(bases)
            wanted = sorted({i["id"] for i in inst})
        for n in wanted:
            if n in bundled:
                todo.append(bundled[n])
                continue
            try:
                client = client or _ws_client()
                todo.append(S.workspace_skill(client, n))
            except auth.NotSignedIn as e:
                ui.fail(str(e))
                return 1
            except Exception as e:
                ui.fail(f"{n}: not an AgentOS skill and not found in your workspace ({type(e).__name__}).")
        if not dirs:
            ui.fail("No agent with a skills folder found. Use --agent to pick one.")
            return 1
        rc = 0
        for sk in todo:
            for base, users in dirs:
                st, d = S.install(sk, base, a.force)
                msg = {"new": "installed", "update": "updated", "current": "already up to date",
                       "edited": "you edited it, left alone (--force to replace)",
                       "clash": "a folder with that name isn't ours, skipped"}[st]
                (ui.ok if st in ("new", "update", "current") else ui.warn)(
                    f"{(d.name if d else sk.name)}: {msg}  [dim]{ui.short(base)} · {', '.join(users)}[/]")
                rc = rc or (0 if st in ("new", "update", "current") else 1)
        return rc

    if a.skills_cmd == "remove":
        rc = 0
        for n in a.names:
            for base, users in dirs:
                st = S.remove(base, n, a.force)
                if st == "missing":
                    continue
                (ui.ok if st == "removed" else ui.warn)(f"{n}: {st}  [dim]{ui.short(base)}[/]")
                rc = rc or (0 if st == "removed" else 1)
        return rc
    return 2


def _skills_table(rows, a, cols) -> int:
    if a.json or not sys.stdout.isatty():
        print(json.dumps(rows, default=str))
        return 0
    if not rows:
        ui.dim("(none)")
        return 0
    t = ui.table(*[c.capitalize() for c in cols])
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, bool):
                v = "[green]✓[/]" if v else "[dim]—[/]"
            elif c == "path":
                v = ui.short(v)
            elif c == "description":
                v = f"[dim]{_one_line(v, 70)}[/]"
            vals.append(str(v if v is not None else ""))
        t.add_row(*vals)
    ui.console.print(t)
    return 0


# ---------------------------------------------------------------- work

def cmd_tools(a) -> int:
    from concurrent.futures import ThreadPoolExecutor
    from .work import connect, call
    client = connect()
    args = {k: v for k, v in {"q": a.q, "family": a.family, "name": a.name, "limit": a.limit}.items() if v}
    # A search also looks through connected-app actions (Slack, Gmail, GitHub…), in parallel:
    # those live behind `aos integrations`, and people search for "send slack", not for a family.
    want_apps = bool(a.q) and not a.name and not a.family
    with ThreadPoolExecutor(2) as pool:
        tools_f = pool.submit(client.call, "workspace_tools", **args)
        apps_f = pool.submit(call, client, "integration_search", {"q": a.q}) if want_apps else None
        res = tools_f.result()
        try:
            apps = apps_f.result() if apps_f else None
        except Exception:
            apps = None
    app_rows = [t for t in ((apps or {}).get("tools") or []) if isinstance(t, dict)][: a.limit] if isinstance(apps, dict) else []
    if a.json or not sys.stdout.isatty() or a.name:
        if want_apps and isinstance(res, dict):
            res = {**res, "app_actions": app_rows}
        _emit(res, a.json)
        return 0
    tools = (res or {}).get("tools", [])
    if tools:
        t = ui.table("Tool", "Family", "What it does")
        for tool in tools:
            t.add_row(f"[bold]{tool['name']}[/]", f"[dim]{tool.get('family', '')}[/]", _one_line(tool.get("description")))
        ui.console.print(t)
        ui.dim("Run one with `aos call <tool> '{...}'`; `aos tools --name <tool>` shows its arguments.")
    if app_rows:
        ui.console.print()
        t = ui.table("App action", "App", "Connected", "What it does", title="Connected-app actions")
        for r in app_rows:
            t.add_row(f"[bold]{r.get('slug', '')}[/]", r.get("toolkit_slug", ""),
                      "[green]✓[/]" if r.get("connected") else "[dim]—[/]", _one_line(r.get("description") or r.get("name"), 60))
        ui.console.print(t)
        ui.dim("Run one with `aos integrations call <ACTION> --args-file -`; `aos integrations describe <ACTION>` shows its arguments.")
    if not tools and not app_rows:
        ui.dim("Nothing matched. Try fewer words.")
    elif (res or {}).get("next_cursor"):
        ui.dim(f"{res.get('total')} tools match; narrow with --q or --family.")
    return 0


def cmd_call(a) -> int:
    from .work import connect, call, parse_args_input, outcome
    args = parse_args_input(a.json_args, a.args_file)
    res = call(connect(), a.tool, args)
    _emit(res, a.json)
    return outcome(res)


def cmd_context(a) -> int:
    from .work import connect, call, outcome
    args = {"subject": " ".join(a.subject), "mode": "evidence"}
    if a.question:
        args["question"] = a.question
    res = call(connect(), "context", args)
    _emit(res, a.json)
    return outcome(res)


# ---------------------------------------------------------------- doctor

def capture_health() -> tuple[str, str]:
    """(level, message) for AgentOS Capture: whether it runs and whether anything goes through it."""
    exe = shutil.which("agentos-capture")
    if not exe:
        return "dim", "Compression + capture: not installed (optional; `aos setup --capture`)"
    cfg = paths.read_json(paths.home() / ".agentos-capture" / "config.json", {})
    port = int(cfg.get("port") or 8797)
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_capture/status", timeout=1.5) as r:
            st = json.loads(r.read())
    except Exception:
        return "warn", (f"Compression + capture: installed but not running on port {port}. "
                        "Run `agentos-capture install`, then `agentos-capture doctor`.")
    reqs = int((st.get("counters") or {}).get("requests") or 0)
    hook = ((st.get("plugin_status") or {}).get("hook") or {})
    counts = hook.get("counts") if isinstance(hook.get("counts"), dict) else {}
    seen = reqs + sum(int(v) for v in counts.values() if isinstance(v, (int, float)))
    if seen == 0:
        up = float(st.get("uptime_s") or 0)
        when = f"in {int(up // 60)} min" if up >= 60 else "yet"
        return "warn", (f"Compression + capture: running on port {port}, but no agent has gone through it {when}. "
                        "Restart your agents, then run `agentos-capture doctor` if it stays at zero.")
    return "ok", f"Compression + capture: running on port {port} · {seen} request(s) since it started"


def cmd_doctor(a) -> int:
    ui.header("aos doctor", paths.base_url())
    rc = 0
    try:
        _, src = auth.token_source()
        ui.ok(f"Signed in ({src})")
        try:
            from .work import connect
            t0 = time.perf_counter()
            c = connect(timeout=15, info=True)
            ms = (time.perf_counter() - t0) * 1000
            ui.ok(f"AgentOS reachable · server {_short_version(c.server_info.get('version'))} · {ms:.0f} ms")
        except auth.NotSignedIn as e:
            ui.fail(str(e))
            rc = 1
        except Exception as e:
            from .mcp import friendly_error
            ui.fail(f"AgentOS not reachable with this sign-in: {friendly_error(e)}")
            rc = 1
    except auth.NotSignedIn:
        ui.warn("Not signed in. Run `aos login`.")
        rc = 1
    found = A.detected()
    for ag, where in found:
        st = A.mcp_status(ag)
        n = len(S.installed([ag.skills_dir()])) if ag.skills_dir else 0
        msg = {"ours": "AgentOS MCP added", "custom": "has its own agentos entry", "absent": "AgentOS MCP not added",
               "link": "connector is added inside the app", "error": "MCP config can't be read"}[st]
        (ui.ok if st in ("ours", "link") else ui.warn)(f"{ag.name}: {msg}; {n} AgentOS skill(s)  [dim]{ui.short(where)}[/]")
    if not found:
        ui.warn("No coding agents found on this computer.")
    level, msg = capture_health()
    {"ok": ui.ok, "warn": ui.warn, "dim": ui.dim}[level](msg)
    if any(A.mcp_status(ag) == "absent" for ag, _ in found):
        ui.dim("Run `aos setup` to add AgentOS where it's missing.")
    return rc


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aos", add_help=False)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--version", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("login", help="sign in")
    s.add_argument("--scope", action="append", choices=["outbound", "compute", "secrets"],
                   help="also ask for this permission (outbound: send and act in apps; compute: run bots and apps; "
                        "secrets: store keys and call APIs with them)")
    s.add_argument("--no-browser", action="store_true", help="print the link instead of opening it")
    s.set_defaults(fn=cmd_login)
    sub.add_parser("logout").set_defaults(fn=cmd_logout)
    s = sub.add_parser("whoami")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_whoami)

    s = sub.add_parser("auth", help="sign-in details for scripts and other tools")
    ss = s.add_subparsers(dest="auth_cmd", required=True)
    x = ss.add_parser("token", help="print a current access token (refreshed when needed)")
    x.add_argument("--json", action="store_true", help="also its expiry, workspace and scopes")
    x = ss.add_parser("status", help="whether you're signed in, to which workspace, until when (no token)")
    x.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_auth)

    s = sub.add_parser("setup", help="add AgentOS to your coding agents")
    s.add_argument("--yes", "-y", action="store_true", help="no questions; use the defaults or the flags below")
    s.add_argument("--agents", help="comma-separated agent ids (default: all found). See `aos agents`.")
    s.add_argument("--skills", choices=["agentos", "workspace", "all", "none"], default="agentos")
    s.add_argument("--workspace-skills", help="comma-separated workspace skill slugs")
    s.add_argument("--no-mcp", action="store_true")
    s.add_argument("--compression", "--capture", dest="compression", action="store_true",
                   help="also turn on compression (agentos-capture)")
    s.add_argument("--upload", dest="upload", action="store_true", default=None,
                   help="upload your own skills to the workspace (on by default when you run setup yourself)")
    s.add_argument("--no-upload", dest="upload", action="store_false")
    s.add_argument("--force", action="store_true", help="replace a custom agentos entry or a skill you edited")
    s.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    s.add_argument("--undo", action="store_true", help="remove what aos setup added")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=lambda a: __import__("agentos_cli.setup_cmd", fromlist=["run"]).run(a))

    s = sub.add_parser("agents")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_agents)

    s = sub.add_parser("skills")
    ss = s.add_subparsers(dest="skills_cmd", required=True)
    for name in ("list", "installed", "outdated"):
        x = ss.add_parser(name)
        x.add_argument("--agent")
        x.add_argument("--json", action="store_true")
        x.add_argument("--agentos-only", action="store_true", help="skip the workspace library")
    x = ss.add_parser("search")
    x.add_argument("query", nargs="+")
    x.add_argument("--online", action="store_true", help="search public skills too")
    x.add_argument("--json", action="store_true")
    x = ss.add_parser("push", help="upload skills you wrote to your workspace")
    x.add_argument("paths", nargs="*", help="skill folders (default: your agents' skills not yet in the workspace)")
    x.add_argument("--dry-run", action="store_true")
    x.add_argument("--json", action="store_true")
    for name in ("add", "update", "remove"):
        x = ss.add_parser(name)
        x.add_argument("names", nargs="*" if name == "update" else "+")
        x.add_argument("--agent", help="comma-separated agent ids (default: every agent found)")
        x.add_argument("--force", action="store_true")
        x.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_skills)

    s = sub.add_parser("tools")
    s.add_argument("--q")
    s.add_argument("--family")
    s.add_argument("--name")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_tools)

    s = sub.add_parser("call")
    s.add_argument("tool")
    s.add_argument("json_args", nargs="?")
    s.add_argument("--args-file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_call)

    s = sub.add_parser("context")
    s.add_argument("subject", nargs="+")
    s.add_argument("--question", "-q")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_context)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    from . import secrets_cmd
    secrets_cmd.register(sub)
    return p


def _friendly(fn):
    from .mcp import friendly_error
    try:
        return fn()
    except KeyboardInterrupt:
        return 130
    except auth.NotSignedIn as e:
        ui.fail(str(e))
        return 1
    except (FileNotFoundError, PermissionError) as e:
        ui.fail(str(e))
        return 1
    except (OSError, RuntimeError, ValueError) as e:
        msg = friendly_error(e)
        if "Bearer" in msg:  # never echo a credential
            msg = type(e).__name__
        from rich.markup import escape
        ui.fail(escape(msg))
        return 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    i = 0
    while i < len(argv) and argv[i].startswith("--") and argv[i] not in ("--help", "--version"):
        i += 1 if "=" in argv[i] else 2
    verb = argv[i] if i < len(argv) else None

    from . import spaces
    if verb in spaces.VERBS:
        return _friendly(lambda: spaces.main(argv))
    if verb == "integrations":
        from . import integrations
        return _friendly(lambda: integrations.main(argv[i + 1:]))
    from .integrations import SERVICE_TOOLS
    if verb in SERVICE_TOOLS:
        from . import integrations
        return _friendly(lambda: integrations.service_main(verb, argv[i + 1:]))
    if verb == "capture":
        exe = shutil.which("agentos-capture")
        if not exe:
            ui.fail("Compression and capture come from agentos-capture, which isn't installed. See `aos doctor`.")
            return 1
        return subprocess.call([exe, *argv[i + 1:]])

    p = build_parser()
    if verb is None or verb in ("help", "-h", "--help"):
        print_help()
        return 0
    if verb == "--version":
        print(f"aos {__version__}")
        return 0
    a = p.parse_args(argv)
    if a.help and a.cmd is None:
        print_help()
        return 0
    return _friendly(lambda: a.fn(a))


if __name__ == "__main__":
    raise SystemExit(main())
