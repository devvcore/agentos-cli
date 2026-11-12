"""`aos integrations`: everything an agent or person can do through AgentOS, in one list.

Two kinds of action, shown together:
  * AgentOS's own: tasks, CRM, memory, files… (workspace tools, snake_case names
    like `tasks_list`), labelled "AgentOS".
  * Connected apps: Gmail, Slack, Stripe… (registry operations, UPPER_CASE slugs
    like `GMAIL_SEND_EMAIL`), labelled with the app. AgentOS holds their credentials.

Commands:
  aos integrations                    AgentOS plus connected apps, and what each has
  aos integrations <words>            find actions in both
  aos integrations show <action>      what it does and its arguments
  aos integrations run <action> [json|--args-file f|stdin]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import ui

SUBCOMMANDS = {"list", "search", "show", "describe", "run", "call"}
OWN_WHEN_APPS = 5
APP_SLUG = re.compile(r"^[A-Z][A-Z0-9_]+$")


MCP_SLUG = re.compile(r"^mcp_[0-9a-f]{6,}_")


def is_app_action(name: str) -> bool:
    """Connected-app actions: registry slugs (GMAIL_SEND_EMAIL) and connected MCP servers' tools
    (mcp_<id>_<tool>). Everything else is one of AgentOS's own workspace tools."""
    return bool(APP_SLUG.match(name) or MCP_SLUG.match(name))


def _client():
    from .work import connect
    return connect()


def _call(client, tool: str, args: dict) -> Any:
    from .work import call
    return call(client, tool, args)


def _app_name(t: dict) -> str:
    """The app a row belongs to: its toolkit, or for a connected MCP server the word after its id."""
    if t.get("toolkit_slug"):
        return t["toolkit_slug"]
    m = re.match(r"^mcp_[0-9a-f]+_([a-z0-9]+)", t.get("slug") or "")
    return f"{m.group(1)} (MCP)" if m else "app"


def _one_line(text, n=72) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _json_out(a) -> bool:
    return getattr(a, "json", False) or not sys.stdout.isatty()


# ---------------------------------------------------------------- list / search

def search(client, words: str, limit: int = 15) -> list[dict]:
    """Rows of {action, source, kind, connected, description}: AgentOS first; both searched in parallel."""
    with ThreadPoolExecutor(2) as pool:
        own_f = pool.submit(client.call, "workspace_tools", q=words, limit=min(limit, 20))
        app_f = pool.submit(_call, client, "integration_search", {"q": words, "limit": min(limit, 50)})
        own = own_f.result()
        try:
            apps = app_f.result()
        except Exception:
            apps = None
    own_rows = [{"action": t["name"], "source": "AgentOS", "kind": "agentos", "connected": True,
                 "description": t.get("description") or ""}
                for t in (((own or {}).get("tools") or []) if isinstance(own, dict) else [])]
    apps = apps if isinstance(apps, dict) else {}
    app_rows = [{"action": t["slug"], "source": _app_name(t), "kind": "app",
                 "connected": bool(t.get("connected")), "description": t.get("description") or t.get("name") or ""}
                for t in apps.get("tools") or [] if isinstance(t, dict) and t.get("slug")]
    # Workspace-tool search is broad (any word in any description); keep its best few so
    # the app actions people usually mean ("send email" → Gmail) aren't pushed off the screen.
    # Named an app ("gmail send")? Its actions come first.
    named = {w.lower() for w in words.split()}
    if any(r["source"].lower() in named for r in app_rows):
        return app_rows + own_rows[:OWN_WHEN_APPS]
    if not app_rows:
        return own_rows[:limit]
    # An AgentOS action whose own name has a search word ("tasks" → tasks_list) is a direct
    # hit; one that only mentions a word in its description goes after the app actions.
    direct = [r for r in own_rows if any(w in r["action"].lower().split("_") for w in named)]
    loose = [r for r in own_rows if r not in direct]
    return (direct + app_rows + loose)[: max(limit, len(direct) + len(app_rows))][: limit + OWN_WHEN_APPS]


def cmd_list(a) -> int:
    client = _client()
    if a.words:
        rows = search(client, " ".join(a.words), a.limit)
        if _json_out(a):
            print(json.dumps(rows, ensure_ascii=False))
            return 0
        if not rows:
            ui.dim("Nothing matched. Try fewer or different words.")
            return 0
        t = ui.table("Action", "From", "", "What it does")
        for r in rows:
            mark = "[green]✓[/]" if r["connected"] else "[dim]not connected[/]"
            t.add_row(f"[bold]{r['action']}[/]", r["source"], mark, _one_line(r["description"]))
        ui.console.print(t)
        ui.dim("Arguments: `aos integrations show <action>`.  Run: `aos integrations run <action> '{…}'`.")
        if any(not r["connected"] for r in rows):
            ui.dim("Not connected? `aos connect <app>`.")
        return 0
    apps = _call(client, "integration_list", {})
    if _json_out(a):
        print(json.dumps(apps, ensure_ascii=False))
        return 0
    t = ui.table("From", "Connected", "Accounts", "Actions")
    t.add_row("[bold]AgentOS[/]", "[green]✓[/]", "[dim]your workspace[/]", "tasks, CRM, memory, files…")
    for r in sorted((apps or {}).get("platforms") or [], key=lambda r: (not r.get("connected"), r.get("toolkit_slug") or "")):
        accts = ", ".join(x.get("label") or x.get("connection_id", "") for x in r.get("accounts") or [] if isinstance(x, dict))
        t.add_row(f"[bold]{r.get('toolkit_name') or r.get('toolkit_slug')}[/]",
                  "[green]✓[/]" if r.get("connected") else "[dim]—[/]", accts or "[dim]—[/]", str(r.get("tool_count") or ""))
    ui.console.print(t)
    ui.dim("Find an action: `aos integrations <words>`, e.g. `aos integrations send email`.  Connect an app: `aos connect <app>`.")
    return 0


# ---------------------------------------------------------------- show

def cmd_show(a) -> int:
    client = _client()
    if is_app_action(a.action):
        res = _call(client, "integration_search", {"q": a.action, "tool_slug": a.action})
        rows = (res.get("tools") or []) if isinstance(res, dict) else []
        info = rows[0] if rows else res
    else:
        info = client.call("workspace_tools", name=a.action)
    if _json_out(a) or not isinstance(info, dict):
        print(json.dumps(info, ensure_ascii=False))
        return 0
    name = info.get("slug") or info.get("name") or a.action
    src = (info.get("toolkit_slug") or "app") if is_app_action(a.action) else "AgentOS"
    ui.console.print(f"[bold]{name}[/]  [dim]{src}[/]")
    if info.get("description"):
        ui.console.print(" ".join(str(info["description"]).split()), markup=False)
    schema = info.get("inputSchema") or {}
    props = schema.get("properties") or {}
    req = set(schema.get("required") or info.get("required") or [])
    if props:
        t = ui.table("Argument", "Type", "", "Description")
        for k, v in props.items():
            t.add_row(f"[bold]{k}[/]", str(v.get("type", "")), "[yellow]required[/]" if k in req else "",
                      _one_line(v.get("description"), 70))
        ui.console.print(t)
    elif info.get("required") or info.get("optional"):
        ui.dim(f"required: {', '.join(info.get('required') or []) or '—'}  optional: {', '.join(info.get('optional') or []) or '—'}")
    ui.dim(f"Run: aos integrations run {name} '{{…}}'")
    return 0


# ---------------------------------------------------------------- run

def cmd_run(a) -> int:
    from .work import parse_args_input, outcome
    args = parse_args_input(a.json_args, a.args_file)
    client = _client()
    if is_app_action(a.action):
        call_args = {"tool_slug": a.action, "arguments": args}
        if a.account:
            call_args["connection_id"] = a.account
        res = _call(client, "integration_call", call_args)
    else:
        res = _call(client, a.action, args)
    print(json.dumps(res, ensure_ascii=False, default=str) if _json_out(a)
          else json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return outcome(res)


# ---------------------------------------------------------------- parser

def build_parser(prog="aos integrations") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="Everything you can do through AgentOS: its own actions "
                                "(tasks, CRM, memory…) and your connected apps (Gmail, Slack, Stripe…).")
    sub = p.add_subparsers(dest="sub")
    for name in ("list", "search"):
        s = sub.add_parser(name, help="AgentOS and connected apps, or find actions by words")
        s.add_argument("words", nargs="*")
        s.add_argument("--limit", type=int, default=15)
        s.add_argument("--json", action="store_true")
        s.set_defaults(fn=cmd_list)
    for name in ("show", "describe"):
        s = sub.add_parser(name, help="what an action does and its arguments")
        s.add_argument("action")
        s.add_argument("--json", action="store_true")
        s.set_defaults(fn=cmd_show)
    for name in ("run", "call"):
        s = sub.add_parser(name, help="run an action")
        s.add_argument("action")
        s.add_argument("json_args", nargs="?", help="JSON object of arguments (or --args-file, or stdin)")
        s.add_argument("--args-file", help="JSON file, or - for stdin. Never put credentials here.")
        s.add_argument("--account", help="for a connected app with several accounts: the connection id")
        s.add_argument("--json", action="store_true")
        s.set_defaults(fn=cmd_run)
    return p


def main(argv: list[str]) -> int:
    argv = list(argv)
    # `aos integrations` and `aos integrations <words>` mean list / search.
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["list", *argv]
    a = build_parser().parse_args(argv)
    return a.fn(a)


def legacy_tools(argv: list[str]) -> int:
    """Old `aos tools [--q words] [--name tool]`: same results, through the new command."""
    p = argparse.ArgumentParser(prog="aos tools")
    p.add_argument("words", nargs="*")
    p.add_argument("--q")
    p.add_argument("--name")
    p.add_argument("--family")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.name:
        return main(["show", a.name, *(["--json"] if a.json else [])])
    words = ([a.q] if a.q else []) + a.words + ([a.family] if a.family else [])
    return main(["list", *words, "--limit", str(a.limit), *(["--json"] if a.json else [])])


def legacy_call(argv: list[str]) -> int:
    return main(["run", *argv])
