---
name: agentos-meetings
description: Find what was said and decided on calls the AgentOS notetaker captured. Use for meeting recaps, action items, "what did we agree with", or preparing for a follow-up call.
---

# Meetings in AgentOS

The AgentOS notetaker joins the team's calls and stores the summary, action
items, participants and transcript. Run the tools below through
`workspace_call`, as `agentos-workspace` describes.

- `meetings_search` with a `query` searches titles, summaries and transcripts.
  Narrow with `since_days`. Zero results means no indexed meeting matched that
  query in that date range, not that it was never discussed: broaden the terms
  or check what was captured. A `fuzzy: true` result matched only some of your
  words.
- `meetings_list` filters by `participant`, `company_id` or `status` when you
  know who was on the call rather than what was said.
- `meetings_get` with a `meeting_id` returns one meeting in full. Pass
  `include_transcript` only when the summary does not answer the question;
  transcripts are long and page with `transcript_offset`.

When preparing for a call, combine the last meetings with the company's CRM
record and open tasks (`context` with the company as `subject`) and lead with
what is still open. Quote decisions and commitments as stated, with the
meeting's date and link.
