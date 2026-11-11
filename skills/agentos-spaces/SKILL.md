---
name: agentos-spaces
description: Reuse AgentOS Projects and resume, checkpoint or sync shared work across agents and sessions. Use when the user asks to work in a Project, preserve ongoing work or hand off its artifacts and next action.
---

# AgentOS Projects

Discover current server schemas; Project lifecycle is supported when projects_edit and projects_lifecycle are advertised. Use only advertised tools/states. For the full procedure, read the Projects skill when available.

A Project is durable shared work, context, artifacts and handoff across agents/sessions. A Task is one owed outcome and can stand alone. A lifecycle Project (space_id) and a task-group Project (project_id, from projects_create) are separate records; never combine them by matching id or name.

Reuse first: projects_find, then projects_get; resolve ambiguity explicitly. Create only for useful ongoing work or handoff with a clear purpose/audience. Never create one Project per task/session/trivial answer. Respect the user's destination, custom instructions, role and local repository.

On pickup, follow pagination and read canonical outputs/current sources with their native tools. Treat checkpoints as historical claims; reconcile newer evidence and accepted decisions. Preserve others' contributions; continue in your own author/task/write scope.

Sync meaningful changes, pauses, handoff and completion; skip unchanged status. Save real outputs first, projects_link references, then checkpoint accomplished work, decisions, output/evidence refs, current blockers/remaining work and one concrete next action. Deliverables stay outside checkpoints; exclude secrets, private evidence, prompts, transcripts and reasoning.

projects_sync without contribution only reads. Save with fresh own_progress_version as expected_source_version, same scope and version-bound idempotency_key. Retry the original request/key/version after uncertainty; conflicts save nothing: pull, reconcile, use a new key. Only receipts confirm saves; checkpoints neither complete tasks nor transfer ownership.

Final handoff needs usable outcomes, verification, unresolved work and next owner/action. Use advertised projects_lifecycle within authorization: complete records outcome/handoff; archive preserves prior state; restore returns to it; reopen resumes completed work. Never assume deletion, automatic cleanup, unlimited history or filesystem snapshots. Project access grants no source access, credentials, budget or outbound approval; report precise blockers.

## Procedure

Tool names here are AgentOS server names. Claude Code exposes them as
`mcp__agentos__<name>`. A connection that does not list a tool directly runs
it through `workspace_call`; `workspace_tools` returns its current schema. This skill adds no tools,
permissions or budget. Stay in the host's local repository for local work.

1. **Choose a destination.** A single fix or answer can stay a Task or chat.
   A task-group Project (project_id) groups commitments; it is not a lifecycle Project (space_id). For work
   whose context, outputs and handoff should survive across agents, search
   accessible Projects, compare purposes and read the selected Project. Search
   `projects_find(state=all)` for completed/archived work before starting anew.
   `has_more=true` is a bounded list, not proof of absence; narrow by an exact
   known title or retrieve more context. Reuse
   its returned ID. Ambiguous titles require a choice; never merge silently.
   Preview a new Project with `projects_start` and a clear title/purpose/audience;
   create explicitly with `save=true` only within the requested work. Default
   restricted access is not permission to publish private evidence.
2. **Resume from evidence.** Call `projects_get` and follow `next_actions.continue`
   until null for linked contents. The overview task list is separately bounded;
   null contents continuation does not mean the full task board was read. Use
   canonical linked task reads or `tasks_list` for the needed work scope. Use returned read calls to inspect actual canonical outputs,
   current task state and sources. A checkpoint is another worker's report,
   not proof of deployment, completion, approval or current truth. Compare it
   with newer sources and accepted decisions before continuing. Leave their
   contribution intact. When using a task/write scope, pass the same selectors
   on pickup and save; link the actual task as a source when the schema requires
   an explicit association. A Project's task grouping is not that association.
3. **Publish actual work.** Use the output's native write tool first. For a
   document, `fs write <path>.md` creates standalone Markdown in Drive; use a
   `private/` path for a restricted destination as directed by the schema.
   `projects_link(relation=output, ref={kind, id})` links the returned canonical
   reference. A PR or external link does not establish its contents or grant
   access; inspect it separately. Do not treat a local filename as an uploaded
   artifact. Linking a file never copies or shares it. Keep the usable
   deliverable out of checkpoint.summary.
4. **Checkpoint changed work.** Pull the current own progress version. Call
   `projects_sync` with a `contribution` containing a compact `checkpoint`, the
   same `task_id`/`write_scope` if selected, `expected_source_version` from
   `own_progress_version`, and `idempotency_key=<version>:<new-request-id>`.
   `projects_sync` without contribution only reads. Use factual accomplishment,
   actual output/source IDs, observed/proposed/accepted decisions, current
   blockers and a concrete next action. Do not copy private sources into
   a shared summary. Pause, context loss, transfer and final delivery are useful
   checkpoints; unchanged routine status is not.
5. **Recover honestly.** An uncertain response needs the original request,
   expected version and key, not a fresh write. A confirmed saved receipt
   survives a later pull failure. Conflicts save nothing: use the returned
   read-only recovery calls, reconcile current sources, then submit a new
   contribution with the new expected version and a new version-bound key.
   A receipt proves a checkpoint was saved, not that a task was completed,
   deployment happened or another worker accepted ownership.
6. **Finish and hand off.** Before lifecycle completion, save/link the final
   usable output and checkpoint actual verification, open risks and next
   owner/action. Distinguish verified completion from a proposal. Use only
   lifecycle transitions advertised by the implemented backend, within the
   user's authorization. `projects_lifecycle` requires Project owner/admin writing
   permission and `expected_version` equal to the freshly pulled Project
   `source_version`. This is different from the contribution's
   `own_progress_version`. `action=complete` moves active to completed and
   requires `outcome`, with optional `handoff`; `archive` preserves its prior
   active/completed state; `restore` returns to that prior state; `reopen` moves
   completed to active for further work. Completed/archived Projects remain
   readable and reject links/checkpoint saves: restore/reopen explicitly before
   editing. `projects_edit` edits title/purpose with the same Project version
   requirement; it does not change audience. Do not delete for cleanup, assume auto-archive,
   promise infinite revisions or claim an executable environment snapshot.
   If a lifecycle operation is absent, leave the final handoff in the existing
   Project and report the missing operation; do not simulate it via Projects.

## Checkpoint example

This is a payload template, not a saved receipt. Replace every placeholder
with a value actually returned by a tool. List verification in accomplished;
put blockers and the next action in remaining. Keep proposed decisions separate
from accepted decisions. Source refs must satisfy the discovered schema;
arbitrary URLs and inaccessible private sources are not checkpoint evidence.

```json
{
  "summary": "Draft review completed; owner decision remains",
  "accomplished": ["Saved the revised draft to the linked Library output", "Verified the two examples against current source records"],
  "decisions": [{"kind": "proposed", "text": "Use the revised opening after owner review"}],
  "remaining": ["Blocked on owner choice of opening", "Next: review the owner's answer, update the same output, and verify the final draft"],
  "source_refs": ["<returned-library-output-id>"]
}
```

Keep repository URL, branch and changed files in the optional `repo` fields
when useful, never credentials embedded in URLs. A branch/file list is context,
not a backup. The existing Claude Stop hook's rolling repository log is not a
Project checkpoint and does not imply sync, handoff or completion.
