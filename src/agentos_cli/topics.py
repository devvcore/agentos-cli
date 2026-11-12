"""`aos help <topic>`: short, plain explanations of how the pieces fit."""
from __future__ import annotations

TOPICS: dict[str, tuple[str, str]] = {
    "overview": ("How the pieces fit", """
AgentOS is your team's shared workspace: context, tasks, CRM, skills, keys and
connected apps. `aos` puts it on this computer:

  aos setup          adds AgentOS (an MCP server, plus skills) to each coding agent
                     here: Claude Code, Codex, Cursor, Claude desktop… Each agent
                     signs in once, then calls AgentOS itself.
  aos context        what AgentOS knows about a person, company or topic.
  aos integrations   everything you can do through AgentOS, in one list: its own
                     actions (tasks, CRM, memory…) and your connected apps (Gmail,
                     Slack, Stripe…). `aos integrations run <action>` does one.
  aos connect        connect another app.   aos secrets   store API keys.
  aos skills         the skills your agents use.
  aos space          share a folder with people and agents (optional).

`aos status` shows all of it at a glance.
Topics: aos help setup | integrations | space | secrets | skills | status
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
    "integrations": ("aos integrations: everything you can do", """
One list of actions, from two places:
  AgentOS     your workspace itself: tasks, CRM, memory, files, meetings…
              (names like tasks_list, crm_contacts_search)
  your apps   Gmail, Slack, Stripe, HubSpot… connected to AgentOS
              (names like GMAIL_SEND_EMAIL). AgentOS holds their logins.

  aos integrations                     AgentOS and your connected apps
  aos integrations send email          find actions by words
  aos integrations show GMAIL_SEND_EMAIL     what it does, its arguments
  aos integrations run tasks_list '{"status":"open"}'
  echo '{"to":"…"}' | aos integrations run GMAIL_SEND_EMAIL

Exit code 2 means it's waiting for a person's approval: not done yet.
An app that isn't connected: `aos connect <app>`.
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
  aos secrets set stripe_api_key     store a key (hidden prompt or stdin)
  aos secrets list                   names only, never values
  aos http stripe_api_key <url>      call an API; AgentOS adds the key

Keys stay in AgentOS and never land on this computer. Storing keys needs one
extra permission: `aos login --scope secrets`. Apps with their own sign-in
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
           "push": "space", "pull": "space", "tools": "integrations", "call": "integrations", "apps": "integrations",
           "actions": "integrations", "keys": "secrets", "connect": "secrets", "credentials": "secrets",
           "login": "setup", "mcp": "setup", "agents": "setup", "about": "overview", "concepts": "overview",
           "doctor": "status"}


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
