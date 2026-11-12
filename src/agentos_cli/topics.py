"""`aos help <topic>`: short, plain explanations of how the pieces fit."""
from __future__ import annotations

TOPICS: dict[str, tuple[str, str]] = {
    "overview": ("How the pieces fit", """
AgentOS is your team's shared workspace: context, tasks, CRM, skills, keys and
connected apps. `aos` puts it on this computer:

  aos setup          adds AgentOS (an MCP server, plus skills) to each coding agent
                     here: Claude Code, Codex, Cursor, Claude desktop… Each agent
                     signs in once, then uses AgentOS itself.
  aos context        what AgentOS knows about a person, company or topic.
  aos call           do something: in AgentOS itself (`aos call agentos`) or in one
                     of your apps (`aos call gmail`).
  aos integrations   your connected apps.   aos connect   connect another one.
  aos secrets        store API keys.        aos skills    the skills your agents use.
  aos space          share a folder with people and agents (optional).

`aos status` shows all of it at a glance.
Topics: aos help setup | call | integrations | space | secrets | skills | status
"""),
    "setup": ("aos setup", """
Finds the coding agents on this computer and shows one line per agent: what it
gets (the AgentOS MCP server, N skills) and which file changes. Enter applies
it. Every file is backed up to ~/.agentos/backups first, and `aos setup --undo`
puts them back. If you have skills of your own, setup asks once whether to add
them to your workspace (default: not now). Run it again any time.

Each agent then signs in once:
  Claude Code      run /mcp, pick agentos, Authenticate
  Codex            codex mcp login agentos
  Cursor           Settings → MCP → Connect
  Claude desktop   quit (⌘Q) and reopen; needs Node 18+

  aos setup --yes                  no questions (agents use this)
  aos setup --agents codex,cursor  only these agents
  aos setup --dry-run              show the plan, change nothing
"""),
    "call": ("aos call: do something", """
  aos call agentos                       what AgentOS itself can do: tasks, CRM, memory, files…
  aos call agentos tasks                 search it
  aos call agentos tasks_list '{"status":"open"}'

  aos call gmail                         what one of your apps can do
  aos call gmail send                    search it
  aos call gmail send_email '{"recipient_email":"ada@example.com","subject":"Hi","body":"…"}'
  aos call gmail send_email --describe   what it does and its arguments

An action runs only when its name matches exactly; anything else is a search,
so a typo never runs the wrong thing. AgentOS holds the apps' logins: no key
ever reaches this computer. Exit code 2 means it's waiting for a person's
approval: not done yet. Your apps: `aos integrations`.
"""),
    "integrations": ("aos integrations: your apps", """
  aos integrations          the apps connected to your workspace, their accounts
                            and how many actions each has
  aos integrations --all    also apps you can connect
  aos connect hubspot       connect one (with the app's own sign-in)
  aos call gmail            use one
"""),
    "skills": ("Skills", """
Skills are folders with a SKILL.md that teach an agent how to do something.

  aos skills list           AgentOS skills and your workspace's skills
  aos skills add <name>     install one into every agent here
  aos skills push           add skills you wrote to your workspace
  aos skills remove <name>

Installed skills live in ~/.claude/skills (Claude Code) and ~/.agents/skills
(Codex, Cursor, opencode, Gemini). aos never touches a folder it didn't install.
"""),
    "space": ("aos space: sharing a folder", """
A space is a folder shared through AgentOS, with versions, like a tiny Git.

  aos space link my-project   tie this folder to a space
  aos space push -m "…"       upload your changes as a new version
  aos space pull              download what others changed (never overwrites yours)
  aos space sync              push and pull for you while it runs; Ctrl-C stops it
  aos space status            what changed, who's ahead, who claimed what
  aos space claim / handoff / wait   coordinate with other people and agents

Changes move only when you push or pull, or while `aos space sync` is running in
a terminal. There's no background mode: leave sync open in a tab. Don't set up
scripts or login items for it; macOS flags them as from an unidentified developer.
"""),
    "secrets": ("Keys", """
  aos secrets list                  your keys: names and descriptions, never values
  aos secrets get stripe_api_key    print the value
  aos secrets set stripe_api_key    store or replace one (hidden prompt, or stdin)

Use one in a script:
  STRIPE_API_KEY=$(aos secrets get stripe_api_key) python report.py

AgentOS records every read (who, which key, when); you see it in Settings →
Secrets. Personal keys are yours alone; workspace keys go to members with
access; bots' own credentials are never handed out. Reading needs one extra
permission, once: `aos login --scope secrets`. Apps with their own sign-in
(Gmail, HubSpot…) are connected with `aos connect <app>` instead.
"""),
    "status": ("aos status", """
One view: who you're signed in as, which agents have the AgentOS MCP server and
skills, and whether this folder is shared as a space (then its details follow).

  aos status          the overview
  aos status --json   the same, for scripts and agents
  aos status --check  also tests the connection to AgentOS
"""),
}

ALIASES = {"spaces": "space", "link": "space", "sync": "space", "auto-sync": "space", "autosync": "space",
           "push": "space", "pull": "space", "run": "call", "do": "call", "actions": "call", "agentos": "call",
           "tools": "call", "apps": "integrations", "connect": "integrations", "keys": "secrets",
           "credentials": "secrets", "login": "setup", "mcp": "setup", "agents": "setup", "about": "overview",
           "concepts": "overview", "doctor": "status"}


def show(topic: str | None) -> int:
    from . import ui
    key = ALIASES.get((topic or "").lower(), (topic or "").lower())
    if key not in TOPICS:
        ui.fail(f"No help topic {topic!r}. Topics: {', '.join(TOPICS)}")
        return 2
    title, body = TOPICS[key]
    ui.console.print(f"[bold]{title}[/]")
    ui.console.print(body.strip("\n"), highlight=False, markup=False)
    return 0
