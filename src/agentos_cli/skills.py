"""Install AgentOS skills into the coding agents on this computer.

Sources:
  * "agentos": skills that ship with the CLI (how to use aos, setup, spaces).
  * "workspace": your workspace's skills library, read with the AgentOS MCP
    tools skills_list / skills_get. skills_get returns the SKILL.md body only;
    companion files need a bundle route on the server (not built yet).

Where: each agent's skills folder (~/.claude/skills for Claude Code,
~/.agents/skills for Codex, opencode, Cursor and Gemini, the shared folder the
AWS toolkit also uses). Folders shared by several agents are written once.

Ownership:
  * A folder is ours only if it holds our marker `.agentos-skill.json`.
    Anything else is yours and is never overwritten or deleted; a name clash
    installs as `agentos-<name>`, or is skipped if that is taken too.
  * A skill you edited after we installed it is left alone on update and
    remove unless --force.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__, agents as agents_mod

MARKER = ".agentos-skill.json"


@dataclass
class Skill:
    name: str            # folder name
    description: str
    source: str          # "agentos" | "workspace"
    id: str              # bundled name or workspace slug
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def sha(self) -> str:
        return files_sha(self.files)


def files_sha(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode() + b"\0" + hashlib.sha256(files[rel]).digest())
    return h.hexdigest()


def safe_name(raw: str) -> str | None:
    s = re.sub(r"[^a-z0-9]+", "-", (raw or "").lower()).strip("-")[:64]
    return s or None


def _frontmatter(name: str, description: str, body: str) -> bytes:
    body = re.sub(r"\A---\n.*?\n---\n", "", body, flags=re.S).lstrip("\n")
    desc = " ".join((description or name).split()).replace('"', "'")
    return f'---\nname: {name}\ndescription: "{desc}"\n---\n\n{body}'.encode()


# ---------------------------------------------------------------- sources

def _skills_root():
    root = resources.files("agentos_cli").joinpath("skills")
    if root.is_dir():
        return root
    # editable install / source checkout: the skills sit at the repo root
    return Path(__file__).resolve().parents[2] / "skills"


def bundled() -> list[Skill]:
    root = _skills_root()
    out = []
    for d in sorted(root.iterdir(), key=lambda p: p.name):
        if not d.is_dir() or not d.joinpath("SKILL.md").is_file():
            continue
        files = {}
        stack = [(d, "")]
        while stack:
            cur, pre = stack.pop()
            for f in cur.iterdir():
                if f.is_dir():
                    stack.append((f, pre + f.name + "/"))
                elif f.is_file():
                    files[pre + f.name] = f.read_bytes()
        text = files["SKILL.md"].decode()
        m = re.search(r"^description:\s*(.+)$", text, re.M)
        out.append(Skill(d.name, (m.group(1).strip().strip('"') if m else ""), "agentos", d.name, files))
    return out


UNTITLED = re.compile(r"^untitled skill\b", re.I)


def workspace_list(client, include_untitled: bool = False) -> list[dict]:
    """[{slug, name, description}] from skills_list, following pages. Drafts named
    "Untitled skill" are left out unless include_untitled."""
    out, cursor = [], None
    for _ in range(50):
        args = {"cursor": cursor} if cursor else {}
        res = call_tool(client, "skills_list", args)
        for s in (res or {}).get("skills", []):
            if s.get("slug") and (include_untitled or not UNTITLED.match(s.get("name") or "")):
                out.append({"slug": s["slug"], "name": s.get("name") or s["slug"], "description": s.get("description") or ""})
        cursor = (res or {}).get("next_cursor")
        if not cursor:
            break
    return out


def workspace_skill(client, slug: str, meta: dict | None = None) -> Skill:
    parts, cursor, got = [], None, {}
    for _ in range(50):
        args = {"id": slug, **({"cursor": cursor} if cursor else {})}
        got = call_tool(client, "skills_get", args) or {}
        parts.append(got.get("content") or "")
        cursor = got.get("next_cursor")
        if not cursor:
            break
    name = safe_name(slug) or "skill"
    meta = meta or {}
    desc = meta.get("description") or got.get("description") or meta.get("name") or got.get("name") or slug
    return Skill(name, desc, "workspace", slug, {"SKILL.md": _frontmatter(name, desc, "".join(parts))})


def call_tool(client, name: str, args: dict) -> Any:
    """Call a workspace tool through workspace_call (the compact MCP surface)."""
    res = client.call("workspace_call", name=name, arguments=json.dumps(args))
    if isinstance(res, dict) and res.get("ok") is False:
        raise RuntimeError(res.get("error") or f"{name} failed")
    return res


# ---------------------------------------------------------------- targets

def target_dirs(agent_ids: list[str] | None = None) -> list[tuple[Path, list[str]]]:
    """[(skills folder, [agent names using it])] for the chosen (default: detected) agents."""
    chosen = [agents_mod.BY_ID[i] for i in agent_ids] if agent_ids else [a for a, _ in agents_mod.detected()]
    dirs: dict[Path, list[str]] = {}
    for a in chosen:
        if a.skills_dir:
            dirs.setdefault(a.skills_dir(), []).append(a.name)
    return list(dirs.items())


# ---------------------------------------------------------------- install / remove

def read_marker(path: Path) -> dict | None:
    if path.is_symlink() or not path.is_dir():
        return None
    try:
        return json.loads((path / MARKER).read_text())
    except (OSError, ValueError):
        return None


def disk_files_sha(path: Path) -> str:
    files = {}
    for f in path.rglob("*"):
        if f.is_file() and f.name != MARKER:
            files[f.relative_to(path).as_posix()] = f.read_bytes()
    return files_sha(files)


def _edited(path: Path, marker: dict) -> bool:
    return disk_files_sha(path) != marker.get("sha")


def plan_install(skill: Skill, base: Path, force: bool = False) -> tuple[str, Path | None]:
    """(status, destination). status: new | update | current | edited | clash."""
    for name in (skill.name, "agentos-" + skill.name):
        dest = base / name
        if not dest.exists() and not dest.is_symlink():
            return "new", dest
        marker = read_marker(dest)
        if marker is None:
            continue  # someone else's folder
        if marker.get("source") != skill.source or marker.get("id") != skill.id:
            continue
        if marker.get("sha") == skill.sha and not _edited(dest, marker):
            return "current", dest
        if _edited(dest, marker) and not force:
            return "edited", dest
        return "update", dest
    return "clash", None


def install(skill: Skill, base: Path, force: bool = False) -> tuple[str, Path | None]:
    status, dest = plan_install(skill, base, force)
    if status not in ("new", "update"):
        return status, dest
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / f".{dest.name}.aos-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    for rel, data in skill.files.items():
        p = (tmp / rel).resolve()
        if not p.is_relative_to(tmp.resolve()):
            raise ValueError(f"unsafe path in skill {skill.name}: {rel}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (tmp / MARKER).write_text(json.dumps({
        "source": skill.source, "id": skill.id, "sha": skill.sha, "installed_at": int(time.time()),
        "installed_by": f"aos {__version__}"}, indent=1) + "\n")
    if dest.exists():
        shutil.rmtree(dest)
    tmp.rename(dest)
    return status, dest


def installed(bases: list[Path]) -> list[dict]:
    out = []
    for base in bases:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            m = read_marker(d)
            if m:
                out.append({**m, "folder": d.name, "path": d, "edited": _edited(d, m)})
    return out


def remove(base: Path, name: str, force: bool = False) -> str:
    """removed | not-ours | edited | missing"""
    saw_other = False
    for folder in (name, "agentos-" + name):
        d = base / folder
        m = read_marker(d)
        if m is None:
            saw_other = saw_other or d.exists()
            continue
        if _edited(d, m) and not force:
            return "edited"
        shutil.rmtree(d)
        return "removed"
    return "not-ours" if saw_other else "missing"


def find_installed_by_id(base: Path, source: str, sid: str) -> Path | None:
    for d in (base.iterdir() if base.is_dir() else []):
        m = read_marker(d)
        if m and m.get("source") == source and m.get("id") == sid:
            return d
    return None


# ---------------------------------------------------------------- your own skills -> workspace

# Files other installers drop next to SKILL.md; a folder with one isn't the person's own skill.
VENDOR_MARKERS = (".aws-skill-metadata",)


def _front(text: str) -> tuple[dict, str]:
    m = re.match(r"\A---\n(.*?)\n---\n?", text, re.S)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if v:
                meta[k.strip()] = v.strip().strip('"').strip("'")
        text = text[m.end():]
    return meta, text.lstrip("\n")


def local_skills(bases: list[Path]) -> list[dict]:
    """Skills the person wrote: SKILL.md folders that aren't ours or another tool's."""
    out, seen = [], set()
    for base in bases:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            md = d / "SKILL.md"
            if d.name.startswith(".") or not md.is_file() or (d / MARKER).exists():
                continue
            if any((d / v).exists() for v in VENDOR_MARKERS):
                continue
            meta, body = _front(md.read_text(errors="replace"))
            name = meta.get("name") or d.name
            key = safe_name(name)
            if not key or key in seen or not body.strip():
                continue
            seen.add(key)
            out.append({"name": name, "slug": key, "description": meta.get("description", ""), "body": body,
                        "path": d, "extra_files": sum(1 for f in d.rglob("*") if f.is_file()) - 1})
    return out


def not_in_workspace(local: list[dict], workspace: list[dict]) -> list[dict]:
    have = {w["slug"] for w in workspace} | {safe_name(w["name"]) for w in workspace}
    return [s for s in local if s["slug"] not in have]


def push(client, skill: dict) -> dict:
    """Create the skill in the workspace (skills_create). Returns the tool result."""
    args = {"name": skill["name"], "content": skill["body"]}
    if skill.get("description"):
        args["description"] = skill["description"][:500]
    return call_tool(client, "skills_create", args)
