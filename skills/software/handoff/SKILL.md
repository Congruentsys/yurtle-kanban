---
name: handoff
description: Hand off work to another agent with full context
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Write, Read
argument-hint: "<FEAT-XXX> <agent-name> [notes]"
---

# Hand Off Work

Create a handoff note so the receiving agent has full context when they pick up the work.

## Required Arguments

- `FEAT-XXX` (or `BUG-XXX`, etc.) - The item ID to hand off
- `agent-name` - The target agent
- `[notes]` - Optional additional context

## Steps

### 1. Create Handoffs Directory

```bash
mkdir -p kanban-work/handoffs
```

### 2. Gather Current State

Read the item file and check git status:

```bash
# Show item details
yurtle-kanban show FEAT-XXX

# Check current branch and uncommitted changes
git status
git log --oneline -5
```

### 3. Create Handoff Note

Create `kanban-work/handoffs/FEAT-XXX-to-agent-YYYYMMDD.md`:

```markdown
---
item: FEAT-XXX
from: [current-agent]
to: [target-agent]
created: YYYY-MM-DD HH:MM
status: pending
---

# Handoff: FEAT-XXX

**From:** [Current Agent]
**To:** [Target Agent]
**Time:** YYYY-MM-DD HH:MM

## Current State

**Branch:** `feature/feat-XXX-...`
**Last Commit:** [commit message]
**Kanban Status:** [status]

## What's Done

- [x] [Completed item 1]
- [x] [Completed item 2]

## What's Needed

- [ ] [Remaining item 1]
- [ ] [Remaining item 2]

## Key Context

[Important decisions, gotchas, things to watch out for]

## Files to Focus On

- `path/to/important/file.py` - [why it matters]
- `path/to/another/file.py` - [why it matters]

## How to Continue

1. Take the item over: `yurtle-kanban claim FEAT-XXX --take-over --agent <receiver>`
2. Checkout branch: `git checkout feature/feat-XXX-...`
3. [Next step]
```

### 4. Update Item File

Add a Change Log entry to the item:

```markdown
### YYYY-MM-DD: Handed off to [Agent]

[Summary of what was done and what remains]
```

### 5. Commit and Push

```bash
git add kanban-work/handoffs/FEAT-XXX-*.md
git add kanban-work/features/FEAT-XXX*.md
git commit -m "handoff(feat-XXX): Hand off to [agent]

- What was completed
- What remains

Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin HEAD
```

### 6. Update Kanban (Optional)

If the work is paused, optionally move to a waiting status:

```bash
# Only if work is blocked waiting for the other agent
yurtle-kanban move FEAT-XXX blocked
```

### 7. Confirm Handoff

Show summary:
- Handoff note location
- What the receiving agent should do, starting with taking the item over:
  `yurtle-kanban claim FEAT-XXX --take-over --agent <receiver>` (recorded as
  `kb:takenOverFrom`; the receiver runs it, as themselves)
- Remind to run `/sync` on next session
