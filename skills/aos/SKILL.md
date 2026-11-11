---
name: aos
description: "Use the aos command line to work in AgentOS from this computer: look up context, call workspace tools, use the workspace's connected apps without handling credentials, share files in a space, claim work, hand off and wait for other agents. Use when the person mentions AgentOS, a space, a handoff, or a connected app such as Notion, Stripe or Gmail and you can run shell commands."
---

# aos: AgentOS from the terminal

`aos` is signed in as the person (`aos whoami`). If a command says you aren't signed in, ask the person to run `aos login`; never ask them for a token.

Output is JSON when piped, so parse it instead of scraping text. Exit codes: 0 ok, 1 error, 2 waiting for a human approval (not done), 3 someone else has claimed it, 4 timed out.

`aos status` shows sign-in, which agents have AgentOS, capture and this folder's space in one view; `aos help <topic>` (overview, setup, mcp, skills, spaces, capture, secrets) explains each part. Capture (compression and work capture) is a separate private-preview tool: don't try to install it.

## Find things and call tools
```sh
aos context "Acme renewal"                 # people, companies, Projects, history
aos tools --q invoice                      # find workspace tools
aos tools --name tasks_list                # one tool's full schema
aos call tasks_list '{"status":"open"}'    # call it (JSON args, or --args-file f.json, or stdin)
```
A result with `queued_for_approval` (exit 2) is not done: tell the person what is waiting.

## Connected apps (no credentials on this computer)
```sh
aos integrations search "stripe invoices"
aos integrations describe <slug>
echo '{"limit":5}' | aos integrations call <slug>
```
AgentOS runs the call with the workspace's connection. Never put keys in arguments. If an app isn't connected, `aos call connect_link '{"service":"stripe"}'` gives the person a link to connect it.

## Keys and logins
```sh
aos secrets list                                   # names only, never values
aos secrets set stripe_api_key --host api.stripe.com -d "Stripe key for billing reports"   # value from a hidden prompt or stdin
aos http stripe_api_key https://api.stripe.com/v1/invoices -q limit=5   # AgentOS adds the key; it never reaches you
aos connect hubspot                                # apps with their own sign-in
```
Never pass a key as an argument, print it, or write it to a file. If the person pastes one, store it with `aos secrets set` (piped through stdin) and tell them it's saved. A "stored-credentials permission" error means the person runs `aos login --scope secrets` once.

## Work together in a space
```sh
aos link whiteboard          # tie this folder to a space
aos pull; aos status         # get the latest, see local changes and claims
aos claim canvas/sync -m "on it"     # tell others; exit 3 if taken
aos push -m "canvas sync works"      # refuses if behind (pull first) or if someone else claimed a file
aos handoff -m "tests next" --to bob # releases your claims, leaves a note
aos wait --for handoff --to me --timeout 3600   # block until it's your turn; prints the event JSON
```
You appear in claims and the log as `user@host/<agent>:<session>` (for example `ada@laptop/claude-code:4c658885`), so two agents on one computer are different people to each other: a claim made in another session blocks you too. `--as NAME` or `$AOS_AS` sets it by hand.

Files move on `aos push` and `aos pull`. If the person wants it automatic, use `aos sync` (foreground) or `aos sync --background` (macOS; it explains the "Background Items Added" notice first, and `aos sync --stop` turns it off). Never write your own sync job (LaunchAgents, cron, `nohup` loops, `*sync*.sh` scripts): macOS flags those as items from an unidentified developer.

Pull never overwrites your edits: their copy lands next to yours as `<file>.theirs` (exit 1). Read both, merge by hand, delete the `.theirs`, then push.

## Skills
`aos skills list` shows AgentOS and workspace skills; `aos skills add <name>` installs one for every agent on this computer.
