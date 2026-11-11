"""Minimal AgentOS MCP (Streamable HTTP) JSON-RPC client, shared with the Claude Code plugin
(plugins/claude-code-agentos/scripts/agentos_mcp.py); the token comes from agentos_cli.auth.

Token resolution order:
  1. env AGENTOS_PAT
  2. the Authorization header of the `agentos` server in ~/.claude.json
     (top-level mcpServers first, then any project's mcpServers)

The token is never printed or logged.
"""
from __future__ import annotations

import itertools
import json
import urllib.request

CLIENT_VERSION = "0.2.0"
CATALOG_REVISION_KEY = "net.tryagentos/catalogRevision"


from . import auth, paths

NoToken = auth.NotSignedIn


def resolve_token() -> str:
    return auth.resolve_token()


def resolve_url() -> str:
    return paths.mcp_url()


class Client:
    def __init__(self, timeout: float = 20.0, url: str | None = None, token: str | None = None):
        self.timeout = timeout
        self.url = url or resolve_url()
        self._token = token or resolve_token()
        self._ids = itertools.count(1)
        self._session_id = None
        self._protocol_version = None
        self.server_info = {}
        self.server_catalog_revision = None
        self.catalog_revision = None

    def _post(self, payload: dict):
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Authorization", "Bearer " + self._token)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "aos-cli")
        req.add_header("Accept", "application/json, text/event-stream")
        if self._session_id:
            req.add_header("Mcp-Session-Id", self._session_id)
        if self._protocol_version:
            req.add_header("MCP-Protocol-Version", self._protocol_version)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            body = r.read().decode()
            ctype = r.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            data = None
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
            return data
        return json.loads(body) if body.strip() else None

    def initialize(self, client_name: str = "aos-cli", version: str = CLIENT_VERSION):
        res = self._post({
            "jsonrpc": "2.0", "id": next(self._ids), "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": version},
            },
        })
        result = (res or {}).get("result", {})
        self._protocol_version = result.get("protocolVersion")
        self.server_info = result.get("serverInfo", {})
        self.server_catalog_revision = (result.get("_meta") or {}).get(CATALOG_REVISION_KEY)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def call(self, tool_name: str, **args):
        res = self._post({
            "jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        })
        if res is None:
            return None
        if "error" in res:
            raise RuntimeError(str(res["error"]))
        result = res.get("result", {})
        if result.get("isError"):
            raise RuntimeError(str(result.get("content")))
        if "structuredContent" in result:
            return result["structuredContent"]
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except Exception:
            return joined

    def list_tools(self):
        # Always discover anew, including on an existing connection. A server
        # revision is diagnostic; it cannot refresh Claude's separate tool cache.
        res = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/list", "params": {}})
        if res and "error" in res:
            raise RuntimeError(str(res["error"]))
        result = (res or {}).get("result", {})
        self.catalog_revision = (result.get("_meta") or {}).get(CATALOG_REVISION_KEY)
        return [t["name"] for t in result.get("tools", [])]


def connect(timeout: float = 20.0) -> Client:
    c = Client(timeout=timeout)
    c.initialize()
    return c


