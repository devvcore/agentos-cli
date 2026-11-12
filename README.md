<p align="center"><strong>AgentOS CLI</strong>: put AgentOS in every coding agent on your computer, and use it from the terminal.</p>

```sh
curl -fsSL https://tryagentos.net/install.sh | sh
aos login      # sign in, or create your AgentOS account, in the browser
aos setup      # add AgentOS to Claude Code, Codex, Cursor, opencode, Gemini CLI…
```

`aos setup` finds the coding agents on your computer and shows one line per agent: the AgentOS MCP server and skills it gets, and the file that changes. Press Enter and it's done; every file is backed up first and `aos setup --undo` takes it all out. Each agent then signs in to AgentOS once; no tokens are written to disk.

## Commands

| | |
|---|---|
| **Get started** | `aos login` · `aos setup` · `aos status` (`--check` tests the connection) · `aos help [topic]` |
| **Use AgentOS** | `aos context "Acme renewal"` · `aos integrations [words]` · `aos integrations show <action>` · `aos integrations run <action> '{…}'` · `aos connect <app>` |
| **Keys** | `aos secrets set stripe_api_key` (hidden prompt or stdin) · `aos secrets list` · `aos http stripe_api_key https://api.stripe.com/v1/…` (AgentOS adds the key; it never reaches your computer) |
| **Skills** | `aos skills list` · `add <name>` · `remove` · `push` (add yours to the workspace) |
| **Share a folder** | `aos space link` · `push` · `pull` · `sync` (while it runs) · `status` · `diff` · `log` · `restore` · `resolve` · `claim` · `handoff` · `wait` |

**`aos integrations` is everything you can do through AgentOS, in one list:** AgentOS's own actions (tasks, CRM, memory, files: lowercase names like `tasks_list`) and your connected apps' (Gmail, Slack, Stripe: names like `GMAIL_SEND_EMAIL`). AgentOS holds the apps' logins.

Output is JSON when piped, so agents can use every command. Exit codes: `0` ok, `1` error, `2` waiting for a person's approval, `3` someone else has claimed it, `4` timed out.

Older names still work: `aos tools` and `aos call` run `aos integrations`, and `aos push`, `aos sync`… run `aos space push`, `aos space sync`….

## Skills

[`skills/`](skills) holds the AgentOS skills: `aos`, `agentos-setup`, `agentos-workspace`, `agentos-tasks`, `agentos-crm` and `agentos-meetings`. `aos setup` installs them into `~/.claude/skills` and `~/.agents/skills`. They are plain `SKILL.md` folders, so any agent that reads skills can use them.

## Claude Code plugin

```sh
claude plugin marketplace add devvcore/agentos-cli
claude plugin install agentos@agentos
```
The plugin bundles the MCP server and the skills, and Claude Code signs you in the first time it connects. `aos setup` does the same for every other agent.

## Install options

- Pin a version: `curl -fsSL https://tryagentos.net/install.sh | AOS_VERSION=v0.2.0 sh`
- Straight from GitHub: `curl -fsSL https://raw.githubusercontent.com/devvcore/agentos-cli/main/install.sh | sh`
- Without the script: `uv tool install git+https://github.com/devvcore/agentos-cli`
- No browser on this machine (CI, a server): set `AGENTOS_PAT` to a personal access token from AgentOS Settings.
- Uninstall: `aos setup --undo && aos logout && uv tool uninstall agentos-cli`

## Develop

```sh
uv venv && uv pip install -e '.[dev]' && .venv/bin/pytest -q
```

MIT licensed. [tryagentos.net](https://tryagentos.net)
