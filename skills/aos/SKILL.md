---
name: aos
description: "Use the aos command line to work in AgentOS from this computer: look up context, do things in AgentOS (tasks, CRM, memory) and in connected apps (Gmail, Slack, Stripe) without handling credentials, store keys, share a folder in a space, claim work, hand off and wait for other agents. Use when the person mentions AgentOS, a space, a handoff, or a connected app and you can run shell commands."
---

# aos: AgentOS from the terminal

`aos` is signed in as the person (`aos status`). If a command says you aren't signed in, ask the person to run `aos login`; never ask them for a token.

Output is JSON when piped, so parse it instead of scraping text. Exit codes: 0 ok, 1 error, 2 waiting for a human approval (not done), 3 someone else has claimed it, 4 timed out.

`aos status` shows sign-in, which agents have AgentOS, and this folder's space; `aos help <topic>` (overview, setup, call, integrations, space, secrets, skills) explains each part.

## Find out and do things
```sh
aos context "Acme renewal"                        # people, companies, history
aos call agentos tasks                            # AgentOS's own actions (tasks, CRM, memory…): search
aos call agentos tasks_list '{"status":"open"}'   # …run one
aos integrations                                  # the person's connected apps
aos call gmail send                               # an app's actions: search
aos call gmail send_email --describe              # what it does, its arguments
echo '{"recipient_email":"ada@example.com","subject":"Hi","body":"…"}' | aos call gmail send_email
```
An action runs only when its name matches exactly; otherwise `aos call` searches, so list first, then run. Apps run with the workspace's connection: no keys on this computer. A result with `queued_for_approval` (exit 2) is not done: tell the person what is waiting. If an app isn't connected, `aos connect <app>` gives the person a link.

## Keys
```sh
aos secrets list                                            # names only, never values
STRIPE_API_KEY=$(aos secrets get stripe_api_key) python report.py
echo -n "$VALUE" | aos secrets set stripe_api_key -d "Stripe key for billing reports"
```
Use `$(aos secrets get …)` inline, as above, so the value goes straight to the program and never into the conversation. Never print a value, write it to a file, or put it in an argument you echo. AgentOS records every read. If the person pastes a key, store it with `aos secrets set` (through stdin) and tell them it's saved. A "stored-credentials permission" error means the person runs `aos login --scope secrets` once.

## Work together in a space
```sh
aos space link whiteboard          # tie this folder to a space
aos space pull; aos space status   # get the latest, see local changes and claims
aos space claim canvas/sync -m "on it"     # tell others; exit 3 if taken
aos space push -m "canvas sync works"      # refuses if behind (pull first) or if someone else claimed a file
aos space handoff -m "tests next" --to bob # releases your claims, leaves a note
aos space wait --for handoff --to me --timeout 3600   # block until it's your turn; prints the event JSON
```
You appear in claims and the log as `user@host/<agent>:<session>` (for example `ada@laptop/claude-code:4c658885`), so two agents on one computer are different people to each other. `--as NAME` or `$AOS_AS` sets it by hand.

Files move on `aos space push` and `aos space pull`, or while `aos space sync` runs in a terminal (foreground only; Ctrl-C stops it). Never set up background sync of your own (LaunchAgents, cron, `nohup` loops, `*sync*.sh` scripts): macOS flags those as items from an unidentified developer, and the person didn't ask for them.

Pull never overwrites your edits: their copy lands next to yours as `<file>.theirs` (exit 1). Read both, merge by hand, `aos space resolve <file>`, then push.

Before you start something new, check whether it exists (`aos context`, `aos call agentos tasks_list`); when you stop, hand off with what's done, what's left and the next owner.

## Skills
`aos skills list` shows AgentOS and workspace skills; `aos skills add <name>` installs one for every agent on this computer; `aos skills push` adds the person's own skills to the workspace (only when they ask).
