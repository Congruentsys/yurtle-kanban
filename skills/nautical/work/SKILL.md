---
name: work
description: Find and start the next highest-priority expedition from the kanban board
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Read
argument-hint: "[EXP-XXX]"
---

# Pick Up Work

Find and start work on an expedition. If an expedition ID is provided ($ARGUMENTS), start that one. Otherwise, find the next highest-priority ready item.

## Steps

### 1. Check Current Work

First, check if already working on something:

```bash
yurtle-kanban list --status in_progress
```

If items are in progress, show them and ask if the user wants to continue or pick up new work.

### 2. Find Ready Work

If no specific expedition requested:

```bash
yurtle-kanban list --status ready
```

Show the top 5 ready items with their priorities.

### 3. Start Work

Once an expedition is selected (either from $ARGUMENTS or user choice):

```bash
# Claim it: one race-free commit on origin's default branch, so of two
# agents claiming one item exactly one wins. Use YOUR agent name, never a
# shared one (sessions on one machine share git user.name).
YURTLE_AGENT=<your-agent-name> yurtle-kanban claim EXP-XXX

# Create the expedition branch from the remote default branch (main here)
git fetch origin
git checkout -b expedition/exp-XXX-short-description origin/main
```

`claim` exits 0 when the item is yours (claimed now, or already yours). Exit 1
means refused (held by someone else, not ready, a gate or WIP limit) and exit 3
means another agent claimed it first: pick another item. Exit 4, 5 or 6 means
the remote could not be reached, was busy, or refused the push: nothing was
claimed, so retry or report it.

**IMPORTANT**: Expedition branches use the `expedition/exp-XXX-name` prefix and are never deleted (permanent memory).

### 4. Load Context

Read the expedition file to understand the work:

```bash
# Find and read the expedition file
cat kanban-work/expeditions/EXP-XXX*.md
```

Summarize:
- What needs to be done (Build Steps)
- Success criteria
- Dependencies
- Current status from Ship's Log

### 5. Ready to Work

Confirm the expedition is loaded and ready to begin implementation.
