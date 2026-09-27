---
name: expedition
description: Create a new expedition with atomic ID allocation and push
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Write, Read
argument-hint: "<title>"
---

# Create New Expedition

Create a new expedition with atomically allocated ID to prevent conflicts between agents.

## Steps

### 1. Create Expedition (Atomic)

**Use `create --push`** — this is a single atomic command that allocates the ID, creates the file, commits, and pushes:

```bash
yurtle-kanban create expedition "$ARGUMENTS" --push --priority medium
```

This atomically: fetches latest → allocates next ID → creates file → commits → pushes.
If another agent pushed first, it retries with a new ID. No race window.

**Options:**
- `--priority low|medium|high|critical`
- `--assign <name>` (sets who holds it; never defaulted)
- `--body-file - <<'EOF'` (the body, from a quoted heredoc — the shell expands nothing)
- `--tags tag1,tag2`

If no remote is configured, the command still works — it commits locally without pushing.

### 2. Flesh Out the Expedition File

Read the created file and fill in the template sections:

```bash
yurtle-kanban show EXP-XXX
```

Edit the file to add:
- Problem statement
- Solution approach
- Build steps / phases
- Success criteria
- Files to modify
- Ship's log entry

### 3. Confirm Creation

Show the created expedition and suggest next steps:
- Review and fill in details
- Set priority and assignee if not already set
- Add dependencies if any
- Run `/work EXP-XXX` to start working on it

## Advanced: Manual ID Allocation

If you need the ID before creating the file (e.g., to reference it in other files first):

```bash
yurtle-kanban next-id EXP --json
# Returns: {"success": true, "id": "EXP-720", "prefix": "EXP", "number": 720}
```

Then create and push the file manually. But `create --push` is preferred.
