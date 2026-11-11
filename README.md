<p align="center"><strong>AgentOS CLI</strong>: put AgentOS in every coding agent on your computer, and use it from the terminal.</p>

```sh
curl -fsSL https://tryagentos.net/install.sh | sh
aos login      # sign in, or create your AgentOS account, in the browser
aos setup      # add AgentOS to Claude Code, Codex, Cursor, opencode, Gemini CLI…
```

`aos setup` finds the coding agents on your computer, lets you tick what to add (the AgentOS MCP server, the AgentOS skills, your workspace's skills, compression), shows you the exact change to each config file, backs it up, and applies it. Each agent then signs in to AgentOS once with OAuth; no tokens are written to disk. `aos setup --undo` takes it all out again.

## How the pieces fit

| | What it is | Do you need it? |
|---|---|---|
| **MCP server** | `aos setup` adds AgentOS to each coding agent's config so the agent can call AgentOS tools. Each agent signs in once. | Yes |
| **Skills** | `aos setup` installs the AgentOS skills so agents know how to use AgentOS and `aos`. | Yes |
| **`aos` command** | You and your agents use AgentOS from the terminal. | Yes |
| **Spaces** (`aos link`) | Share a folder with people and agents, with versions. Changes move when you run `aos push` / `aos pull`, or automatically while `aos sync` runs (`--background` keeps it going on a Mac). | Only for shared folders |
| **Capture** | Compression of large tool output, plus learning from your coding sessions. Separate tool, private preview. | No |

`aos status` shows all of it at a glance. `aos help <topic>` explains each part: `overview`, `setup`, `mcp`, `skills`, `spaces`, `capture`, `secrets`, `status`.

## What you can do with it

| | |
|---|---|
| **Account** | `aos login` · `aos whoami` · `aos logout` |
| **Your agents** | `aos agents` · `aos setup [--dry-run] [--yes] [--undo]` · `aos doctor` |
| **Skills** | `aos skills list` · `add <name>` · `remove` · `update` · `outdated` · `search` |
| **Work** | `aos context "Acme renewal"` · `aos tools --q invoice` · `aos call <any workspace tool> '{…}'` |
| **Connected apps** | `aos connect hubspot` · `aos integrations search\|describe\|call` |
| **Credentials** | `aos secrets set stripe_api_key` (hidden prompt or stdin) · `aos secrets list` · `aos http stripe_api_key https://api.stripe.com/v1/…` (AgentOS adds the key; it never reaches your computer) |
| **Spaces** | `aos link` · `pull` · `push` · `status` · `diff` · `restore` · `log` · `claim` · `release` · `handoff` · `wait` · `watch` |

Output is JSON when piped, so agents can use every command. Exit codes: `0` ok, `1` error, `2` waiting for a person's approval, `3` someone else has claimed it, `4` timed out.

## Skills

[`skills/`](skills) holds the AgentOS skills: `aos`, `agentos-setup`, `agentos-workspace`, `agentos-tasks`, `agentos-crm`, `agentos-meetings` and `agentos-spaces`. `aos setup` installs them into `~/.claude/skills` and `~/.agents/skills`. They are plain `SKILL.md` folders, so any agent that reads skills can use them.

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
