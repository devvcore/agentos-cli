"""Doing work: context, tools, call. Every AgentOS MCP tool is reachable as `aos call <tool>`."""
from __future__ import annotations

import json
import sys
import time
from typing import Any

from . import mcp, paths


def connect(timeout: float = 60.0, info: bool = False):
    """No network here: the endpoint is stateless, so the first tool call is the first request."""
    return mcp.connect(timeout=timeout, info=info)


_TOP: set[str] | None = None
TOOLS_CACHE_TTL_S = 6 * 3600


def _cache_path():
    return paths.state_dir() / "cache" / "top-level-tools.json"


def top_level(client, refresh: bool = False) -> set[str]:
    """Names the server exposes directly (not through workspace_call). Cached on disk for 6 hours."""
    global _TOP
    if _TOP is not None and not refresh:
        return _TOP
    url = getattr(client, "url", None)
    cached = paths.read_json(_cache_path(), {}) if url else {}
    if (not refresh and url and cached.get("url") == url and isinstance(cached.get("names"), list)
            and time.time() - cached.get("at", 0) < TOOLS_CACHE_TTL_S):
        _TOP = set(cached["names"])
        return _TOP
    names = client.list_tools()
    _TOP = set(names)
    if not url:
        return _TOP
    try:
        paths.write_json(_cache_path(), {"url": url, "at": time.time(), "names": sorted(names)})
    except OSError:
        pass
    return _TOP


def call(client, name: str, args: dict) -> Any:
    """Top-level MCP tools are called directly; everything else goes through workspace_call.

    The top-level list comes from a cache; if workspace_call says it doesn't know the name,
    the list is refreshed once in case the server added it as a top-level tool since."""
    if name in top_level(client):
        return client.call(name, **args)
    try:
        return client.call("workspace_call", name=name, arguments=json.dumps(args))
    except mcp.ToolError as e:
        if "workspace tool" in str(e) and name in top_level(client, refresh=True):
            return client.call(name, **args)
        raise


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
