"""Where things live. Everything hangs off Path.home(), so tests point HOME at a temp dir."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_BASE = "https://tryagentos.net"


def base_url() -> str:
    return (os.environ.get("AGENTOS_URL") or DEFAULT_BASE).rstrip("/")


def mcp_url() -> str:
    return os.environ.get("AGENTOS_MCP_URL", "").strip() or base_url() + "/api/mcp"


def api_url() -> str:
    return base_url() + "/api"


def home() -> Path:
    return Path.home()


def state_dir() -> Path:
    return home() / ".agentos"


def cli_state_path() -> Path:
    return state_dir() / "cli.json"


def manifest_path() -> Path:
    return state_dir() / "install.json"


def backups_dir() -> Path:
    return state_dir() / "backups"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any, mode: int | None = None) -> None:
    write_text(path, json.dumps(data, indent=2) + "\n", mode)


def write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Atomic write: temp file in the same folder, then rename. Keeps the old mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None and path.exists():
        mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
