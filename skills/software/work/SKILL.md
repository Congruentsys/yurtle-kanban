---
name: work
description: Find and start the next highest-priority item from the kanban board
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Read
argument-hint: "[FEAT-XXX|BUG-XXX]"
---

# Pick Up Work

Find and start work on an item. If an item ID is provided ($ARGUMENTS), start that one. Otherwise, find the next highest-priority ready item.

## Steps

### 1. Check Current Work

First, check if already working on something:

```bash
yurtle-kanban list --status in_progress
```

If items are in progress, show them and ask if the user wants to continue or pick up new work.

### 2. Find Ready Work

If no specific item requested:

```bash
yurtle-kanban list --status ready
```

Show the top 5 ready items with their priorities.

### 3. Start Work

Once an item is selected (either from $ARGUMENTS or user choice):

```bash
# Move to in_progress
yurtle-kanban move FEAT-XXX in_progress

# Create feature branch from main
git checkout main
git pull origin main
git checkout -b feature/feat-XXX-short-description
```

**IMPORTANT**: Feature branches use the `feature/feat-XXX-name` prefix.

### 4. Load Context

Read the item file to understand the work:

```bash
# Find and read the item file
yurtle-kanban show FEAT-XXX
```

Summarize:
- What needs to be done (Implementation Plan)
- Acceptance criteria
- Dependencies
- Current status from Change Log

### 5. Ready to Work

Confirm the item is loaded and ready to begin implementation.
