"""Sign in to AgentOS with OAuth 2.1 (PKCE, loopback redirect) and keep the token safe.

AgentOS is its own OAuth server: DCR at /api/oauth/register,
/authorize with sign-up for new people, /token with rotating refresh tokens.

Where the token lives: the OS keychain (macOS Keychain via `keyring`). If no keychain
is available, ~/.agentos/credentials.json with mode 0600. Tokens are never printed.

Resolution order for every command: $AGENTOS_PAT, then the signed-in login
(refreshed when it is about to expire), then the bearer header of an `agentos`
server in ~/.claude.json (what the Claude Code plugin always used).
"""
from __future__ import annotations

import base64
import hashlib
import html
import http.server
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Callable

from . import paths

SERVICE = "ai.agentos.cli"
CLIENT_NAME = "AgentOS CLI"
DEFAULT_SCOPES = ("workspace:read", "workspace:write")
SCOPE_NAMES = ("workspace:read", "workspace:write", "workspace:outbound", "workspace:compute", "workspace:secrets")
LOOPBACK_PORTS = (53682, 53683, 53684, 53685)
LOGIN_TIMEOUT_S = 600


class NotSignedIn(RuntimeError):
    pass


class LoginError(RuntimeError):
    pass


# ---------------------------------------------------------------- storage

def _account() -> str:
    return paths.base_url()


def _keyring():
    if os.environ.get("AOS_NO_KEYRING"):
        return None
    try:
        import keyring
        from keyring.backends import fail
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def _cred_file():
    return paths.state_dir() / "credentials.json"


def load_login() -> dict[str, Any] | None:
    kr = _keyring()
    if kr:
        try:
            raw = kr.get_password(SERVICE, _account())
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    data = paths.read_json(_cred_file(), {})
    return data.get(_account())


def save_login(data: dict[str, Any]) -> str:
    """Store the login; returns where it went ('keychain' or a file path)."""
    kr = _keyring()
    if kr:
        try:
            kr.set_password(SERVICE, _account(), json.dumps(data))
            return "keychain"
        except Exception:
            pass
    all_ = paths.read_json(_cred_file(), {})
    all_[_account()] = data
    paths.write_json(_cred_file(), all_, mode=0o600)
    return str(_cred_file())


def clear_login() -> bool:
    found = False
    kr = _keyring()
    if kr:
        try:
            if kr.get_password(SERVICE, _account()):
                kr.delete_password(SERVICE, _account())
                found = True
        except Exception:
            pass
    all_ = paths.read_json(_cred_file(), {})
    if all_.pop(_account(), None) is not None:
        paths.write_json(_cred_file(), all_, mode=0o600)
        found = True
    return found


# ---------------------------------------------------------------- http

def _http(method: str, url: str, *, form: dict | None = None, body: dict | None = None,
          timeout: float = 20.0) -> tuple[int, dict]:
    data, headers = None, {"Accept": "application/json", "User-Agent": "aos-cli"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}


def discover(http=_http) -> dict:
    status, meta = http("GET", paths.base_url() + "/.well-known/oauth-authorization-server")
    if status != 200 or "authorization_endpoint" not in meta:
        raise LoginError("AgentOS sign-in isn't reachable right now (no OAuth metadata).")
    return meta


def _client_for(meta: dict, redirect_uri: str, http=_http) -> str:
    """Reuse the client registered for this redirect URI; register one if there is none."""
    state = paths.read_json(paths.cli_state_path(), {})
    clients = state.setdefault("oauth_clients", {}).setdefault(paths.base_url(), {})
    if redirect_uri in clients:
        return clients[redirect_uri]
    status, reg = http("POST", meta["registration_endpoint"], body={
        "client_name": CLIENT_NAME, "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none"})
    if status not in (200, 201) or not reg.get("client_id"):
        raise LoginError("AgentOS refused to register the CLI as a sign-in client.")
    clients[redirect_uri] = reg["client_id"]
    paths.write_json(paths.cli_state_path(), state)
    return reg["client_id"]


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


_DONE_PAGE = """<!doctype html><meta charset=utf-8><title>AgentOS</title>
<body style="font:15px -apple-system,system-ui,sans-serif;display:grid;place-items:center;height:90vh;color:#111">
<div style="max-width:420px;text-align:center"><div style="font-size:28px">{icon}</div>
<h2 style="font-weight:600;margin:.4em 0">{title}</h2><p style="color:#666">{text}</p></div>"""


class _Callback(http.server.BaseHTTPRequestHandler):
    result: dict = {}
    event: threading.Event

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if urllib.parse.urlparse(self.path).path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = {k: v[0] for k, v in q.items()}
        ok = "code" in q
        page = _DONE_PAGE.format(
            icon="✓" if ok else "✕",
            title="You're signed in" if ok else "Sign-in didn't finish",
            text="You can close this tab and go back to your terminal." if ok
            else html.escape(q.get("error_description", q.get("error", ["Try `aos login` again."]))[0]))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())
        type(self).event.set()

    def log_message(self, *a):  # keep the terminal clean
        pass


def _listen() -> tuple[http.server.HTTPServer, int]:
    for port in LOOPBACK_PORTS:
        try:
            return http.server.HTTPServer(("127.0.0.1", port), _Callback), port
        except OSError:
            continue
    raise LoginError("No free local port for the sign-in callback (tried %s)." % ", ".join(map(str, LOOPBACK_PORTS)))


def login(scopes=DEFAULT_SCOPES, *, open_browser: Callable[[str], Any] = webbrowser.open,
          say: Callable[[str], Any] = print, http=_http, timeout: float = LOGIN_TIMEOUT_S) -> dict:
    meta = discover(http)
    server, port = _listen()
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    try:
        client_id = _client_for(meta, redirect_uri, http)
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(24)
        url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
            "scope": " ".join(scopes), "resource": paths.mcp_url()})
        _Callback.result, _Callback.event = {}, threading.Event()
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        say(url)
        open_browser(url)
        if not _Callback.event.wait(timeout):
            raise LoginError("Timed out waiting for the browser. Run `aos login` again.")
    finally:
        server.shutdown()
        server.server_close()
    got = _Callback.result
    if got.get("state") != state:
        raise LoginError("The sign-in answer didn't match this request (state mismatch). Nothing was saved.")
    if "code" not in got:
        raise LoginError(got.get("error_description") or got.get("error") or "Sign-in was cancelled.")
    status, tok = http("POST", meta["token_endpoint"], form={
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier, "resource": paths.mcp_url()})
    if status != 200 or not tok.get("access_token"):
        raise LoginError("AgentOS didn't issue a token (%s)." % (tok.get("error") or status))
    data = _record(tok, client_id, meta["token_endpoint"])
    data["stored_in"] = save_login(data)
    return data


def _record(tok: dict, client_id: str, token_endpoint: str) -> dict:
    return {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + int(tok.get("expires_in") or 3600),
            "scope": tok.get("scope", ""), "user_id": tok.get("user_id"),
            "workspace_id": tok.get("workspace_id"), "client_id": client_id,
            "token_endpoint": token_endpoint, "signed_in_at": time.time()}


def refresh(data: dict, http=_http) -> dict:
    status, tok = http("POST", data["token_endpoint"], form={
        "grant_type": "refresh_token", "refresh_token": data.get("refresh_token") or "",
        "client_id": data["client_id"], "resource": paths.mcp_url()})
    if status != 200 or not tok.get("access_token"):
        raise NotSignedIn("Your AgentOS sign-in expired. Run `aos login`.")
    new = _record({**{"user_id": data.get("user_id"), "workspace_id": data.get("workspace_id"),
                      "scope": data.get("scope")}, **tok}, data["client_id"], data["token_endpoint"])
    new["signed_in_at"] = data.get("signed_in_at")
    save_login(new)
    return new


def _claude_json_token() -> str | None:
    d = paths.read_json(paths.home() / ".claude.json", {})
    cands = list((d.get("mcpServers") or {}).items())
    for pd in (d.get("projects") or {}).values():
        if isinstance(pd, dict):
            cands += list((pd.get("mcpServers") or {}).items())
    ordered = [c for c in cands if c[0] == "agentos"] + [c for c in cands if c[0] != "agentos" and "agentos" in c[0].lower()]
    for _, cfg in ordered:
        h = (cfg or {}).get("headers") or {} if isinstance(cfg, dict) else {}
        auth = h.get("Authorization") or h.get("authorization") or ""
        if auth:
            return auth.replace("Bearer ", "").strip()
    return None


REFRESH_MARGIN_S = 120


class _RefreshLock:
    """One refresh at a time across processes. Refresh tokens rotate, so two processes
    refreshing with the same one (a background tool calling `aos auth token` while you
    run a command) would leave one of them holding a dead token."""

    def __enter__(self):
        self.f = None
        try:
            import fcntl
            path = paths.state_dir() / "auth.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.f = open(path, "a")
            fcntl.flock(self.f, fcntl.LOCK_EX)
        except (ImportError, OSError):
            self.f = None
        return self

    def __exit__(self, *exc):
        if self.f:
            try:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_UN)
            finally:
                self.f.close()


def current_login() -> dict[str, Any] | None:
    """The `aos login` record with a fresh access token (refreshed when under 2 minutes are
    left). None when not signed in with `aos login`. Raises NotSignedIn if the refresh fails."""
    data = load_login()
    if not data:
        return None
    if data.get("expires_at", 0) - time.time() < REFRESH_MARGIN_S and data.get("refresh_token"):
        with _RefreshLock():
            data = load_login() or data  # another process may have refreshed while we waited
            if data.get("expires_at", 0) - time.time() < REFRESH_MARGIN_S:
                data = refresh(data)
    return data


def update_login(**fields: Any) -> dict[str, Any] | None:
    """Add fields to the saved login under the refresh lock, re-reading it first, so a token
    another process refreshed meanwhile is never overwritten with an older one."""
    with _RefreshLock():
        data = load_login()
        if not data:
            return None
        data.update({k: v for k, v in fields.items() if v is not None})
        save_login(data)
        return data


def token_source() -> tuple[str, str]:
    """(token, where it came from). Raises NotSignedIn."""
    env = os.environ.get("AGENTOS_PAT", "").strip()
    if env:
        return env, "AGENTOS_PAT"
    data = current_login()
    if data:
        return data["access_token"], "aos login"
    tok = _claude_json_token()
    if tok:
        return tok, "~/.claude.json"
    raise NotSignedIn("Not signed in to AgentOS. Run `aos login` (or set AGENTOS_PAT).")


def resolve_token() -> str:
    return token_source()[0]


def revoke(data: dict, http=_http) -> None:
    try:
        meta = discover(http)
        if meta.get("revocation_endpoint") and data.get("refresh_token"):
            http("POST", meta["revocation_endpoint"], form={
                "token": data["refresh_token"], "token_type_hint": "refresh_token", "client_id": data["client_id"]})
    except Exception:
        pass  # local sign-out still happens


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0
