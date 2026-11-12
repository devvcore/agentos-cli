"""`aos secrets`: keys stored in AgentOS, used from this computer.

  aos secrets list                   names and descriptions (never values)
  aos secrets get NAME               the value, on stdout
  aos secrets run [--only a,b] -- CMD  run CMD with secrets as environment variables
  aos secrets set NAME               store or rotate one (hidden prompt or stdin)
  aos secrets log [NAME]             who read which secret, when, from where

This is the model AWS Secrets Manager, Doppler, 1Password and Infisical use:
the person (or their agent) may read a secret they have access to, and every
read is logged on the server (who, which secret, when, which client), never
the value. AgentOS decides access: your personal secrets are yours; workspace
secrets go to members with access; bot-only and app-only credentials are never
returned. Reading needs the stored-credentials permission (`aos login --scope
secrets`).

The server reads secrets with `secrets_read` (batch, with the client and purpose
in the audit row) when it has it, and with `secrets_get` (one at a time, audited)
otherwise.

`aos http` and `aos connect` live here too (http: an API call where AgentOS adds
the key on its side; now an unlisted alias).
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor

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
                ui.dim(f"  Use it: aos secrets run -- <command>  (as ${env_name(res.get('name')) or res.get('name')})")
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


# ---------------------------------------------------------------- reading values

ENV_RESERVED = ("AGENTOS_", "BASH", "LD_", "DYLD_", "GIT_", "LC_", "E2B_")
ENV_RESERVED_EXACT = {"PATH", "HOME", "SHELL", "USER", "PWD", "TERM", "LANG", "TMPDIR", "LOGNAME", "PS1", "IFS"}


def env_name(name: str) -> str | None:
    """Same rule as the server's secrets_mgr.env_name: upper case, non A-Z/0-9 → '_',
    a leading digit gets '_' in front; shell/system names get no variable."""
    out = "".join(ch if ("A" <= ch <= "Z" or "0" <= ch <= "9") else "_" for ch in str(name or "").strip().upper())
    if not out.strip("_"):
        return None
    if out[0].isdigit():
        out = "_" + out
    if out in ENV_RESERVED_EXACT or out.startswith(ENV_RESERVED):
        return None
    return out


def _client_label() -> str:
    from . import __version__
    return f"aos-cli {__version__}"


def read_secrets(names: list[str], purpose: str) -> tuple[list[dict], list[dict]]:
    """([{name, env, value, scope?}], [{name, error}]). One audited server read per secret."""
    from .work import connect, call
    from .mcp import ToolError
    client = connect()
    try:
        res = call(client, "secrets_read", {"names": names, "purpose": purpose, "client": _client_label()})
    except ToolError as e:
        if "workspace tool" not in str(e) and "unknown" not in str(e).lower():
            if "missing_permission" in str(e).lower() or "workspace:secrets" in str(e):
                raise SystemExit("aos: " + NEEDS_SCOPE)
            raise
        res = None  # this server has no secrets_read yet: one secrets_get per name
    except RuntimeError as e:
        if "missing_permission" in str(e).lower() or "workspace:secrets" in str(e):
            raise SystemExit("aos: " + NEEDS_SCOPE)
        raise
    if isinstance(res, dict) and "secrets" in res:
        got = [{"name": r["name"], "env": r.get("env") or env_name(r["name"]), "value": r["value"],
                "scope": r.get("scope")} for r in res["secrets"] if isinstance(r, dict) and "value" in r]
        bad = [{"name": n, "error": "not found"} for n in res.get("missing") or []] + \
              [{"name": n, "error": "you don't have access"} for n in res.get("denied") or []]
        return got, bad

    def one(n):
        try:
            r = call(client, "secrets_get", {"name": n})
        except RuntimeError as e:
            msg = str(e)
            if "missing_permission" in msg.lower() or "workspace:secrets" in msg:
                raise SystemExit("aos: " + NEEDS_SCOPE)
            return {"name": n, "error": msg.splitlines()[0][:120]}
        if isinstance(r, dict) and "value" in r:
            return {"name": n, "env": env_name(n), "value": r["value"]}
        return {"name": n, "error": (r or {}).get("error") if isinstance(r, dict) else "not found"}
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(one, names))
    return [r for r in out if "value" in r], [r for r in out if "error" in r]


def cmd_get(a) -> int:
    got, bad = read_secrets([a.name], "get")
    if bad or not got:
        ui.fail(f"{a.name}: {(bad[0]['error'] if bad else 'not found')}")
        return 1
    value = got[0]["value"]
    if a.json:
        print(json.dumps({"name": a.name, "env": got[0]["env"], "value": value}))
    elif sys.stdout.isatty():
        print(value)
    else:
        sys.stdout.write(value)  # piped: exactly the value, no newline
    return 0


def _all_names() -> list[str]:
    res = _call("secrets_list", {})
    rows = res.get("secrets", res) if isinstance(res, dict) else res
    return [r["name"] for r in rows or [] if isinstance(r, dict) and r.get("name")]


MASK = "<concealed by aos>"


def _pump(src, dst, values: list[bytes]):
    for line in iter(src.readline, b""):
        for v in values:
            if v and v in line:
                line = line.replace(v, MASK.encode())
        dst.write(line)
        dst.flush()
    src.close()


def cmd_run(a) -> int:
    cmd = list(a.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise SystemExit("aos: say what to run, after --: aos secrets run -- npm start")
    names = [n.strip() for n in (a.only or "").split(",") if n.strip()] or _all_names()
    if not names:
        ui.warn("No secrets to add; running it as is.")
    got, bad = read_secrets(names, "run") if names else ([], [])
    for b in bad:
        ui.err.print(f"[yellow]![/] {b['name']}: {b['error']}; not set")
    env = dict(os.environ)
    seen = {}
    for r in got:
        var = r["env"]
        if not var:
            ui.err.print(f"[yellow]![/] {r['name']}: its name can't be an environment variable; not set")
            continue
        if var in seen:
            ui.err.print(f"[yellow]![/] {r['name']} and {seen[var]} both become ${var}; kept {seen[var]}")
            continue
        seen[var] = r["name"]
        env[var] = r["value"]
    if a.verbose:
        ui.err.print("[dim]aos: set " + (", ".join(f"${v}" for v in seen) or "nothing") + "[/]")
    # Hide values in the program's output when it goes somewhere other than this
    # terminal (an agent, a log, a pipe): that's where they leak. A person at a
    # terminal gets the program untouched, so interactive tools keep working.
    mask = a.mask if a.mask is not None else not sys.stdout.isatty()
    values = sorted({r["value"].encode() for r in got if len(r["value"]) >= 4}, key=len, reverse=True)
    try:
        if not mask or not values:
            return subprocess.call(cmd, env=env)
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ts = [threading.Thread(target=_pump, args=(p.stdout, sys.stdout.buffer, values), daemon=True),
              threading.Thread(target=_pump, args=(p.stderr, sys.stderr.buffer, values), daemon=True)]
        for t in ts:
            t.start()
        rc = p.wait()
        for t in ts:
            t.join()
        return rc
    except FileNotFoundError:
        ui.fail(f"{cmd[0]}: command not found")
        return 127
    except KeyboardInterrupt:
        return 130


def cmd_log(a) -> int:
    args = {"limit": a.limit, **({"name": a.name} if a.name else {})}
    try:
        res = _call("secrets_access_log", args)
    except RuntimeError as e:
        if "workspace tool" in str(e) or "unknown" in str(e).lower():
            ui.warn("This AgentOS server doesn't show the read log yet. It's being added; reads are already recorded.")
            return 1
        raise
    rows = (res or {}).get("access") if isinstance(res, dict) else res
    if a.json or not sys.stdout.isatty():
        print(json.dumps(res, default=str))
        return 0
    if not rows:
        ui.dim("No reads recorded." + (f" ({a.name})" if a.name else ""))
        return 0
    t = ui.table("When", "Secret", "Who", "From", "Why")
    for r in rows:
        who = r.get("user") or r.get("user_id") or ""
        if r.get("agent") or r.get("agent_id"):
            who += f" via {r.get('agent') or r.get('agent_id')}"
        t.add_row(str(r.get("at") or r.get("created_at") or "")[:19].replace("T", " "), f"[bold]{r.get('name') or r.get('secret_name') or ''}[/]",
                  who, str(r.get("client") or r.get("tool_slug") or ""), str(r.get("purpose") or ""))
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


SECRETS_USAGE = """usage: aos secrets <command>

Keys stored in AgentOS (API keys, tokens, passwords), used from this computer.

  aos secrets list                     your secrets: names and descriptions, never values
  aos secrets get stripe_api_key       print the value
  aos secrets run -- npm start         run a command with your secrets as environment
                                       variables (stripe_api_key → $STRIPE_API_KEY)
  aos secrets run --only stripe_api_key,db_url -- python report.py
  aos secrets set stripe_api_key       store or replace one (hidden prompt, or stdin)
  aos secrets log [name]               who read which secret, when, and from where

Every read is recorded in AgentOS: who, which secret, when, from which app or
agent. Never the value. Your personal secrets are only yours; workspace secrets
go to members with access; bots' own credentials are never handed out.

Reading values needs one extra permission, once: aos login --scope secrets

options for run:
  --only a,b        just these secrets (default: all you can use)
  --mask/--no-mask  hide secret values in the command's output (default: hidden
                    when output isn't your terminal, e.g. when an agent runs it)
  -v                say which variables were set
options for set:
  -d TEXT           what it's for     --from-env VAR / --from-file PATH   read it from there"""


def register(sub) -> None:
    import argparse as _ap
    s = sub.add_parser("secrets", add_help=False)
    s.add_argument("-h", "--help", action="store_true", dest="secrets_help")
    ss = s.add_subparsers(dest="secrets_cmd")
    s.set_defaults(fn=lambda a: print(SECRETS_USAGE) or 0)
    x = ss.add_parser("get")
    x.add_argument("name")
    x.add_argument("--json", action="store_true")
    x.set_defaults(fn=cmd_get)
    x = ss.add_parser("run")
    x.add_argument("--only")
    x.add_argument("--mask", dest="mask", action="store_true", default=None)
    x.add_argument("--no-mask", dest="mask", action="store_false")
    x.add_argument("-v", "--verbose", action="store_true")
    x.add_argument("cmd", nargs=_ap.REMAINDER)
    x.set_defaults(fn=cmd_run)
    x = ss.add_parser("log")
    x.add_argument("name", nargs="?")
    x.add_argument("--limit", type=int, default=50)
    x.add_argument("--json", action="store_true")
    x.set_defaults(fn=cmd_log)
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

    s = sub.add_parser("http")  # unlisted: AgentOS adds the key on its side
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
