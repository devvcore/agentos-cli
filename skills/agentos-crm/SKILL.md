---
name: agentos-crm
description: Look up and add companies, contacts and pipeline deals in the AgentOS CRM. Use when the person asks about a client, prospect, account, contact, or the state of a deal.
---

# CRM in AgentOS

The CRM is the team's shared memory of clients. Agents and people read the
same records, so keep them accurate.

Run the tools below through `workspace_call`, as `agentos-workspace`
describes.

## Looking up

- `crm_companies_search` or `crm_contacts_search` with a `query` (name,
  domain, email). Then `crm_companies_get` or `crm_contacts_get` by `id`.
- For the full picture on one account, call `context` with the company as the
  `subject`: it pulls notes, open tasks and meetings together.
- `crm_pipelines_list` names the pipelines; `crm_pipelines_get` with a
  `pipeline_id` lists every deal in it with its stage.

## Adding

- Search first. Create only when no record matches by name or domain.
- `crm_companies_create` needs a `name`; add `domain` whenever known, since it
  is what prevents duplicates later.
- `crm_contacts_create` needs a `name`; add `email`, `role`, and the
  `company_id` from the company record.

Do not paste internal ids into notes; the records link themselves.
