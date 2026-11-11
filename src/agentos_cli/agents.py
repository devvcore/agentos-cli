"""Find the coding agents on this computer and add (or remove) the AgentOS MCP server.

Every change is planned first as a `Change` (path, text before, text after), so
`aos setup` can show a diff, back the file up and record what it did. We only
ever add or remove our own `agentos` entry; a hand-made `agentos` entry that
differs from ours is reported and left alone unless --force.

No tokens are written. Each agent signs in to AgentOS with OAuth itself the
first time it connects (the server supports DCR + PKCE for all of these).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import paths

NAME = "agentos"


@dataclass
class Change:
    agent: str
    what: str                      # "mcp" | "skill"
    path: Path
    before: str | None             # None: file doesn't exist yet
    after: str | None              # None: delete the file
    summary: str
    undo: dict = field(default_factory=dict)


@dataclass
class Agent:
    id: str
    name: str
    markers: list[Callable[[], Path]]     # any existing => installed
    commands: list[str]
    mcp_format: str | None                # key into WRITERS, or "link"
    mcp_file: Callable[[], Path] | None
    skills_dir: Callable[[], Path] | None
    sign_in: str                          # what the person does next

    def found(self) -> str | None:
        """Where we found it (a path or a command), or None."""
        for c in self.commands:
            if shutil.which(c):
                return shutil.which(c)
        for m in self.markers:
            p = m()
            if p.exists():
                return str(p)
        return None


H = paths.home
MAC = sys.platform == "darwin"


def _app_support(*parts: str) -> Path:
    if MAC:
        return H().joinpath("Library", "Application Support", *parts)
    return H().joinpath(".config", *parts)


AGENTS: list[Agent] = [
    Agent("claude-code", "Claude Code", [lambda: H() / ".claude"], ["claude"], "claude_json",
          lambda: H() / ".claude.json", lambda: H() / ".claude" / "skills",
          "In Claude Code run /mcp, pick agentos, then Authenticate."),
    Agent("codex", "Codex", [lambda: H() / ".codex"], ["codex"], "codex_toml",
          lambda: H() / ".codex" / "config.toml", lambda: H() / ".agents" / "skills",
          "Run `codex mcp login agentos` (or start Codex; it asks you to sign in)."),
    Agent("opencode", "opencode", [lambda: H() / ".config" / "opencode"], ["opencode"], "opencode_json",
          lambda: H() / ".config" / "opencode" / "opencode.json", lambda: H() / ".agents" / "skills",
          "Start opencode; it opens the AgentOS sign-in the first time it connects."),
    Agent("cursor", "Cursor", [lambda: H() / ".cursor", lambda: Path("/Applications/Cursor.app")], ["cursor"],
          "cursor_json", lambda: H() / ".cursor" / "mcp.json", lambda: H() / ".agents" / "skills",
          "In Cursor open Settings → MCP and click Connect next to agentos."),
    Agent("gemini-cli", "Gemini CLI", [lambda: H() / ".gemini"], ["gemini"], "gemini_json",
          lambda: H() / ".gemini" / "settings.json", lambda: H() / ".agents" / "skills",
          "In Gemini CLI run /mcp auth agentos."),
    Agent("windsurf", "Windsurf", [lambda: H() / ".codeium" / "windsurf"], ["windsurf"], "windsurf_json",
          lambda: H() / ".codeium" / "windsurf" / "mcp_config.json", None,
          "In Windsurf open the MCP panel and refresh; it asks you to sign in."),
    Agent("vscode", "VS Code (Copilot)", [lambda: _app_support("Code", "User")], ["code"], "vscode_json",
          lambda: _app_support("Code", "User") / "mcp.json", None,
          "In VS Code run 'MCP: List Servers', pick agentos, then Start."),
    Agent("claude-desktop", "Claude desktop", [lambda: _app_support("Claude"), lambda: Path("/Applications/Claude.app")],
          [], "link", None, None,
          "Claude desktop adds connectors in the app: `aos setup` opens the AgentOS connector page for you."),
]

BY_ID = {a.id: a for a in AGENTS}


def detected() -> list[tuple[Agent, str]]:
    return [(a, w) for a in AGENTS if (w := a.found())]


def claude_connector_link(url: str | None = None) -> str:
    q = urllib.parse.urlencode({"modal": "add-custom-connector", "connectorName": "AgentOS",
                                "connectorUrl": url or paths.mcp_url()})
    return "https://claude.ai/customize/connectors?" + q


# ---------------------------------------------------------------- JSON configs

def _entry(fmt: str, url: str) -> dict:
    return {
        "claude_json": {"type": "http", "url": url},
        "opencode_json": {"type": "remote", "url": url, "enabled": True},
        "cursor_json": {"url": url},
        "gemini_json": {"httpUrl": url},
        "windsurf_json": {"serverUrl": url},
        "vscode_json": {"type": "http", "url": url},
    }[fmt]


_KEY = {"claude_json": ("mcpServers",), "opencode_json": ("mcp",), "cursor_json": ("mcpServers",),
        "gemini_json": ("mcpServers",), "windsurf_json": ("mcpServers",), "vscode_json": ("servers",)}


def _load_json(text: str | None, path: Path) -> dict:
    if not text or not text.strip():
        return {}
    try:
        d = json.loads(text)
    except ValueError as e:
        raise ValueError(f"{path} isn't valid JSON ({e.msg} at line {e.lineno}); fix it or skip this agent.")
    if not isinstance(d, dict):
        raise ValueError(f"{path} isn't a JSON object.")
    return d


def _dump_json(d: dict, original: str | None) -> str:
    indent = 2
    if original:
        m = re.search(r"\n( +|\t)\"", original)
        if m:
            indent = "\t" if m.group(1) == "\t" else len(m.group(1))
    out = json.dumps(d, indent=indent, ensure_ascii=False)
    return out + "\n" if (original is None or original.endswith("\n")) else out


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except FileNotFoundError:
        return None


def mcp_status(agent: Agent, url: str | None = None) -> str:
    """'ours' | 'custom' | 'absent' | 'link' | 'error'."""
    url = url or paths.mcp_url()
    if agent.mcp_format == "link":
        return "link"
    path = agent.mcp_file()
    text = _read(path)
    try:
        if agent.mcp_format == "codex_toml":
            block = _toml_block(text or "")
            if block is None:
                return "absent"
            return "ours" if f'url = "{url}"' in block else "custom"
        d = _load_json(text, path)
    except ValueError:
        return "error"
    cur = _dig(d, _KEY[agent.mcp_format]).get(NAME) if isinstance(_dig(d, _KEY[agent.mcp_format]), dict) else None
    if cur is None:
        return "absent"
    return "ours" if cur == _entry(agent.mcp_format, url) else "custom"


def _dig(d: dict, keys: tuple) -> Any:
    for k in keys:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d


def plan_mcp_add(agent: Agent, url: str | None = None, force: bool = False) -> Change | None:
    url = url or paths.mcp_url()
    if agent.mcp_format == "link":
        return None
    path = agent.mcp_file()
    before = _read(path)
    status = mcp_status(agent, url)
    if status == "ours":
        return None
    if status == "custom" and not force:
        raise FileExistsError(f"{agent.name} already has its own `agentos` server in {path}; left alone (use --force to replace it).")
    if status == "error":
        _load_json(before, path)  # raises the readable error
    if agent.mcp_format == "codex_toml":
        text = before or ""
        if status == "custom":
            text = _toml_remove(text)
        sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        after = text + sep + f'[mcp_servers.{NAME}]\nurl = "{url}"\n'
    else:
        d = _load_json(before, path)
        node = d
        for k in _KEY[agent.mcp_format]:
            if not isinstance(node.get(k), dict):
                node[k] = {}
            node = node[k]
        node[NAME] = _entry(agent.mcp_format, url)
        after = _dump_json(d, before)
    return Change(agent.id, "mcp", path, before, after, f"add the AgentOS MCP server to {agent.name}",
                  {"type": "mcp", "agent": agent.id})


def plan_mcp_remove(agent: Agent, url: str | None = None, force: bool = False) -> Change | None:
    url = url or paths.mcp_url()
    if agent.mcp_format == "link":
        return None
    path = agent.mcp_file()
    before = _read(path)
    status = mcp_status(agent, url)
    if status in ("absent", "error", "link"):
        return None
    if status == "custom" and not force:
        return None  # not ours
    if agent.mcp_format == "codex_toml":
        after = _toml_remove(before or "")
    else:
        d = _load_json(before, path)
        _dig(d, _KEY[agent.mcp_format]).pop(NAME, None)
        after = _dump_json(d, before)
    return Change(agent.id, "mcp", path, before, after, f"remove the AgentOS MCP server from {agent.name}")


# ---------------------------------------------------------------- Codex TOML (text-level, keeps comments)

_HDR = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def _is_ours_header(line: str) -> bool:
    m = _HDR.match(line)
    if not m:
        return False
    name = m.group(1).replace('"', "")
    return name == f"mcp_servers.{NAME}" or name.startswith(f"mcp_servers.{NAME}.")


def _toml_block(text: str) -> str | None:
    lines, out, inside, found = text.splitlines(), [], False, False
    for line in lines:
        if _HDR.match(line):
            inside = _is_ours_header(line)
            found = found or inside
        if inside:
            out.append(line)
    return "\n".join(out) if found else None


def _toml_remove(text: str) -> str:
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        if _HDR.match(line.rstrip("\n")):
            inside = _is_ours_header(line.rstrip("\n"))
        if not inside:
            out.append(line)
    res = re.sub(r"\n{3,}", "\n\n", "".join(out))
    # drop the blank separator line we added in front of our block
    return res.rstrip("\n") + "\n" if res.strip() else ""
