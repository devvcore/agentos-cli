"""`aos setup`: find your agents, tick what to add, review every change, apply with backups.

Modeled on the AWS CLI's agent toolkit: one command puts the MCP server and the
skills into every coding agent it finds. Differences: you see the exact diff
first, every file is backed up, and `aos setup --undo` takes it all out again.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

from . import agents as A, auth, paths, skills as S, ui


def _choose(question: str, choices: list, default_all=True):
    import questionary
    return questionary.checkbox(question, choices=choices, style=ui.qstyle(),
                                instruction="(space to toggle, a for all, enter to continue)").ask()


def _capture_status() -> str:
    return "agentos-capture installed" if shutil.which("agentos-capture") else "needs agentos-capture"


def _backup(path: Path, stamp: str) -> str | None:
    if not path.exists():
        return None
    try:
        rel = "~/" + path.relative_to(paths.home()).as_posix()
    except ValueError:
        rel = path.as_posix()
    dest = paths.backups_dir() / stamp / rel.strip("~/").replace("/", "__")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return str(dest)


def _apply_change(ch: A.Change, stamp: str, replan) -> dict:
    # Re-read right before writing: Claude Code rewrites ~/.claude.json often,
    # so if the file moved since the review, plan again against what's there now.
    now = ch.path.read_text() if ch.path.exists() else None
    if now != ch.before:
        fresh = replan()
        if fresh is None:
            return {"agent": ch.agent, "path": str(ch.path), "status": "already done"}
        ch = fresh
    backup = _backup(ch.path, stamp)
    paths.write_text(ch.path, ch.after or "")
    return {"agent": ch.agent, "path": str(ch.path), "backup": backup, "status": "written"}


def _record(entry: dict) -> None:
    m = paths.read_json(paths.manifest_path(), {"runs": []})
    m.setdefault("runs", []).append(entry)
    paths.write_json(paths.manifest_path(), m)


def run(args) -> int:
    inter = ui.interactive() and not args.yes
    if not inter and not args.yes and not args.dry_run:
        ui.fail("`aos setup` needs a terminal to ask you questions. Re-run with --yes (and --agents …) to accept the defaults.")
        return 2
    if args.undo:
        return undo(args, inter)

    try:
        _, src = auth.token_source()
        who = f"signed in ({src})"
    except auth.NotSignedIn:
        who = "not signed in"
    except Exception:
        who = "sign-in check failed"
    ui.header("AgentOS setup", who)

    # 1. agents
    found = A.detected()
    if args.agents:
        wanted = [x.strip() for x in args.agents.split(",") if x.strip()]
        bad = [w for w in wanted if w not in A.BY_ID]
        if bad:
            ui.fail(f"unknown agent(s): {', '.join(bad)}. Known: {', '.join(A.BY_ID)}")
            return 2
        chosen = [A.BY_ID[w] for w in wanted]
    else:
        chosen = [a for a, _ in found]

    t = ui.table("", "Agent", "Found at", "AgentOS MCP", "Skills folder")
    found_ids = {a.id for a, _ in found}
    for a in A.AGENTS:
        where = next((w for x, w in found if x.id == a.id), None)
        st = A.mcp_status(a) if where else ""
        label = {"ours": "[green]connected[/]", "custom": "[yellow]custom entry[/]", "absent": "—",
                 "link": "added in the app", "error": "[red]config unreadable[/]"}.get(st, "")
        t.add_row("[green]●[/]" if where else "[dim]○[/]", a.name if where else f"[dim]{a.name}[/]",
                  ui.short(where) if where else "[dim]not installed[/]", label,
                  ui.short(a.skills_dir()) if (where and a.skills_dir) else "")
    ui.console.print(t)
    if not found and not args.agents:
        ui.warn("No coding agents found. Install Claude Code, Codex, Cursor, opencode or Gemini CLI, then run `aos setup` again.")
        return 1

    if inter and not args.agents:
        import questionary
        picked = _choose("Set up which agents?", [
            questionary.Choice(a.name, value=a.id, checked=True) for a, _ in found])
        if picked is None:
            return 130
        chosen = [A.BY_ID[i] for i in picked]
    if not chosen:
        ui.dim("Nothing selected.")
        return 0

    # 2. what to add
    want_mcp, want_bundled, want_ws, want_capture = not args.no_mcp, args.skills in ("agentos", "all"), \
        args.skills in ("workspace", "all") or bool(args.workspace_skills), args.capture
    bundled = S.bundled()
    if inter:
        import questionary
        cap = _capture_status()
        opts = [
            questionary.Choice(f"AgentOS MCP server     {paths.mcp_url()} (each agent signs in with OAuth)", value="mcp", checked=want_mcp),
            questionary.Choice(f"AgentOS skills         {', '.join(s.name for s in bundled)}", value="bundled", checked=want_bundled),
            questionary.Choice("Workspace skills       pick from your workspace's skills library", value="ws", checked=want_ws),
            questionary.Choice("Compression + capture  local proxy, folders you choose", value="capture",
                               checked=want_capture and bool(shutil.which("agentos-capture")),
                               disabled=None if shutil.which("agentos-capture") else "coming soon"),
        ]
        picked = _choose("Add to each agent:", opts)
        if picked is None:
            return 130
        want_mcp, want_bundled, want_ws, want_capture = ("mcp" in picked, "bundled" in picked, "ws" in picked, "capture" in picked)

    skills_to_install: list[S.Skill] = list(bundled) if want_bundled else []
    if want_ws:
        try:
            from .work import connect
            client = connect()
            listing = S.workspace_list(client)
        except auth.NotSignedIn as e:
            ui.warn(f"Workspace skills skipped: {e}")
            listing, client = [], None
        except Exception as e:
            ui.warn(f"Workspace skills skipped: couldn't read the skills library ({type(e).__name__}).")
            listing, client = [], None
        slugs = [x.strip() for x in (args.workspace_skills or "").split(",") if x.strip()]
        if listing and inter and not slugs:
            import questionary
            picked = _choose("Which workspace skills?", [
                questionary.Choice(f"{s['name'][:48]:<48} {s['description'][:60]}", value=s["slug"], checked=False)
                for s in listing])
            slugs = picked or []
        elif listing and not slugs and args.skills in ("workspace", "all"):
            slugs = [s["slug"] for s in listing]
        meta = {s["slug"]: s for s in listing}
        for slug in slugs:
            try:
                skills_to_install.append(S.workspace_skill(client, slug, meta.get(slug)))
            except Exception as e:
                ui.warn(f"skipped workspace skill {slug} ({type(e).__name__})")

    # 3. plan
    changes: list[A.Change] = []
    notes: list[str] = []
    links: list[str] = []
    if want_mcp:
        for a in chosen:
            if a.mcp_format == "link":
                links.append(A.claude_connector_link())
                continue
            try:
                ch = A.plan_mcp_add(a, force=args.force)
            except (FileExistsError, ValueError) as e:
                notes.append(str(e))
                continue
            if ch:
                changes.append(ch)
            else:
                notes.append(f"{a.name}: AgentOS MCP server already added.")
    dirs = S.target_dirs([a.id for a in chosen])
    skill_plan = []
    for base, users in dirs:
        for sk in skills_to_install:
            status, dest = S.plan_install(sk, base, args.force)
            skill_plan.append((sk, base, users, status, dest))

    # 4. review
    ui.console.print()
    ui.console.print("[bold]Review[/]")
    for ch in changes:
        ui.console.print(f"  [green]+[/] {ch.summary}  [dim]{ui.short(ch.path)}[/]")
        ui.diff(ch.before, ch.after, ui.short(ch.path), max_lines=24)
    if skill_plan:
        t = ui.table("Skill", "Source", "Into", "For", "")
        for sk, base, users, status, dest in skill_plan:
            label = {"new": "[green]install[/]", "update": "[cyan]update[/]", "current": "[dim]up to date[/]",
                     "edited": "[yellow]you edited it; left alone[/]", "clash": "[yellow]name taken; skipped[/]"}[status]
            t.add_row(dest.name if dest else sk.name, sk.source, ui.short(base), ", ".join(users), label)
        ui.console.print(t)
    for link in links:
        ui.console.print(f"  [green]+[/] Claude desktop: open the AgentOS connector page  [dim]{link}[/]")
    for n in notes:
        ui.dim(f"  · {n}")
    if want_capture:
        ui.console.print(f"  [green]+[/] Compression + capture: run `agentos-capture setup` ({_capture_status()})")

    todo_skills = [p for p in skill_plan if p[3] in ("new", "update")]
    if not (changes or todo_skills or links or want_capture):
        ui.ok("Everything selected is already set up.")
        _next_steps(chosen, want_mcp)
        return 0
    if args.dry_run:
        ui.dim("Dry run: nothing was changed.")
        if args.json:
            print(json.dumps({"changes": [{"agent": c.agent, "path": str(c.path)} for c in changes],
                              "skills": [{"skill": p[0].name, "dir": str(p[1]), "status": p[3]} for p in skill_plan]}))
        return 0
    if inter:
        import questionary
        if not questionary.confirm("Apply these changes?", default=True, style=ui.qstyle()).ask():
            ui.dim("Nothing was changed.")
            return 1

    # 5. apply
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_rec = {"at": stamp, "mcp": [], "skills": []}
    for ch in changes:
        try:
            r = _apply_change(ch, stamp, lambda ch=ch: A.plan_mcp_add(A.BY_ID[ch.agent], force=args.force))
            run_rec["mcp"].append(r)
            ui.ok(f"{A.BY_ID[ch.agent].name}: AgentOS MCP server added  [dim]{ui.short(ch.path)}"
                  + (f" · backup {ui.short(r['backup'])}" if r.get("backup") else "") + "[/]")
        except Exception as e:
            ui.fail(f"{A.BY_ID[ch.agent].name}: couldn't write {ui.short(ch.path)} ({e})")
    for sk, base, users, status, dest in todo_skills:
        try:
            st, d = S.install(sk, base, args.force)
            if st in ("new", "update"):
                run_rec["skills"].append({"path": str(d), "source": sk.source, "id": sk.id})
                ui.ok(f"skill {d.name} {'installed' if st == 'new' else 'updated'}  [dim]{ui.short(d)}[/]")
        except Exception as e:
            ui.fail(f"skill {sk.name}: {e}")
    _record(run_rec)
    for link in links:
        if inter:
            webbrowser.open(link)
        ui.ok(f"Claude desktop: {'opened' if inter else 'open'} {link}")
    if want_capture:
        exe = shutil.which("agentos-capture")
        if not exe:
            ui.warn("Compression + capture: agentos-capture isn't installed yet; see `aos doctor`.")
        elif inter:
            subprocess.call([exe, "setup"])
        else:
            ui.dim("Compression + capture: run `agentos-capture setup` in a terminal to choose folders.")
    _next_steps(chosen, want_mcp)
    return 0


def _next_steps(chosen, want_mcp) -> None:
    if not want_mcp:
        return
    ui.console.print()
    ui.console.print("[bold]Next[/]: sign each agent in to AgentOS (one browser click each)")
    for a in chosen:
        ui.console.print(f"  [dim]•[/] [bold]{a.name}[/]: {a.sign_in}")
    ui.dim("  Then ask your agent: “set up AgentOS”.  Undo all of this with `aos setup --undo`.")


def undo(args, inter: bool) -> int:
    ui.header("Remove AgentOS from your agents")
    chosen = [A.BY_ID[x] for x in args.agents.split(",")] if args.agents else [a for a, _ in A.detected()]
    changes = [c for a in chosen if (c := A.plan_mcp_remove(a, force=args.force))]
    m = paths.read_json(paths.manifest_path(), {"runs": []})
    recorded = []
    for r in m.get("runs", []):
        for s in r.get("skills", []):
            p = Path(s["path"])
            if p.exists() and S.read_marker(p) and p not in recorded:
                recorded.append(p)
    for ch in changes:
        ui.console.print(f"  [red]-[/] {ch.summary}  [dim]{ui.short(ch.path)}[/]")
        ui.diff(ch.before, ch.after, ui.short(ch.path), max_lines=16)
    for p in recorded:
        ui.console.print(f"  [red]-[/] skill {p.name}  [dim]{ui.short(p)}[/]")
    if not (changes or recorded):
        ui.ok("Nothing from AgentOS to remove.")
        return 0
    if args.dry_run:
        ui.dim("Dry run: nothing was changed.")
        return 0
    if inter:
        import questionary
        if not questionary.confirm("Remove these?", default=False, style=ui.qstyle()).ask():
            ui.dim("Nothing was changed.")
            return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for ch in changes:
        r = _apply_change(ch, stamp, lambda ch=ch: A.plan_mcp_remove(A.BY_ID[ch.agent], force=args.force))
        ui.ok(f"{ch.summary}  [dim]{r['status']}" + (f" · backup {ui.short(r['backup'])}" if r.get("backup") else "") + "[/]")
    for p in recorded:
        st = S.remove(p.parent, p.name, args.force)
        (ui.ok if st == "removed" else ui.warn)(f"skill {p.name}: {st}")
    return 0
