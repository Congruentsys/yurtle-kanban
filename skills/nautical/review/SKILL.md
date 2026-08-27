---
name: review
description: Pre-merge review - verify tests, docs, and merge readiness for an expedition
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Bash(git *), Bash(pytest *), Bash(python3 *), Bash(gh *), Read, Glob, Grep
argument-hint: "EXP-XXX"
---

# Review Expedition

Perform pre-merge review of an expedition: verify tests exist and pass, docs are updated, and merge if ready.

## Required Argument

`$ARGUMENTS` must be an expedition ID (e.g., `EXP-711` or `711`).

## Steps

### 1. Load Expedition

```bash
# Find expedition file
ls kanban-work/expeditions/EXP-$ARGUMENTS*.md 2>/dev/null || ls kanban-work/expeditions/EXP-$ARGUMENTS*.md
```

Read the expedition file and extract:
- **tags**: Determine required test types (`unit-tests`, `integration-tests`)
- **status**: Current kanban status
- **branch**: Associated branch name

### 2. Check Test Coverage

Based on expedition tags, verify tests exist:

> **These commands are the common Python layout, not a contract.** Substitute your
> project's own test command — the structure below (find the tests for this item, run
> the tier its tags ask for) is what the skill is for; `pytest` and `tests/` are just
> the most likely spelling.

#### If `unit-tests` tag (or no tags = default):
```bash
# Find tests for this expedition
find . -name "test_*$ARGUMENTS*.py" -o -name "test_exp$ARGUMENTS*.py" | grep -v __pycache__

# Run the unit suite
pytest tests/ -v --tb=short 2>&1 | tail -50
```

#### If `integration-tests` tag:
```bash
# Find end-to-end tests for this expedition
ls tests/integration/test_*$ARGUMENTS*.py 2>/dev/null
ls tests/integration/test_exp$ARGUMENTS*.py 2>/dev/null

# Run them
pytest tests/integration/test_*$ARGUMENTS*.py -v --tb=short 2>&1 | tail -50
```

### 3. Check Documentation

Verify related docs were updated:

```bash
# What files changed in this branch vs main?
git diff --name-only main...HEAD | grep -E '\.(md|rst)$'

# Check if expedition doc was updated
git diff --name-only main...HEAD | grep -i "exp.*$ARGUMENTS"
```

**Documentation checklist:**
- [ ] Expedition doc updated with completion notes
- [ ] README updated if user-facing changes
- [ ] API docs updated if new endpoints

### 4. Generate Review Report

Output a structured report:

```
## Review: EXP-$ARGUMENTS

### Test Coverage
| Type | Required | Found | Status |
|------|----------|-------|--------|
| Standard (unit/integration) | Yes/No | X files | PASS/FAIL/MISSING |
| Live Being Tests | Yes/No | X files | PASS/FAIL/MISSING |

### Documentation
| Doc | Updated | Notes |
|-----|---------|-------|
| Expedition doc | Yes/No | ... |
| README | Yes/No | ... |
| Other | Yes/No | ... |

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
git push --force-with-lease origin exp-$ARGUMENTS-branch

# Merge through a pull request — NOT by pushing main directly. CLAUDE.md is
# explicit about this: "never push directly to main", and the review a PR
# carries is the point of this skill.
gh pr create --fill                       # if one is not open yet
gh pr merge --merge --delete-branch       # after someone OTHER than the author approves

# Update kanban
yurtle-kanban move EXP-$ARGUMENTS done
```

### 6. If Not Ready: List Action Items

Create a checklist of what needs to be done:

```
## Action Items for EXP-$ARGUMENTS

- [ ] Add unit tests for [specific module]
- [ ] Add integration tests for [specific feature]
- [ ] Update expedition doc with [missing section]
- [ ] Fix failing test: [test name]
```
