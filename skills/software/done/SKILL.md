---
name: done
description: Complete current work item - run tests, commit, push, and update kanban status
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Bash(pytest *), Bash(python3 *), Bash(gh *)
argument-hint: "[FEAT-XXX|BUG-XXX]"
---

# Complete Work

Finish the current work item: run tests, commit changes, push to remote, and update kanban status.

## Steps

### 1. Identify Current Work

```bash
# Check current branch
git branch --show-current

# Check in-progress items
yurtle-kanban list --status in_progress
```

If $ARGUMENTS provided, use that item. Otherwise, infer from branch name or ask.

### 2. Run Tests

Run appropriate tests based on what was changed:

```bash
# Unit tests for modified modules
pytest tests/ -v

# Integration tests if applicable
pytest tests/ -v -k "integration"
```

Report test results. If tests fail, DO NOT proceed - fix issues first.

### 3. Commit Changes

```bash
# Check what's changed
git status
git diff --stat

# Stage and commit with item reference
git add -A
git commit -m "feat(feat-XXX): Brief description

- What was implemented
- Key changes

Co-Authored-By: Claude <noreply@anthropic.com>"
```

### 4. Push to Remote

```bash
git push origin HEAD
```

### 4b. Version Check (for significant changes)

If this work adds features or fixes bugs, consider bumping the version:

```bash
# Check current version
grep version pyproject.toml | head -1

# For bug fixes: bump patch (1.1.0 → 1.1.1)
# For new features: bump minor (1.1.0 → 1.2.0)
# For breaking changes: bump major (1.1.0 → 2.0.0)
```

If version bump is appropriate, use `/release patch` after merging to main.

### 5. Create PR (required for feature branches)

Create PR so other agents can see the work is ready for review:

```bash
gh pr create --title "feat(feat-XXX): Title" --body "## Summary
- What this PR does

## Test Plan
- How it was tested

## Work Item
FEAT-XXX: [Title]"
```

### 6. Update Kanban Status

**IMPORTANT**: This makes the work visible to other agents:

```bash
# Move to review (for changes needing review)
yurtle-kanban move FEAT-XXX review

# Or if self-contained, fully tested, and ready to merge:
yurtle-kanban move FEAT-XXX done
```

### 7. Show Next Work

```bash
yurtle-kanban list --status ready
```

Suggest the next highest-priority item.
