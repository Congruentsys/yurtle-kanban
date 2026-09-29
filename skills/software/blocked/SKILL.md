---
name: blocked
description: Mark item as blocked with reason and optional unblock agent
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Read, Edit
argument-hint: "<FEAT-XXX> <reason> [--unblock-by agent-name]"
---

# Mark as Blocked

Mark an item as blocked with a clear reason so other agents know why and who can help.

## Required Arguments

- `FEAT-XXX` (or `BUG-XXX`, etc.) - The item ID to block
- `reason` - Why it's blocked (quote if multiple words)

## Optional Arguments

- `--unblock-by agent-name` - Which agent can unblock this

## Steps

> **hdd boards:** there `blocked` is the canonical name of `abandoned`, so
> `move … blocked` on an hdd hypothesis or experiment **abandons** it. Don't use this
> skill to pause hdd work; record a dependency (step 1a) or a comment instead.

### 1a. The blocker is another item: record a dependency

When the item waits on another item (FEAT-YYY), don't move it to blocked. Record the
dependency and leave the status alone (or move it back to `ready`):

```bash
yurtle-kanban update FEAT-XXX --add-dep FEAT-YYY
```

`yurtle-kanban blocked` then lists FEAT-XXX with FEAT-YYY under it until FEAT-YYY
is done, and nobody has to remember to unblock it.

### 1b. The blocker is not an item: move to blocked, reason in a comment

Only for a decision, hardware, an external party or anything else that isn't on a
board:

```bash
yurtle-kanban move FEAT-XXX blocked --agent <your-agent-name>
yurtle-kanban comment FEAT-XXX --agent <your-agent-name> --body-file - <<'EOF'
BLOCKED: [reason]
Can unblock: [agent-name or "anyone"]
EOF
```

### 2. Commit and Push

`update`, `move` and `comment` each commit the item file; push them:

```bash
git push origin HEAD
```

### 3. Confirm Block

Show:
- `yurtle-kanban blocked` (status- and dependency-blocked items, each once)
- Who can unblock
- Suggest notifying the unblocking agent

## Common Block Reasons

| Reason | Who Can Unblock |
|--------|-----------------|
| "Waiting for external API" | whoever manages the dependency |
| "Needs architecture decision" | tech lead |
| "Waiting for PR review" | any agent |
| "Blocked by FEAT-XXX" | nobody: record a dependency (step 1a) |
| "Needs stakeholder input" | project owner |
| "External dependency" | depends |

## Unblocking

- A dependency (1a): nothing to do; the item leaves `blocked` when FEAT-YYY is done.
  To drop the dependency instead: `yurtle-kanban update FEAT-XXX --rm-dep FEAT-YYY`.
- A status block (1b): add a comment saying what changed, then
  move it back: `yurtle-kanban move FEAT-XXX in_progress --agent <your-agent-name>`
