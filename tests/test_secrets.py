import io
import json
import sys

import pytest

from agentos_cli import cli, secrets_cmd, work


class Fake:
    server_info = {}

    def __init__(self, result):
        self.result, self.calls = result, []

    def initialize(self, client_name=""):
        pass

    def list_tools(self):
        return ["workspace_call"]

    def call(self, tool, **args):
        self.calls.append((tool, args))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def fake(monkeypatch, home):
    def make(result):
        f = Fake(result)
        monkeypatch.setattr(work, "_TOP", None)
        monkeypatch.setattr(work, "connect", lambda timeout=60: f)
        return f
    return make


def sent(f):
    tool, args = f.calls[-1]
    assert tool == "workspace_call"
    return args["name"], json.loads(args["arguments"])


def test_set_reads_value_from_stdin_never_argv(fake, monkeypatch, capsys):
    f = fake({"ok": True, "name": "stripe_api_key", "last_4": "4242"})
    monkeypatch.setattr(sys, "stdin", io.StringIO("example-not-a-key-4242\n"))
    rc = cli.main(["secrets", "set", "stripe_api_key", "-d", "Stripe test key", "--host", "api.stripe.com"])
    assert rc == 0
    name, args = sent(f)
    assert name == "secrets_set"
    assert args == {"name": "stripe_api_key", "value": "example-not-a-key-4242",
                    "description": "Stripe test key", "hosts": ["api.stripe.com"]}
    out = capsys.readouterr().out
    assert "example-not-a-key-4242" not in out and "4242" in out


def test_set_from_env_and_bad_name(fake, monkeypatch):
    f = fake({"ok": True, "name": "k_x", "last_4": "zzzz"})
    monkeypatch.setenv("MY_KEY", "zzzzzzzz")
    assert cli.main(["secrets", "set", "k_x", "--from-env", "MY_KEY"]) == 0
    assert sent(f)[1]["value"] == "zzzzzzzz"
    with pytest.raises(SystemExit, match="snake_case"):
        cli.main(["secrets", "set", "Bad-Name", "--from-env", "MY_KEY"])


def test_missing_scope_says_how_to_fix(fake, monkeypatch):
    fake(RuntimeError("missing_permission: workspace:secrets"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("v4lue-123456"))
    with pytest.raises(SystemExit, match="aos login --scope secrets"):
        cli.main(["secrets", "set", "k_y"])


def test_error_text_never_echoes_the_value(fake, monkeypatch):
    fake(RuntimeError("bad request: value=s3cr3t-value-xyz rejected"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("s3cr3t-value-xyz"))
    with pytest.raises(SystemExit) as e:
        cli.main(["secrets", "set", "k_z"])
    assert "s3cr3t-value-xyz" not in str(e.value) and "[redacted]" in str(e.value)


def test_http_builds_a_proxy_request(fake):
    f = fake({"status": 200, "json": {"data": []}})
    rc = cli.main(["http", "stripe_api_key", "https://api.stripe.com/v1/invoices", "-q", "limit=3",
                   "-H", "Stripe-Version: 2024-06-20"])
    assert rc == 0
    name, args = sent(f)
    assert name == "http_request"
    assert args == {"secret": "stripe_api_key", "url": "https://api.stripe.com/v1/invoices", "method": "GET",
                    "headers": {"Stripe-Version": "2024-06-20"}, "query": {"limit": "3"}}


def test_connect_returns_link(fake, capsys):
    f = fake({"link": "https://tryagentos.net/c/abc", "paste": "…"})
    assert cli.main(["connect", "gmail", "--no-browser"]) == 0
    assert sent(f) == ("connect_link", {"service": "gmail"})
    assert "tryagentos.net/c/abc" in capsys.readouterr().out
