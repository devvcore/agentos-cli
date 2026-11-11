"""Credentials through AgentOS: store keys, call APIs with them, connect apps.

* `aos secrets set NAME` stores a key in the workspace's encrypted store
  (secrets_set). The value never goes on the command line: it comes from a
  hidden prompt, stdin, --from-env or --from-file.
* `aos secrets list` shows names and descriptions, never values.
* `aos http NAME URL` calls an API with a stored key through AgentOS
  (http_request): the key is added on the server and never reaches this computer.
* `aos connect gmail` opens a link to connect an app with its own sign-in
  (connect_link), for services that use OAuth instead of a key.

secrets_* and http_request need the "stored credentials" permission
(workspace:secrets), which `aos login` asks for only with --scope secrets.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import webbrowser

from . import ui

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
NEEDS_SCOPE = "This needs the stored-credentials permission. Run `aos login --scope secrets` and tick it on the consent screen."


def _call(name: str, args: dict):
    from .work import connect, call
    try:
        return call(connect(), name, args)
    except RuntimeError as e:
        text = str(e).lower()
        if "missing_permission" in text or "workspace:secrets" in text or "scope" in text:
            raise SystemExit("aos: " + NEEDS_SCOPE)
        raise


def _scope_error(res) -> bool:
    if isinstance(res, dict):
        blob = json.dumps(res).lower()
        return res.get("ok") is False and ("missing_permission" in blob or "workspace:secrets" in blob)
    return False


def read_value(a) -> str:
    if a.from_env:
        v = os.environ.get(a.from_env)
        if v is None:
            raise SystemExit(f"aos: ${a.from_env} isn't set.")
        return v
    if a.from_file:
        with open(os.path.expanduser(a.from_file), encoding="utf-8") as f:
            return f.read().rstrip("\n")
    if sys.stdin.isatty():
        v = getpass.getpass(f"Value for {a.name} (hidden): ")
        if not v:
            raise SystemExit("aos: nothing entered; nothing stored.")
        return v
    return sys.stdin.read().rstrip("\n")


def cmd_set(a) -> int:
    if not NAME_RE.match(a.name):
        raise SystemExit("aos: use a snake_case name, like stripe_api_key.")
    value = read_value(a)
    if not value.strip():
        raise SystemExit("aos: the value is empty; nothing stored.")
    args = {"name": a.name, "value": value}
    if a.description:
        args["description"] = a.description
    if a.host:
        args["hosts"] = a.host
    if a.auth:
        args["auth"] = a.auth
    try:
        res = _call("secrets_set", args)
    except RuntimeError as e:
        msg = str(e).replace(value, "[redacted]")[:200]
        raise SystemExit(f"aos: not stored ({msg}).")
    del value, args
    if _scope_error(res):
        raise SystemExit("aos: " + NEEDS_SCOPE)
    if isinstance(res, dict) and res.get("ok"):
        extra = f" · sent only to {', '.join(res['hosts'])} as {res.get('auth')}" if res.get("hosts") else ""
        if a.json or not sys.stdout.isatty():
            print(json.dumps({k: v for k, v in res.items() if k != "value"}))
        else:
            ui.ok(f"Stored {res.get('name')} (…{res.get('last_4', '')}) in your workspace{extra}.")
            if not res.get("hosts"):
                ui.dim(f"  Tip: add --host api.example.com so `aos http {res.get('name')} …` and your bots can use it.")
        return 0
    ui.fail(f"Not stored: {(res or {}).get('error') if isinstance(res, dict) else res}")
    return 1


def cmd_list(a) -> int:
    res = _call("secrets_list", {})
    if _scope_error(res):
        raise SystemExit("aos: " + NEEDS_SCOPE)
    rows = res.get("secrets", res) if isinstance(res, dict) else res
    if a.json or not sys.stdout.isatty() or not isinstance(rows, list):
        print(json.dumps(res, default=str))
        return 0
    t = ui.table("Name", "Description", "Last 4")
    for s in rows:
        if isinstance(s, dict):
            t.add_row(f"[bold]{s.get('name', '')}[/]", f"[dim]{(s.get('description') or '')[:70]}[/]",
                      str(s.get("last_4") or ""))
    ui.console.print(t)
    return 0


def cmd_http(a) -> int:
    args = {"secret": a.secret, "url": a.url, "method": a.method.upper()}
    if a.json_body:
        try:
            args["json"] = json.loads(a.json_body)
        except ValueError as e:
            raise SystemExit(f"aos: --json isn't valid JSON ({e.msg}).")
    if a.data:
        args["body"] = a.data
    if a.header:
        hdr = {}
        for h in a.header:
            k, _, v = h.partition(":")
            if not v:
                raise SystemExit(f"aos: header {h!r} should look like 'Name: value'.")
            hdr[k.strip()] = v.strip()
        args["headers"] = hdr
    if a.query:
        args["query"] = dict(q.split("=", 1) for q in a.query)
    if a.all_pages:
        args["all_pages"] = True
    res = _call("http_request", args)
    if _scope_error(res):
        raise SystemExit("aos: " + NEEDS_SCOPE)
    print(json.dumps(res, ensure_ascii=False, default=str) if (a.raw_json or not sys.stdout.isatty())
          else json.dumps(res, ensure_ascii=False, indent=2, default=str))
    from .work import outcome
    return outcome(res)


def cmd_connect(a) -> int:
    args = {"service": a.service}
    if a.purpose:
        args["purpose"] = a.purpose
    if a.for_person:
        args["for_person"] = a.for_person
    res = _call("connect_link", args)
    link = res.get("link") if isinstance(res, dict) else None
    if not link:
        print(json.dumps(res, default=str))
        return 1
    if not sys.stdout.isatty():
        print(json.dumps(res))
        return 0
    ui.console.print(f"Connect [bold]{a.service}[/] to AgentOS. Approve it with the provider in your browser.")
    ui.console.print(f"  [link={link}]{link}[/link]")
    if not a.no_browser:
        webbrowser.open(link)
    ui.dim("When it's done, `aos integrations list` shows the account. The key stays in AgentOS.")
    return 0


def register(sub) -> None:
    s = sub.add_parser("secrets", help="store keys in your workspace (values never shown)")
    ss = s.add_subparsers(dest="secrets_cmd", required=True)
    x = ss.add_parser("set", help="store or rotate a key; the value comes from a hidden prompt or stdin")
    x.add_argument("name")
    x.add_argument("-d", "--description", help="what it unlocks, e.g. 'Stripe live key for billing reports'")
    x.add_argument("--host", action="append", help="host it may be sent to (repeatable), e.g. api.stripe.com")
    x.add_argument("--auth", help="bearer (default), header:<Name>, query:<param>, basic or basic:<user>")
    x.add_argument("--from-env", metavar="VAR")
    x.add_argument("--from-file", metavar="PATH")
    x.add_argument("--json", action="store_true")
    x.set_defaults(fn=cmd_set)
    x = ss.add_parser("list", help="names and descriptions, never values")
    x.add_argument("--json", action="store_true")
    x.set_defaults(fn=cmd_list)

    s = sub.add_parser("http", help="call an API with a stored key; AgentOS adds the key")
    s.add_argument("secret", help="stored key name (see `aos secrets list`)")
    s.add_argument("url")
    s.add_argument("-X", "--method", default="GET")
    s.add_argument("--json", dest="json_body", metavar="JSON", help="JSON request body")
    s.add_argument("-d", "--data", help="raw request body")
    s.add_argument("-H", "--header", action="append", help="'Name: value' (repeatable)")
    s.add_argument("-q", "--query", action="append", help="key=value (repeatable)")
    s.add_argument("--all-pages", action="store_true")
    s.add_argument("--raw-json", action="store_true", help="one line of JSON")
    s.set_defaults(fn=cmd_http)

    s = sub.add_parser("connect", help="connect an app (Gmail, HubSpot, Stripe…) with its own sign-in")
    s.add_argument("service")
    s.add_argument("--purpose")
    s.add_argument("--for", dest="for_person")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_connect)
