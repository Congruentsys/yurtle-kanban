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

### 1. Move to Blocked Status

```bash
yurtle-kanban move FEAT-XXX blocked --agent <your-agent-name>
```

### 2. Update Item File

Add a blocked section to the item file:

Find the item file and add after the frontmatter:

```markdown
> **BLOCKED**: [reason]
> **Since:** YYYY-MM-DD
> **Can unblock:** [agent-name or "anyone"]
```

Also add a Change Log entry:

```markdown
### YYYY-MM-DD: BLOCKED

**Reason:** [reason]
**Can unblock:** [who]
**Context:** [any additional context]
```

### 3. Commit and Push

```bash
git add kanban-work/features/FEAT-XXX*.md
git commit -m "blocked(feat-XXX): [short reason]

Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin HEAD
```

### 4. Confirm Block

Show:
- Current blocked items count
- Who can unblock
- Suggest notifying the unblocking agent

## Common Block Reasons

| Reason | Who Can Unblock |
|--------|-----------------|
| "Waiting for external API" | whoever manages the dependency |
| "Needs architecture decision" | tech lead |
| "Waiting for PR review" | any agent |
| "Blocked by FEAT-XXX" | whoever finishes that feature |
| "Needs stakeholder input" | project owner |
| "External dependency" | depends |

## Unblocking

When you unblock an item:

1. Remove the `> **BLOCKED**` section from the file
2. Add Change Log entry: "Unblocked: [what changed]"
3. Move status: `yurtle-kanban move FEAT-XXX in_progress --agent <your-agent-name>`
