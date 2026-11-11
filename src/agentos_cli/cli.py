"""aos: the AgentOS CLI."""
from __future__ import annotations

import argparse
import json
import os
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


def cmd_whoami(a) -> int:
    try:
        _, src = auth.token_source()
    except auth.NotSignedIn as e:
        ui.fail(str(e))
        return 1
    from .work import connect, call
    client = connect(timeout=20)
    info = {"signed_in_with": src, "server": paths.base_url(), "server_version": client.server_info.get("version")}
    try:
        res = call(client, "agent_access", {})
        if isinstance(res, dict):
            for k in ("person", "user", "workspace", "agent", "identity", "you"):
                if k in res:
                    info[k] = res[k]
    except Exception:
        pass
    login = auth.load_login() or {}
    if src == "aos login":
        info["scopes"] = login.get("scope")
        info["expires_in_min"] = max(0, int((login.get("expires_at", 0) - time.time()) / 60))
    _emit(info, a.json)
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
                v = f"[dim]{str(v or '')[:70]}[/]"
            vals.append(str(v if v is not None else ""))
        t.add_row(*vals)
    ui.console.print(t)
    return 0


# ---------------------------------------------------------------- work

def cmd_tools(a) -> int:
    from .work import connect, call
    client = connect()
    args = {k: v for k, v in {"q": a.q, "family": a.family, "name": a.name, "limit": a.limit}.items() if v}
    res = client.call("workspace_tools", **args)
    if a.json or not sys.stdout.isatty() or a.name:
        _emit(res, a.json)
        return 0
    t = ui.table("Tool", "Family", "What it does")
    for tool in (res or {}).get("tools", []):
        t.add_row(f"[bold]{tool['name']}[/]", f"[dim]{tool.get('family', '')}[/]", (tool.get("description") or "")[:90])
    ui.console.print(t)
    if (res or {}).get("next_cursor"):
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

def cmd_doctor(a) -> int:
    ui.header("aos doctor", paths.base_url())
    rc = 0
    try:
        _, src = auth.token_source()
        ui.ok(f"Signed in ({src})")
        try:
            from .work import connect
            c = connect(timeout=15)
            ui.ok(f"AgentOS reachable · server {c.server_info.get('version', '?')} · {len(c.list_tools())} top-level tools")
        except Exception as e:
            ui.fail(f"AgentOS not reachable with this sign-in ({type(e).__name__}). Try `aos login`.")
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
    cap = shutil.which("agentos-capture")
    (ui.ok if cap else ui.dim)("Compression + capture: " + ("agentos-capture installed" if cap else "agentos-capture not installed"))
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
    try:
        return fn()
    except KeyboardInterrupt:
        return 130
    except auth.NotSignedIn as e:
        ui.fail(str(e))
        return 1
    except (OSError, RuntimeError) as e:
        if isinstance(e, (FileNotFoundError, PermissionError)):
            ui.fail(str(e))
            return 1
        # urllib errors can include URLs or bodies; name the type only.
        msg = str(e) if isinstance(e, RuntimeError) and len(str(e)) < 300 and "Bearer" not in str(e) else type(e).__name__
        ui.fail(f"AgentOS request failed: {msg}. Nothing was retried.")
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
