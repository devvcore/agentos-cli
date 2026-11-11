"""Terminal look: rich for output, questionary for choices. Quiet and plain when piped."""
from __future__ import annotations

import difflib
import os
import sys

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

ACCENT = "#7c5cff"
console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

QSTYLE = None


def qstyle():
    global QSTYLE
    if QSTYLE is None:
        import questionary
        QSTYLE = questionary.Style([
            ("qmark", f"fg:{ACCENT} bold"), ("question", "bold"), ("pointer", f"fg:{ACCENT} bold"),
            ("highlighted", f"fg:{ACCENT} bold"), ("selected", "fg:#16a34a"), ("instruction", "fg:#888888"),
            ("answer", f"fg:{ACCENT}"), ("disabled", "fg:#888888 italic")])
    return QSTYLE


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get("AOS_NONINTERACTIVE")


def header(title: str, right: str = "") -> None:
    t = Table.grid(expand=True)
    t.add_column()
    t.add_column(justify="right")
    t.add_row(Text(title, style="bold"), Text(right, style="dim"))
    console.print(Panel(t, box=box.ROUNDED, border_style=ACCENT, padding=(0, 1)))


def ok(msg: str) -> None:
    console.print(f"[green]✓[/] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]![/] {msg}")


def fail(msg: str) -> None:
    err.print(f"[red]✕[/] {msg}")


def dim(msg: str) -> None:
    console.print(f"[dim]{msg}[/]")


def table(*cols: str, title: str | None = None) -> Table:
    t = Table(box=box.SIMPLE_HEAD, header_style="dim", title=title, title_justify="left", pad_edge=False)
    for c in cols:
        t.add_column(c)
    return t


def short(p) -> str:
    s = str(p)
    h = os.path.expanduser("~")
    return "~" + s[len(h):] if s.startswith(h) else s


def diff(before: str | None, after: str | None, label: str, max_lines: int = 40) -> None:
    a = (before or "").splitlines(keepends=True)
    b = (after or "").splitlines(keepends=True)
    lines = list(difflib.unified_diff(a, b, f"{label} (now)", f"{label} (after)", n=2))
    if not lines:
        return
    shown = lines[:max_lines]
    out = Text()
    for ln in shown:
        ln = ln if ln.endswith("\n") else ln + "\n"
        style = "green" if ln.startswith("+") and not ln.startswith("+++") else \
            "red" if ln.startswith("-") and not ln.startswith("---") else \
            "cyan" if ln.startswith("@@") else "dim"
        out.append(ln, style=style)
    if len(lines) > max_lines:
        out.append(f"… {len(lines) - max_lines} more diff lines\n", style="dim")
    console.print(Panel(out, box=box.MINIMAL, padding=(0, 2)))


__all__ = ["console", "err", "escape", "header", "ok", "warn", "fail", "dim", "table", "short", "diff",
           "interactive", "qstyle", "ACCENT"]
