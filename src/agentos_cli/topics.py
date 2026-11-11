"""`aos help <topic>`: short, plain explanations of how the pieces fit."""
from __future__ import annotations

TOPICS: dict[str, tuple[str, str]] = {
    "overview": ("How the pieces fit", """
AgentOS is your team's shared workspace: context, tasks, CRM, skills, keys and
connected apps. `aos` puts it on this computer, in three ways:

  1. The MCP server      `aos setup` adds it to each coding agent (Claude Code,
                         Codex, Cursor, Claude desktop…). The agent then calls
                         AgentOS tools itself. Each agent signs in once.
  2. Skills              `aos setup` installs the AgentOS skills into your agents,
                         so they know how to use AgentOS and `aos`.
  3. The `aos` command   you and your agents run AgentOS from the terminal:
                         `aos context`, `aos call`, `aos secrets`, `aos connect`…

Optional:
  Spaces (`aos link`)    share a folder with people and agents, with versions.
                         You push and pull; nothing syncs by itself.
  Capture                compression and work capture. Private preview.

`aos status` shows all of it at a glance. Topics: aos help setup | mcp | skills |
spaces | capture | secrets | status
"""),
    "setup": ("aos setup", """
Finds the coding agents on this computer and shows one line per agent: what it
gets (the AgentOS MCP server, N skills) and which file changes. Enter applies
it. Every file is backed up to ~/.agentos/backups first, and `aos setup --undo`
puts them back.

If you have skills of your own, setup asks once whether to add them to your
workspace (default: not now). Run it again any time: it only does what's missing.

  aos setup --yes                  no questions (agents use this)
  aos setup --agents codex,cursor  only these agents
  aos setup --dry-run              show the plan, change nothing
"""),
    "mcp": ("The AgentOS MCP server", """
MCP is how a coding agent calls AgentOS tools directly. `aos setup` adds a
server named `agentos` (https://tryagentos.net/api/mcp) to each agent's config.
No token is written: each agent signs in once with your AgentOS account.

  Claude Code      run /mcp, pick agentos, Authenticate
  Codex            codex mcp login agentos
  Cursor           Settings → MCP → Connect
  Claude desktop   quit (⌘Q) and reopen; needs Node 18+ (it uses mcp-remote)

`aos status` shows which agents have it.
"""),
    "skills": ("Skills", """
Skills are folders with a SKILL.md that teach an agent how to do something.

  aos skills list           AgentOS skills and your workspace's skills
  aos skills add <name>     install one into every agent here
  aos skills push           add skills you wrote to your workspace
  aos skills outdated / update / remove

Installed skills live in ~/.claude/skills (Claude Code) and ~/.agents/skills
(Codex, Cursor, opencode, Gemini). aos never touches a folder it didn't install.
"""),
    "spaces": ("Spaces: sharing a folder (aos link)", """
A space is a folder shared through AgentOS, with versions, like a tiny Git.

  aos link my-project   tie this folder to a space
  aos push -m "…"       upload your changes as a new version
  aos pull              download what others changed (never overwrites your edits)
  aos status            what changed, who's ahead, who has claimed what
  aos claim / handoff / wait   coordinate with other people and agents

Nothing syncs by itself: changes move only when you (or your agent) run
`aos push` and `aos pull`. `aos watch` shows changes as they happen. There is
no background auto-sync, and you shouldn't need a script for it; if macOS
says a "sync" item from an unidentified developer wants to run in the
background, that is a script someone added, not part of aos (see
`aos help status` to check what's running).
"""),
    "capture": ("Capture and compression", """
Capture is separate from spaces. It runs a small proxy on this computer that:
  • compresses large tool output so your agents use fewer tokens, and
  • (if you turn it on) learns decisions and fixes from your coding sessions
    in folders you choose, and adds them to your team's AgentOS memory.

It is in private preview and isn't installed by `aos`. `aos status` says
whether it's on. You don't need it for anything else in aos.
"""),
    "secrets": ("Keys and connected apps", """
  aos secrets set stripe_api_key     store a key (hidden prompt or stdin)
  aos secrets list                   names only, never values
  aos http stripe_api_key <url>      call an API; AgentOS adds the key
  aos connect hubspot                connect an app with its own sign-in

Keys stay in AgentOS: they never land on this computer. Storing keys needs one
extra permission: `aos login --scope secrets`.
"""),
    "status": ("aos status", """
One view of everything on this computer: who you're signed in as, which agents
have the AgentOS MCP server and skills, whether capture is running, and, in a
linked folder, the space (changes, versions, claims).

  aos status          the overview (plus the space, in a linked folder)
  aos status --json   the overview for scripts and agents
  aos doctor          the same checks, plus a live connection test
"""),
}

ALIASES = {"link": "spaces", "space": "spaces", "sync": "spaces", "push": "spaces", "pull": "spaces",
           "compression": "capture", "keys": "secrets", "connect": "secrets", "credentials": "secrets",
           "login": "setup", "agents": "mcp", "about": "overview", "concepts": "overview"}


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
