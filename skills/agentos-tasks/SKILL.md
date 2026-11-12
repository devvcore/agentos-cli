---
name: agentos-tasks
description: Read and update the AgentOS task board. Use when the person asks what is owed, what is overdue, to create or assign a task, to mark work done, or to log progress on a task.
---

# Tasks in AgentOS

A task is a commitment: somebody owes an outcome. Observations and status
updates are not tasks; they belong as a comment on the task they concern or a
note in a Drive document. Each task stands on its own; to hand off ongoing work,
write the outcome, what's left and the next owner on the task itself.

Run the tools below through `workspace_call`, as `agentos-workspace`
describes.

## Reading

- `tasks_list` with filters (`status`, `assignee`, `due_before`, `q`) for the
  board. Default results are open work, newest first.
- `tasks_get` with `task_id` for one task in full, including recent comments.

## Changing

- Before `tasks_create`, search with `tasks_list` and `q` for an existing task
  on the same outcome. Update that one instead of creating a duplicate.
- `tasks_create` needs a `title` that names the outcome, for example "Send
  Acme the revised SOW". Put context in `description`, and set `assignee`,
  `due_at` and `priority` when the person gave them.
- `tasks_update` changes title, status (`todo`, `in_progress`, `done`,
  `cancelled`), priority, assignee or due date in place.
- `tasks_comment` adds progress that is new information. The board rejects
  near-duplicate comments and repeated status nags; that is intended.
- `tasks_complete` with a `note` saying what was done. Only close work the
  person confirms is finished.

Confirm the change back to the person with the task's link.
