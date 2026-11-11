"""Doing work: context, tools, call. Every AgentOS MCP tool is reachable as `aos call <tool>`."""
from __future__ import annotations

import json
import sys
from typing import Any

from . import mcp


def connect(timeout: float = 60.0):
    c = mcp.Client(timeout=timeout)
    c.initialize(client_name="aos-cli")
    return c


_TOP: set[str] | None = None


def top_level(client) -> set[str]:
    global _TOP
    if _TOP is None:
        _TOP = set(client.list_tools())
    return _TOP


def call(client, name: str, args: dict) -> Any:
    """Top-level MCP tools are called directly; everything else goes through workspace_call."""
    if name in top_level(client):
        return client.call(name, **args)
    return client.call("workspace_call", name=name, arguments=json.dumps(args))


def parse_args_input(raw: str | None, args_file: str | None) -> dict:
    if args_file:
        text = sys.stdin.read() if args_file == "-" else open(args_file, encoding="utf-8").read()
    elif raw is not None:
        text = raw
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = "{}"
    text = text.strip() or "{}"
    try:
        d = json.loads(text)
    except ValueError as e:
        raise SystemExit(f"aos: arguments aren't valid JSON ({e.msg}).")
    if not isinstance(d, dict):
        raise SystemExit("aos: arguments must be a JSON object.")
    return d


def outcome(res: Any) -> int:
    """Exit code for a tool result: 0 done, 1 failed, 2 waiting for approval."""
    if isinstance(res, dict):
        if res.get("queued_for_approval") is True or res.get("status") in ("awaiting_approval", "pending_approval"):
            return 2
        if res.get("ok") is False or res.get("error_code") or res.get("status") in ("failed", "denied", "expired"):
            return 1
    return 0
