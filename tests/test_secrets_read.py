import json
import sys

import pytest

from agentos_cli import cli, mcp, secrets_cmd, work

VALUES = {"stripe_api_key": "sk_example_value_1234", "db_url": "postgres://u:pw-example@h/db"}


class Fake:
    """has_batch=False is today's server: no secrets_read, one secrets_get per name."""
    server_info = {}

    def __init__(self, has_batch):
        self.has_batch, self.calls = has_batch, []

    def list_tools(self):
        return ["workspace_call"]

    def call(self, tool, **kw):
        name, args = kw["name"], json.loads(kw["arguments"])
        self.calls.append((name, args))
        if name == "secrets_read":
            if not self.has_batch:
                raise mcp.ToolError("name must identify a workspace tool from workspace_tools")
            return {"secrets": [{"name": n, "env": n.upper(), "value": VALUES[n], "scope": "workspace"}
                                for n in args["names"] if n in VALUES],
                    "missing": [n for n in args["names"] if n not in VALUES]}
        if name == "secrets_get":
            if args["name"] not in VALUES:
                raise mcp.ToolError("Secret not found")
            return {"name": args["name"], "value": VALUES[args["name"]]}
        if name == "secrets_list":
            return {"secrets": [{"name": n} for n in VALUES]}
        raise AssertionError(name)


@pytest.fixture(params=[True, False], ids=["secrets_read", "fallback"])
def fake(request, monkeypatch, home):
    f = Fake(request.param)
    monkeypatch.setattr(work, "_TOP", None)
    monkeypatch.setattr(work, "connect", lambda timeout=60, info=False: f)
    return f


def test_get_prints_exactly_the_value_when_piped(fake, capsys):
    assert cli.main(["secrets", "get", "stripe_api_key"]) == 0
    assert capsys.readouterr().out == VALUES["stripe_api_key"]
    purpose = [a.get("purpose") for n, a in fake.calls if n == "secrets_read"]
    assert purpose in ([], ["get"])


def test_get_missing_is_an_error(fake, capsys):
    assert cli.main(["secrets", "get", "nope"]) == 1


def test_run_injects_env_and_masks_piped_output(fake, capfdbinary):
    code = "import os,sys; print(os.environ['STRIPE_API_KEY']); print(os.environ['DB_URL'], file=sys.stderr)"
    assert cli.main(["secrets", "run", "--", sys.executable, "-c", code]) == 0
    out = capfdbinary.readouterr()
    assert b"<concealed by aos>" in out.out and VALUES["stripe_api_key"].encode() not in out.out
    assert VALUES["db_url"].encode() not in out.err


def test_run_only_and_no_mask(fake, capfdbinary):
    code = "import os; print(os.environ.get('STRIPE_API_KEY'), os.environ.get('DB_URL'))"
    assert cli.main(["secrets", "run", "--only", "stripe_api_key", "--no-mask", "--", sys.executable, "-c", code]) == 0
    assert capfdbinary.readouterr().out.strip() == f"{VALUES['stripe_api_key']} None".encode()


def test_run_passes_the_exit_code(fake):
    assert cli.main(["secrets", "run", "--", sys.executable, "-c", "raise SystemExit(7)"]) == 7


def test_scope_error_says_how_to_fix(home, monkeypatch):
    class NoScope(Fake):
        def call(self, tool, **kw):
            raise mcp.ToolError("missing_permission: workspace:secrets")
    f = NoScope(False)
    monkeypatch.setattr(work, "_TOP", None)
    monkeypatch.setattr(work, "connect", lambda timeout=60, info=False: f)
    with pytest.raises(SystemExit, match="aos login --scope secrets"):
        cli.main(["secrets", "get", "stripe_api_key"])


def test_env_names_match_the_server_rule():
    assert secrets_cmd.env_name("stripe-api.key") == "STRIPE_API_KEY"
    assert secrets_cmd.env_name("2fa seed") == "_2FA_SEED"
    assert secrets_cmd.env_name("path") is None and secrets_cmd.env_name("git_token") is None


def test_secrets_help_has_no_http(capsys):
    assert cli.main(["secrets"]) == 0
    out = capsys.readouterr().out
    assert "aos secrets run" in out and "aos http" not in out
