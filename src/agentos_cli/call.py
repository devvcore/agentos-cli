"""`aos call`: do something in AgentOS or in a connected app.

  aos call agentos                     what AgentOS itself can do (tasks, CRM, memory, files…)
  aos call agentos tasks               …search it
  aos call agentos tasks_list '{"status":"open"}'
  aos call gmail                       what Gmail can do (an app from `aos integrations`)
  aos call gmail send                  …search it
  aos call gmail send_email '{"recipient_email":"…","body":"…"}'
  aos call gmail send_email --describe what it does and its arguments

An action runs only when its name matches exactly; anything else is a search,
so a typo never runs the wrong thing. Arguments are a JSON object (inline,
--args-file, or stdin). Old forms still work: `aos call tasks_list '{…}'`,
`aos call GMAIL_SEND_EMAIL '{…}'`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from . import ui

AGENTOS = "agentos"
APP_SLUG = re.compile(r"^[A-Z][A-Z0-9_]+$")
MCP_SLUG = re.compile(r"^mcp_[0-9a-f]{6,}_")
PAGE = 50


def is_app_slug(name: str) -> bool:
    """A connected-app action's full name: GMAIL_SEND_EMAIL, or a connected MCP server's mcp_<id>_<tool>."""
    return bool(APP_SLUG.match(name) or MCP_SLUG.match(name))


def _client():
    from .work import connect
    return connect()


def _call(client, tool: str, args: dict) -> Any:
    from .work import call
    return call(client, tool, args)


def _json_out(a) -> bool:
    return getattr(a, "json", False) or not sys.stdout.isatty()


def _one_line(text, n=70) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def short_name(app: str, slug: str) -> str:
    """GMAIL_SEND_EMAIL → send_email (what people type after `aos call gmail`)."""
    pre = re.sub(r"[^A-Z0-9]", "_", app.upper()) + "_"
    return slug[len(pre):].lower() if slug.startswith(pre) else slug


# ---------------------------------------------------------------- looking things up

def agentos_actions(client, q: str | None = None) -> list[dict]:
    rows, cursor = [], None
    for _ in range(20):
        args = {"limit": 20, **({"q": q} if q else {}), **({"cursor": cursor} if cursor else {})}
        res = client.call("workspace_tools", **args) or {}
        rows += [{"action": t["name"], "area": t.get("family") or "", "description": t.get("description") or ""}
                 for t in res.get("tools") or []]
        cursor = res.get("next_cursor")
        if not cursor:
            break
    return _by_name(rows, q)


def _by_name(rows: list[dict], q: str | None) -> list[dict]:
    """Actions whose own name has a search word ("tasks" → tasks_list) before ones that only mention it."""
    if not q:
        return rows
    words = {w.lower() for w in q.split()}
    return sorted(rows, key=lambda r: not (words & set(r["action"].lower().split("_"))))


def agentos_action(client, name: str) -> dict | None:
    try:
        res = client.call("workspace_tools", name=name)
    except Exception:
        return None
    ok = isinstance(res, dict) and res.get("name") == name and res.get("found") is not False and "message" not in res
    return res if ok else None


def app_actions(client, app: str, q: str | None = None) -> list[dict]:
    rows, offset = [], 0
    for _ in range(40):
        res = _call(client, "integration_search", {"q": q or app, "toolkit": app, "limit": PAGE, "offset": offset})
        if not isinstance(res, dict):
            break
        for t in res.get("tools") or []:
            if isinstance(t, dict) and t.get("slug"):
                rows.append({"action": short_name(app, t["slug"]), "slug": t["slug"], "connected": bool(t.get("connected")),
                             "description": t.get("description") or t.get("name") or "", "schema": t})
        if not res.get("has_more") or res.get("next_offset") is None:
            break
        offset = res["next_offset"]
    return _by_name(rows, q if q and q != app else None)


def resolve_app_action(client, app: str, action: str) -> dict | None:
    """Exact match only: `send_email` or `GMAIL_SEND_EMAIL` (or an MCP tool's full name)."""
    want = action.upper() if not MCP_SLUG.match(action) else action
    full = want if is_app_slug(want) and want.startswith(re.sub(r"[^A-Z0-9]", "_", app.upper())) else \
        re.sub(r"[^A-Z0-9]", "_", app.upper()) + "_" + re.sub(r"[^A-Za-z0-9]", "_", action).upper()
    for r in app_actions(client, app, action.replace("_", " ")):
        if r["slug"] in (full, action) or r["slug"].lower() == action.lower():
            return r
    return None


def toolkits(client) -> set[str]:
    res = _call(client, "integration_list", {})
    return {p.get("toolkit_slug") for p in (res or {}).get("platforms") or [] if isinstance(p, dict)}


# ---------------------------------------------------------------- output

def _show_list(a, app: str, rows: list[dict], q: str | None) -> int:
    if _json_out(a):
        print(json.dumps([{k: v for k, v in r.items() if k != "schema"} for r in rows], ensure_ascii=False))
        return 0
    label = "AgentOS" if app == AGENTOS else app
    if not rows:
        ui.dim(f"{label} has nothing matching {q!r}." if q else f"Nothing found for {label}. Is it connected? `aos integrations`")
        return 1 if not q else 0
    title = f"{label}: {len(rows)} action{'s' * (len(rows) != 1)}" + (f" matching {q!r}" if q else "")
    if app == AGENTOS:
        t = ui.table("Action", "Area", "What it does", title=title)
        for r in sorted(rows, key=lambda r: (r["area"], r["action"])) if not q else rows:
            t.add_row(f"[bold]{r['action']}[/]", f"[dim]{r['area']}[/]", _one_line(r["description"]))
    else:
        t = ui.table("Action", "What it does", title=title)
        for r in rows:
            t.add_row(f"[bold]{r['action']}[/]", _one_line(r["description"], 80))
        if rows and not rows[0]["connected"]:
            ui.warn(f"{app} isn't connected yet: `aos connect {app}`")
    ui.console.print(t)
    example = rows[0]["action"]
    ui.dim(f"Run one: aos call {app} {example} '{{…}}'    Its arguments: aos call {app} {example} --describe")
    return 0


def _describe(a, app: str, info: dict) -> int:
    if _json_out(a):
        print(json.dumps(info, ensure_ascii=False))
        return 0
    name = info.get("name") or info.get("slug")
    ui.console.print(f"[bold]aos call {app} {a.action}[/]  [dim]{'AgentOS' if app == AGENTOS else name}[/]")
    if info.get("description"):
        ui.console.print(" ".join(str(info["description"]).split()), markup=False)
    schema = info.get("inputSchema") or {}
    props, req = schema.get("properties") or {}, set(schema.get("required") or info.get("required") or [])
    if props:
        t = ui.table("Argument", "Type", "", "Description")
        for k, v in props.items():
            t.add_row(f"[bold]{k}[/]", str(v.get("type", "")), "[yellow]required[/]" if k in req else "",
                      _one_line(v.get("description"), 70))
        ui.console.print(t)
    else:
        ui.dim(f"required: {', '.join(sorted(req)) or '—'}   optional: {', '.join(info.get('optional') or []) or '—'}")
    return 0


def _run(a, client, kind: str, name: str, args: dict) -> int:
    from .work import outcome
    if kind == "app":
        call_args = {"tool_slug": name, "arguments": args, **({"connection_id": a.account} if a.account else {})}
        res = _call(client, "integration_call", call_args)
    else:
        res = _call(client, name, args)
    print(json.dumps(res, ensure_ascii=False, default=str) if _json_out(a)
          else json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return outcome(res)


# ---------------------------------------------------------------- main

USAGE = """usage: aos call <app> [action] [json]

Do something in AgentOS or one of your connected apps.

  aos call agentos                 what AgentOS itself can do: tasks, CRM, memory, files…
  aos call agentos tasks           search it
  aos call agentos tasks_list '{"status":"open"}'
  aos call gmail                   what Gmail can do (your apps: aos integrations)
  aos call gmail send              search it
  aos call gmail send_email '{"recipient_email":"ada@example.com","subject":"Hi","body":"…"}'
  aos call gmail send_email --describe     what it does and its arguments

An action runs only when its name matches exactly; anything else is a search.
Arguments: a JSON object inline, with --args-file <file>, or on stdin.

options:
  --describe        show what the action does and its arguments; don't run it
  --args-file FILE  read the JSON arguments from FILE (- for stdin)
  --account ID      for an app with several accounts: which one
  --json            JSON output (the default when piped)

Exit codes: 0 done · 1 failed · 2 waiting for a person's approval (not done yet)"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aos call", add_help=False)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("pos", nargs="*")
    p.add_argument("--describe", action="store_true")
    p.add_argument("--args-file")
    p.add_argument("--account")
    p.add_argument("--json", action="store_true")
    return p


def _is_json(s: str | None) -> bool:
    return s is not None and (s.lstrip().startswith("{") or s == "-")


def main(argv: list[str]) -> int:
    from .work import parse_args_input
    a = build_parser().parse_args(argv)
    if a.help or not a.pos:
        print(USAGE)
        return 0
    pos = list(a.pos)
    first, rest = pos[0], pos[1:]
    client = _client()

    # Old single-name forms: aos call GMAIL_SEND_EMAIL '{…}' / aos call tasks_list '{…}'
    if first != AGENTOS and (is_app_slug(first) or (not rest or _is_json(rest[0]))):
        raw = rest[0] if rest else None
        if is_app_slug(first):
            a.action = first
            if a.describe:
                res = _call(client, "integration_search", {"q": first, "tool_slug": first})
                rows = (res.get("tools") or []) if isinstance(res, dict) else []
                return _describe(a, rows[0].get("toolkit_slug") or "app", rows[0]) if rows else 1
            return _run(a, client, "app", first, parse_args_input(raw, a.args_file))
        if "_" in first and raw is not None and not a.describe:
            return _run(a, client, "agentos", first, parse_args_input(raw, a.args_file))
        if "_" in first and first not in toolkits(client):
            a.action = first
            info = agentos_action(client, first)
            if info is None:
                ui.fail(f"No action {first!r}. Try `aos call agentos {first.split('_')[0]}` to search.")
                return 1
            if a.describe:
                return _describe(a, AGENTOS, info)
            return _run(a, client, "agentos", first, parse_args_input(raw, a.args_file))

    app = first
    words = [w for w in rest if not _is_json(w)]
    raw = next((w for w in rest if _is_json(w)), None)
    if not words:
        rows = agentos_actions(client) if app == AGENTOS else app_actions(client, app)
        return _show_list(a, app, rows, None)
    a.action = words[0]
    if len(words) == 1:
        if app == AGENTOS:
            info = agentos_action(client, words[0])
            if info:
                return _describe(a, app, info) if a.describe else \
                    _run(a, client, "agentos", words[0], parse_args_input(raw, a.args_file))
        else:
            hit = resolve_app_action(client, app, words[0])
            if hit and a.describe:
                full = _call(client, "integration_search", {"q": hit["slug"], "tool_slug": hit["slug"]})
                rows = (full.get("tools") or []) if isinstance(full, dict) else []
                return _describe(a, app, rows[0] if rows else hit["schema"])
            if hit:
                return _describe(a, app, hit["schema"]) if a.describe else \
                    _run(a, client, "app", hit["slug"], parse_args_input(raw, a.args_file))
    if raw is not None or a.args_file:
        ui.fail(f"{' '.join(words)!r} isn't an action of {'AgentOS' if app == AGENTOS else app}; nothing was run.")
        q = " ".join(words).replace("_", " ")
        rows = agentos_actions(client, q) if app == AGENTOS else app_actions(client, app, q)
        _show_list(a, app, rows, q)
        return 1
    q = " ".join(words).replace("_", " ")
    rows = agentos_actions(client, q) if app == AGENTOS else app_actions(client, app, q)
    return _show_list(a, app, rows, q)
