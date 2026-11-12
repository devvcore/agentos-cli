---
name: agentos-workspace
description: Answer questions from the person's AgentOS workspace (documents and files, meetings, CRM, tasks) with citations. Use whenever the question is about their company, clients, projects, decisions, or who is doing what.
---

# Answering from AgentOS

AgentOS is the person's shared workspace: the same documents, CRM, task board and
meeting notes their team and agents use. Answer from it before answering from
general knowledge, and say so when it has nothing.

## Calling AgentOS tools

The connection lists a few tools directly: `context`, `source_read`,
`rules_list`,
`integration_search`, `workspace_init`, `workspace_tools` and `workspace_call`.
`source_read` opens a fact (`fact_...`) or message (`evt_...`) that `context`
cited or left out; `refs=[...]` opens up to 10 at once. `context` leads with
`now` (what is true now, each line with its ref) and `replaced` (what changed).
`rules_list` lists the rules in force for an agent, person or client.
Every other tool these skills name
(`fs`, `drive_list`, `tasks_list`, `meetings_get`, the CRM tools) runs through
`workspace_call`: put the tool's name in `name` and its arguments, as a JSON
string, in `arguments`. `workspace_tools` with that `name` returns the exact
schema first.

If your host lists one of those names as a tool of its own and calling it
fails with "Unknown tool", the host is holding an old copy of the tool list.
Call the same name through `workspace_call` instead. There is no wiki:
documents are markdown files in Drive, read and written with `fs`.

## Find it

1. Start with `context` and a short `subject` (a company, project, or person)
   plus the `question`. It searches Drive, CRM, tasks, meetings and
   messages at once and returns sources.
2. Go narrower when you know the surface:
   - documents: `fs` with `grep <words>` or `find <name>` over Drive, then
     `fs` with `cat <path>` for the full document;
   - calls: `meetings_search` or `meetings_list`, then `meetings_get`;
   - companies and people: `crm_companies_search`, `crm_contacts_search`, then
     the matching `_get`;
   - owed work: `tasks_list` (and `tasks_get` for one task's history);
   - attachments: `drive_list`, then `drive_read`.
3. `skills_search` and `skills_get` hold the team's own procedures. Read the
   relevant one before doing work the team has a written way of doing.

## Answer

- Cite what you used. Results carry an `app_url`; link it rather than
  inventing a URL.
- Quote numbers, dates and decisions as written. If two sources disagree, show
  both and say which is newer.
- If nothing relevant comes back, say that plainly and suggest who or what
  would know. Do not fill the gap from general knowledge as if it were the
  workspace's answer.

## Writing back

Only write when the person asks you to record something. Prefer adding to what
exists: read the right document with `fs cat`, then `fs append` to it or `fs
write` it back with what was there kept; or a comment on the existing task.
