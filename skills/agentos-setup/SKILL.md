---
name: agentos-setup
description: Initialize AgentOS. Use when the person says "set up AgentOS", "move everything to AgentOS", "init the workspace" or "get our company running there". Walks through it in order - look, plan, one approval, connect, load memory, databases, bots, apps, hand-off - with workspace_init for the approval and the finished-state check.
---

# Initialize AgentOS

The person wants their company running in AgentOS: the company in memory, a
few bots with real jobs, what the business tracks in databases that stay
current, two or three apps to review them, and nothing left running on
anyone's laptop. They approve once. Everything else is yours to do, in this
order. `workspace_init` does two things: it puts your plan in front of the
person as one approval card, and it checks the finished state. A connection
that does not list a tool named here directly runs it through `workspace_call`;
`workspace_tools` returns its current schema.

## 0. On the person's own computer: install the AgentOS CLI

Running on the person's computer with a shell (Claude Code, Codex, Cursor's
agent, Gemini CLI, opencode), not as an AgentOS bot? Install `aos` first, so
every coding agent there gets AgentOS and its skills:

```sh
curl -fsSL https://tryagentos.net/install.sh | sh
aos setup --yes
```

`aos setup` adds the AgentOS MCP server and the AgentOS skills to each agent
it finds, and backs up every file it changes. Each agent then signs in once
with OAuth. Tell the person before you run it, since it edits their agents'
settings; `aos setup --undo` takes it out. Afterwards `aos context`,
`aos call <tool>`, `aos connect <app>` and `aos secrets set <name>` work from the
shell too. Skip this step when there is no shell or it isn't their computer.

## 1. Look

Call `workspace_init` with no arguments for the finished-state checklist, and
`workspace_map` for the bots, databases, apps and connections already here.
Never duplicate a bot, database or app that exists: extend it.

## 2. Plan, and get one yes

Send `workspace_init` with `plan`, shaped like this:

```json
{"summary": "Finance run by Ledger; invoices in one database with a receivables app.",
 "bots": [{"name": "Ledger", "area": "finance", "owner": "<a member's name or email>",
           "job": "Keep receivables current and tell the owner what is overdue.",
           "connections": ["gmail"],
           "routines": [{"what": "Monday note: overdue invoices and who to chase", "cron": "0 8 * * 1"}],
           "watches": [{"what": "Payment processor webhook", "url": true, "then": "Mark the invoice paid"}],
           "first_task": "List this month's unpaid invoices from email."}],
 "databases": [{"name": "Invoices", "purpose": "every invoice we sent and whether it is paid",
                "source": {"provider": "gmail", "what": "invoice emails"}, "owner_bot": "Ledger",
                "feed": {"kind": "bot_routine", "cron": "0 7 * * *", "how": "Read new invoice emails; add or update rows."},
                "tables": [{"table": "Invoices", "fields": [{"name": "Customer"}, {"name": "Amount", "type": "number"},
                            {"name": "Due", "type": "date"}, {"name": "Paid", "type": "checkbox"}]}]}],
 "apps": [{"name": "Receivables", "database": "Invoices", "purpose": "What is owed and overdue", "template": "dashboard"}],
 "documents": [{"title": "How we work", "reader": "anyone new, in their first week"}],
 "connections": [{"provider": "gmail", "why": "invoices arrive by email"}],
 "workflows": [{"id": "<each enabled scheduled workflow's id>", "action": "switch_off", "bot": "Ledger"}]}
```

The rules, each from what went wrong when an agent set a workspace up alone:

- At most five bots, one per real area of the business. Each has an owner who
  is a member, a one-sentence job, and a routine or watch. A routine is a paid
  model turn: at most once a day until someone uses its output.
- One database per real data source, never a copy of another database, the
  CRM or tasks. Each has a purpose, a source (a service a link can connect,
  or `people` for rows people enter in its app), an owner bot, and a feed that
  runs inside AgentOS: `bot_routine` (the owner bot's routine; do not list it
  again in its routines), `workflow` (scheduled, no model step; name it) or
  `people`. It must stay fresh: two feed intervals, or `fresh_within_hours`.
- Apps only over planned databases: two or three.
- At most five Library documents, ones a person will read (operating context,
  runbooks, policies). Reference material goes to memory, not the Library.
- CRM records (companies, contacts, deals) live in the CRM, never as tasks.
- Every enabled scheduled workflow gets `keep` (a fixed pipeline with no
  model step) or `switch_off` (a bot routine does it now).
- A plan sent after one was approved carries the whole plan, changes included.

A refusal lists every problem: fix them all and send the whole plan again.
Submit the plan from a running turn in the proposing bot's chat. Accepted,
the person gets one approval card in that conversation: tell them in a line
and give them `review_url`. Without a chat, submission returns `NoChatToAsk`
and queues nothing; the checklist and guide can still be read anywhere.
One yes records the plan; it does not create its bots, databases or apps.

Connected from outside AgentOS (Claude Code, Codex, ChatGPT, Cursor)? There
is no card and no yes: the plan is applied at once. Its bots, databases and
app drafts are made, and `applied` lists each with its id and link. Tell the
person in your conversation exactly what was made, then do `still_to_do`.
This needs the "Set up your workspace" permission; without it nothing is made.

## 3. While they decide: connect and load the company

- Give the person a `connect_link` for each planned source not connected.
- Load the company into memory: batch your records with
  `memory_ingest records=[...]` (up to 100 per call, never one call each);
  files go through `memory_ingest_file`. Who the company is, its people,
  customers, partners, products, decisions and how things work: one record
  per thing, raw and complete, with a stable id. Ask `memory_recall` a basic
  question ("who is our biggest customer") to see it answers.
- Put companies, contacts and deals in the CRM now (`crm_` tools), before
  any bot starts, so no bot loads them twice.

## 4. After the yes, carry it out

`workspace_init` with `status: true` says `plan: "approved"`. Then:

- **Databases:** `databases_build` once per planned table (`new_database` for
  the first table, `database` for the next), and load the first real rows
  (`rows`, `app_db_import_csv` or `app_db_execute`).
- **Bots,** once the company is in memory: `agents_create` with the bot's name
  and job (`request_key` `init:<name>`); a bot that exists already is
  extended, not created again. Then `bot_message` each one its part: who it
  answers to, the databases it keeps current (with their ids, from
  `workspace_map`) and how, its routines and watches to set (a watch with
  `url: true` is a watch URL it makes), and its first task. Tell it the
  memory and CRM are loaded and the planned apps exist: read them, never
  create them again.
- **Workflows:** `workflow_update` with `enabled: false` for each one switched
  off; a `workflow` feed is yours to build with `workflow_create`: scheduled,
  no model step, named as in the plan.
- **Apps:** each on its database from a kit template: `app_update` with the
  database id as `app_id` and the template, then `app_build` and
  `app_preview`. Without app tools, ask the owning bot.

## 5. Leave nothing outside

Anything that must keep running goes to a bot routine or a watch URL the bot
owns, never a job on the person's own machine (cron, launchd, a script left
running) or their personal key: it stops when the laptop sleeps. Ask the
person what runs on their machine today, and move it.

## 6. Hand off

Write a short hand-off to Drive with `fs` (`write agentos-setup.md`): what was set
up, which bot owns what, what is still missing. Call `workspace_init` with
`status: true` until `finished` is true (each item says what it is waiting
on), then tell the person in a few lines what is running, where to look, and
what is left for them.
