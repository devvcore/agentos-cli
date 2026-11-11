"""Minimal AgentOS MCP (Streamable HTTP) JSON-RPC client; the token comes from agentos_cli.auth.

Speed: the AgentOS MCP endpoint is stateless (no Mcp-Session-Id), so a tool call needs no
initialize round trip. Each thread keeps one HTTPS connection open (keep-alive), so only
the first request pays for TCP and TLS. `initialize()` is still available for callers
that want the server's name and version (doctor, whoami); it costs one request.

The token is never printed or logged.
"""
from __future__ import annotations

import http.client
import itertools
import json
import threading
import urllib.parse
from typing import Any

CLIENT_VERSION = "0.2.0"
PROTOCOL_VERSION = "2025-06-18"
CATALOG_REVISION_KEY = "net.tryagentos/catalogRevision"


from . import auth, paths

NoToken = auth.NotSignedIn


class ToolError(RuntimeError):
    """The server ran the request and said no. str() is the server's own message, as plain text."""


class HTTPError(RuntimeError):
    def __init__(self, status: int, reason: str):
        super().__init__(f"AgentOS answered HTTP {status} {reason}".strip())
        self.status = status


def resolve_token() -> str:
    return auth.resolve_token()


def resolve_url() -> str:
    return paths.mcp_url()


def content_text(content: Any) -> str:
    """Text of an MCP content list (or anything else), without Python reprs."""
    if isinstance(content, list):
        parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
        return "\n".join(p for p in parts if p).strip()
    if isinstance(content, dict):
        return str(content.get("message") or content.get("text") or json.dumps(content))
    return str(content or "").strip()


def _error_text(err: Any) -> str:
    if isinstance(err, dict):
        msg = err.get("message") or ""
        data = err.get("data")
        if isinstance(data, str) and data and data not in msg:
            msg = f"{msg}: {data}" if msg else data
        return msg or json.dumps(err)
    return str(err)


# Errors on a reused keep-alive connection that mean "the server closed it while idle":
# the request never reached the application, so sending it again is safe.
_STALE = (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError, http.client.CannotSendRequest)


class Client:
    def __init__(self, timeout: float = 20.0, url: str | None = None, token: str | None = None):
        self.timeout = timeout
        self.url = url or resolve_url()
        u = urllib.parse.urlsplit(self.url)
        self._scheme, self._host = u.scheme, u.netloc
        self._path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self._token = token or resolve_token()
        self._ids = itertools.count(1)
        self._local = threading.local()
        self._protocol_version = PROTOCOL_VERSION
        self.server_info: dict = {}
        self.server_catalog_revision = None
        self.catalog_revision = None

    # ------------------------------------------------------------ transport
    def _conn(self, fresh: bool = False) -> tuple[http.client.HTTPConnection, bool]:
        c = getattr(self._local, "conn", None)
        if c is not None and not fresh:
            return c, True
        if c is not None:
            c.close()
        cls = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        c = cls(self._host, timeout=self.timeout)
        self._local.conn = c
        return c, False

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def _post(self, payload: dict):
        body = json.dumps(payload).encode()
        headers = {
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json",
            "User-Agent": f"aos-cli/{CLIENT_VERSION}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._protocol_version,
        }
        for attempt in (0, 1):
            conn, reused = self._conn(fresh=attempt == 1)
            try:
                conn.request("POST", self._path, body, headers)
                r = conn.getresponse()
                raw = r.read()
            except _STALE:
                conn.close()
                self._local.conn = None
                if reused and attempt == 0:
                    continue
                raise
            except Exception:
                conn.close()
                self._local.conn = None
                raise
            if (r.getheader("Connection") or "").lower() == "close":
                conn.close()
                self._local.conn = None
            break
        if r.status == 401:
            raise auth.NotSignedIn("AgentOS didn't accept your sign-in. Run `aos login` again.")
        text = raw.decode("utf-8", "replace")
        ctype = r.getheader("Content-Type") or ""
        if r.status >= 400 and "json" not in ctype and "event-stream" not in ctype:
            raise HTTPError(r.status, r.reason or "")
        if "text/event-stream" in ctype:
            data = None
            for line in text.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
            return data
        if not text.strip():
            if r.status >= 400:
                raise HTTPError(r.status, r.reason or "")
            return None
        return json.loads(text)

    # ------------------------------------------------------------ protocol
    def initialize(self, client_name: str = "aos-cli", version: str = CLIENT_VERSION):
        """Only needed for server_info; tool calls work without it."""
        res = self._post({
            "jsonrpc": "2.0", "id": next(self._ids), "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": client_name, "version": version}},
        })
        result = (res or {}).get("result", {})
        self._protocol_version = result.get("protocolVersion") or PROTOCOL_VERSION
        self.server_info = result.get("serverInfo", {})
        self.server_catalog_revision = (result.get("_meta") or {}).get(CATALOG_REVISION_KEY)
        return res

    def call(self, tool_name: str, **args):
        res = self._post({
            "jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        })
        if res is None:
            return None
        if "error" in res:
            raise ToolError(_error_text(res["error"]))
        result = res.get("result", {})
        if result.get("isError"):
            raise ToolError(content_text(result.get("content")) or f"{tool_name} failed")
        if "structuredContent" in result:
            return result["structuredContent"]
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except Exception:
            return joined

    def list_tools(self) -> list[str]:
        res = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/list", "params": {}})
        if res and "error" in res:
            raise ToolError(_error_text(res["error"]))
        result = (res or {}).get("result", {})
        self.catalog_revision = (result.get("_meta") or {}).get(CATALOG_REVISION_KEY)
        return [t["name"] for t in result.get("tools", [])]


def connect(timeout: float = 20.0, info: bool = False) -> Client:
    """A ready client. No network until the first call, unless info=True (fetches server_info)."""
    c = Client(timeout=timeout)
    if info:
        c.initialize()
    return c


def friendly_error(e: BaseException) -> str:
    """One plain sentence for any failure talking to AgentOS. Never a traceback or a repr."""
    if isinstance(e, auth.NotSignedIn):
        return str(e) or "Not signed in. Run `aos login`."
    if isinstance(e, ToolError):
        msg = str(e)
        # Server messages carry a "Recovery:" hint aimed at agents; keep it, on its own line.
        head, _, rest = msg.partition("\nRecovery:")
        head = head.removeprefix("ValueError: ").strip()
        return head + (f"\n  Hint: {rest.strip()}" if rest.strip() else "")
    if isinstance(e, HTTPError):
        return f"{e}. Try again; if it keeps happening, run `aos doctor`."
    if isinstance(e, TimeoutError):
        return "AgentOS didn't answer in time. Try again, or check your connection."
    if isinstance(e, OSError):
        return f"Couldn't reach AgentOS ({type(e).__name__}). Check your connection, then `aos doctor`."
    if isinstance(e, ValueError):
        return "AgentOS sent a reply aos couldn't read. Try again; `aos doctor` checks the connection."
    return f"{type(e).__name__}: {e}"
