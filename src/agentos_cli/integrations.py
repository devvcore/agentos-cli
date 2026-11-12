"""`aos integrations`: the apps connected to your AgentOS workspace, and their status.

That's all it does. To use an app: `aos call <app>`. To connect one: `aos connect <app>`.

The 0.3.0 subcommands (search, show, run, call) still work, unlisted, for old scripts.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import ui

USAGE = """usage: aos integrations [--all] [--json]

The apps connected to your AgentOS workspace (Gmail, Slack, Stripe…), their
accounts, and how many actions each has.

  aos integrations          your connected apps
  aos integrations --all    also apps you can connect

Use one:        aos call <app>        e.g. aos call gmail
Connect one:    aos connect <app>     e.g. aos connect hubspot

options:
  --all     include apps that aren't connected yet
  --json    JSON output (the default when piped)"""


def _client():
    from .work import connect
    return connect()


def _call(client, tool, args):
    from .work import call
    return call(client, tool, args)


def cmd_list(show_all: bool, as_json: bool) -> int:
    res = _call(_client(), "integration_list", {})
    rows = [r for r in (res or {}).get("platforms") or [] if isinstance(r, dict)]
    if not show_all:
        rows = [r for r in rows if r.get("connected")]
    if as_json or not sys.stdout.isatty():
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    if not rows:
        ui.dim("No apps connected yet. Connect one: aos connect <app>  (aos integrations --all lists them)")
        return 0
    t = ui.table("App", "Status", "Accounts", "Actions")
    for r in sorted(rows, key=lambda r: (not r.get("connected"), r.get("toolkit_slug") or "")):
        accts = ", ".join(x.get("label") or x.get("connection_id", "") for x in r.get("accounts") or [] if isinstance(x, dict))
        status = "[green]connected[/]" if r.get("connected") else "[dim]not connected[/]"
        t.add_row(f"[bold]{r.get('toolkit_slug') or r.get('toolkit_name')}[/]", status, accts or "[dim]—[/]",
                  str(r.get("tool_count") or ""))
    ui.console.print(t)
    ui.dim("Use one: aos call <app>    Connect another: aos connect <app>"
           + ("" if show_all else "    All apps: aos integrations --all"))
    return 0


LEGACY = {"list", "search", "show", "describe", "run", "call"}


def main(argv: list[str]) -> int:
    argv = list(argv)
    if argv and argv[0] in LEGACY:
        return _legacy(argv)
    p = argparse.ArgumentParser(prog="aos integrations", add_help=False)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    a, extra = p.parse_known_args(argv)
    if a.help:
        print(USAGE)
        return 0
    if extra:  # `aos integrations send email` (0.3.0 search) still works
        return _legacy(["search", *argv])
    return cmd_list(a.all, a.json)


# ---------------------------------------------------------------- 0.3.0 forms, unlisted

def _legacy(argv: list[str]) -> int:
    from . import call
    sub, rest = argv[0], argv[1:]
    as_json = "--json" in rest
    rest = [x for x in rest if x != "--json"]
    if sub == "list" and not rest:
        return cmd_list(False, as_json)
    if sub in ("list", "search"):
        return legacy_search(rest, as_json)
    if sub in ("show", "describe"):
        return call.main([*rest[:1], "--describe", *(["--json"] if as_json else [])])
    return call.main([*rest, *(["--json"] if as_json else [])])


def legacy_search(words: list[str], as_json: bool, limit: int = 15) -> int:
    """0.3.0 `aos integrations <words>` / old `aos tools`: search AgentOS and apps at once."""
    from . import call
    client = _client()
    q = " ".join(w for w in words if not w.startswith("--"))
    own = [{"action": r["action"], "source": "AgentOS", "kind": "agentos", "connected": True,
            "description": r["description"]} for r in call.agentos_actions(client, q)[:limit]]
    res = _call(client, "integration_search", {"q": q, "limit": min(limit, 50)})
    apps = [{"action": t["slug"], "source": t.get("toolkit_slug") or "app", "kind": "app",
             "connected": bool(t.get("connected")), "description": t.get("description") or ""}
            for t in ((res or {}).get("tools") or []) if isinstance(t, dict) and t.get("slug")] if isinstance(res, dict) else []
    rows = own[:5] + apps if apps else own
    if as_json or not sys.stdout.isatty():
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    t = ui.table("Action", "From", "What it does")
    for r in rows:
        t.add_row(f"[bold]{r['action']}[/]", r["source"], " ".join(r["description"].split())[:70])
    ui.console.print(t)
    ui.dim("Run one: aos call <app> <action> '{…}'  (AgentOS's own: aos call agentos <action>)")
    return 0


def legacy_tools(argv: list[str]) -> int:
    """Old flags of the 0.2 search command (hidden): same search."""
    p = argparse.ArgumentParser(prog="aos")
    p.add_argument("words", nargs="*")
    p.add_argument("--q")
    p.add_argument("--name")
    p.add_argument("--family")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.name:
        from . import call
        return call.main([a.name, "--describe", *(["--json"] if a.json else [])])
    words = ([a.q] if a.q else []) + a.words + ([a.family] if a.family else [])
    return legacy_search(words, a.json, a.limit)
