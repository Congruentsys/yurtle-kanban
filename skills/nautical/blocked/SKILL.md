---
name: blocked
description: Mark expedition as blocked with reason and optional unblock agent
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Read, Edit
argument-hint: "<EXP-XXX> <reason> [--unblock-by agent-X]"
---

# Mark as Blocked

Mark an expedition as blocked with a clear reason so other agents know why and who can help.

## Required Arguments

- `EXP-XXX` - The expedition ID to block
- `reason` - Why it's blocked (quote if multiple words)

## Optional Arguments

- `--unblock-by agent-X` - Which agent can unblock this

## Steps

> **hdd boards:** there `blocked` is the canonical name of `abandoned`, so
> `move … blocked` on an hdd hypothesis or experiment **abandons** it. Don't use this
> skill to pause hdd work; record a dependency (step 1a) or a comment instead.

### 1a. The blocker is another item: record a dependency

When the expedition waits on another item (EXP-YYY), don't move it to blocked. Record the
dependency and leave the status alone (or move it back to `ready`):

```bash
yurtle-kanban update EXP-XXX --add-dep EXP-YYY
```

`yurtle-kanban blocked` then lists EXP-XXX with EXP-YYY under it until EXP-YYY
is done, and nobody has to remember to unblock it.

### 1b. The blocker is not an item: move to blocked, reason in a comment

Only for a decision, hardware, an external party or anything else that isn't on a
board:

```bash
yurtle-kanban move EXP-XXX blocked --agent <your-agent-name>
yurtle-kanban comment EXP-XXX --agent <your-agent-name> --body-file - <<'EOF'
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
| "Waiting for GPU training to complete" | agent-a, agent-c |
| "Needs architecture decision" | agent-b, agent-d, captain |
| "Waiting for PR review" | any agent |
| "Blocked by EXP-XXX" | nobody: record a dependency (step 1a) |
| "Needs Captain input" | captain |
| "External dependency" | depends |

## Unblocking

- A dependency (1a): nothing to do; the expedition leaves `blocked` when EXP-YYY is done.
  To drop the dependency instead: `yurtle-kanban update EXP-XXX --rm-dep EXP-YYY`.
- A status block (1b): add a comment saying what changed, then
  move it back: `yurtle-kanban move EXP-XXX in_progress --agent <your-agent-name>`
