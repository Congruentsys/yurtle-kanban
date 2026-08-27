---
name: review
description: Pre-merge review - verify tests, docs, and merge readiness for an item
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Bash(pytest *), Bash(python3 *), Bash(gh *), Read, Glob, Grep
argument-hint: "FEAT-XXX"
---

# Review Work Item

Perform pre-merge review of a work item: verify tests exist and pass, docs are updated, and merge if ready.

## Required Argument

`$ARGUMENTS` must be an item ID (e.g., `FEAT-042` or `BUG-007`).

## Steps

### 1. Load Item

```bash
# Show item details
yurtle-kanban show $ARGUMENTS
```

Read the item file and extract:
- **tags**: Determine required test types
- **status**: Current kanban status
- **branch**: Associated branch name

### 2. Check Test Coverage

```bash
# Find tests for this item
find . -name "test_*.py" | grep -v __pycache__

# Run tests
pytest tests/ -v --tb=short 2>&1 | tail -50
```

### 3. Check Documentation

Verify related docs were updated:

```bash
# What files changed in this branch vs main?
git diff --name-only main...HEAD | grep -E '\.(md|rst)$'
```

**Documentation checklist:**
- [ ] Item doc updated with completion notes
- [ ] README updated if user-facing changes
- [ ] API docs updated if new endpoints

### 4. Generate Review Report

Output a structured report:

```
## Review: $ARGUMENTS

### Test Coverage
| Type | Required | Found | Status |
|------|----------|-------|--------|
| Unit tests | Yes/No | X files | PASS/FAIL/MISSING |
| Integration tests | Yes/No | X files | PASS/FAIL/MISSING |

### Documentation
| Doc | Updated | Notes |
|-----|---------|-------|
| Item doc | Yes/No | ... |
| README | Yes/No | ... |

### Merge Readiness
- [ ] All required tests pass
- [ ] Documentation updated
- [ ] Branch up to date with main
- [ ] No merge conflicts

### Recommendation
READY TO MERGE / NEEDS WORK: [specific issues]
```

### 5. If Ready: Offer to Merge

If all checks pass, offer to:

```bash
# Update the branch with main
git fetch origin main
git rebase origin/main
git push --force-with-lease origin feature/feat-XXX-branch

# Merge through a pull request — NOT by pushing main directly. CLAUDE.md is
# explicit about this: "never push directly to main", and the review a PR
# carries is the point of this skill.
gh pr create --fill                       # if one is not open yet
gh pr merge --merge --delete-branch       # after someone OTHER than the author approves

# Update kanban
yurtle-kanban move $ARGUMENTS done
```

### 6. If Not Ready: List Action Items

Create a checklist of what needs to be done:

```
## Action Items for $ARGUMENTS

- [ ] Add unit tests for [specific module]
- [ ] Add integration tests for [specific feature]
- [ ] Update item doc with [missing section]
- [ ] Fix failing test: [test name]
```
