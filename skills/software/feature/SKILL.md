---
name: feature
description: Create a new feature with atomic ID allocation and push
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Write, Read
argument-hint: "<title>"
---

# Create New Feature

Create a new feature with atomically allocated ID to prevent conflicts between agents.

## Steps

### 1. Create Feature (Atomic)

**Use `create --push`** — this is a single atomic command that allocates the ID, creates the file, commits, and pushes:

```bash
yurtle-kanban create feature "$ARGUMENTS" --push --priority medium
```

This atomically: fetches latest → allocates next ID → creates file → commits → pushes.
If another agent pushed first, it retries with a new ID. No race window.

**Options:**
- `--priority low|medium|high|critical`
- `--assign <name>` (sets who holds it; never defaulted)
- `--body-file - <<'EOF'` (the body, from a quoted heredoc — the shell expands nothing)
- `--tags tag1,tag2`

If no remote is configured, the command still works — it commits locally without pushing.

### 2. Flesh Out the Feature File

Read the created file and fill in the template sections:

```bash
yurtle-kanban show FEAT-XXX
```

Edit the file to add:
- Problem statement
- Acceptance criteria
- Implementation plan
- Files to modify
- Change log entry

### 3. Confirm Creation

Show the created feature and suggest next steps:
- Review and fill in details
- Set priority and assignee if not already set
- Add dependencies if any
- Run `/work FEAT-XXX` to start working on it

## Advanced: Manual ID Allocation

If you need the ID before creating the file (e.g., to reference it in other files first):

```bash
yurtle-kanban next-id FEAT --json
# Returns: {"success": true, "id": "FEAT-042", "prefix": "FEAT", "number": 42}
```

Then create and push the file manually. But `create --push` is preferred.
