import json
import time
import urllib.parse
import urllib.request

import pytest

from agentos_cli import auth, paths

META = {"authorization_endpoint": "https://t/api/oauth/authorize", "token_endpoint": "https://t/api/oauth/token",
        "registration_endpoint": "https://t/api/oauth/register", "revocation_endpoint": "https://t/api/oauth/revoke"}


class FakeServer:
    def __init__(self):
        self.calls = []
        self.registered = 0

    def __call__(self, method, url, form=None, body=None, timeout=20):
        self.calls.append((method, url, form, body))
        if url.endswith("/.well-known/oauth-authorization-server"):
            return 200, META
        if url == META["registration_endpoint"]:
            self.registered += 1
            assert body["token_endpoint_auth_method"] == "none"
            assert body["redirect_uris"][0].startswith("http://127.0.0.1:")
            return 201, {"client_id": f"cli_{self.registered}"}
        if url == META["token_endpoint"]:
            if form["grant_type"] == "authorization_code":
                assert form["code"] == "c0de" and len(form["code_verifier"]) > 40
                return 200, {"access_token": "agentos_pat_A", "refresh_token": "agentos_refresh_A", "expires_in": 3600,
                             "scope": "workspace:read workspace:write", "user_id": "u1", "workspace_id": "w1"}
            assert form["refresh_token"] == "agentos_refresh_A"
            return 200, {"access_token": "agentos_pat_B", "refresh_token": "agentos_refresh_B", "expires_in": 3600}
        if url == META["revocation_endpoint"]:
            return 200, {}
        raise AssertionError(url)


def browser(code="c0de", state_override=None):
    """Plays the browser: follows the authorize URL straight back to the loopback callback."""
    def open_(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        assert q["code_challenge_method"] == "S256" and q["resource"].endswith("/api/mcp")
        cb = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": state_override or q["state"]})
        import threading
        threading.Thread(target=lambda: urllib.request.urlopen(cb, timeout=5).read(), daemon=True).start()
    return open_


def test_login_saves_token_privately_and_reuses_client(home):
    srv = FakeServer()
    data = auth.login(open_browser=browser(), say=lambda *_: None, http=srv, timeout=5)
    assert data["access_token"] == "agentos_pat_A"
    f = home / ".agentos" / "credentials.json"
    assert oct(f.stat().st_mode & 0o777) == "0o600"
    assert auth.token_source() == ("agentos_pat_A", "aos login")
    auth.login(open_browser=browser(), say=lambda *_: None, http=srv, timeout=5)
    assert srv.registered == 1  # same port => same registered client


def test_state_mismatch_saves_nothing(home):
    with pytest.raises(auth.LoginError, match="state mismatch"):
        auth.login(open_browser=browser(state_override="evil"), say=lambda *_: None, http=FakeServer(), timeout=5)
    assert auth.load_login() is None


def test_refresh_when_about_to_expire(home, monkeypatch):
    srv = FakeServer()
    auth.login(open_browser=browser(), say=lambda *_: None, http=srv, timeout=5)
    d = auth.load_login()
    d["expires_at"] = time.time() + 30
    auth.save_login(d)
    monkeypatch.setattr(auth, "_http", srv)
    monkeypatch.setattr(auth.refresh, "__defaults__", (srv,))
    assert auth.token_source() == ("agentos_pat_B", "aos login")
    assert auth.load_login()["refresh_token"] == "agentos_refresh_B"


def test_token_order_env_then_login_then_claude_json(home, monkeypatch):
    with pytest.raises(auth.NotSignedIn):
        auth.token_source()
    p = home / ".claude.json"
    d = json.loads(p.read_text())
    d["mcpServers"]["agentos"] = {"type": "http", "url": "u", "headers": {"Authorization": "Bearer from_claude"}}
    p.write_text(json.dumps(d))
    assert auth.token_source() == ("from_claude", "~/.claude.json")
    auth.save_login({"access_token": "from_login", "expires_at": time.time() + 3600})
    assert auth.token_source()[0] == "from_login"
    monkeypatch.setenv("AGENTOS_PAT", "from_env")
    assert auth.token_source() == ("from_env", "AGENTOS_PAT")


def test_logout_clears(home):
    auth.save_login({"access_token": "x", "expires_at": time.time() + 3600})
    assert auth.clear_login() is True
    assert auth.load_login() is None
