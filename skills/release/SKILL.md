---
name: release
description: Create a versioned release with git tag and CHANGELOG update
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(grep *), Read, Edit, Write
argument-hint: "[patch|minor|major] [--message 'Description']"
---

# Create Release

Create a new version release with semantic versioning, git tag, and CHANGELOG update.

## Arguments

- `patch` (default): Bug fixes, minor improvements (0.9.0 → 0.9.1)
- `minor`: New features, non-breaking changes (0.9.0 → 0.10.0)
- `major`: Breaking changes (0.9.0 → 1.0.0)
- `--message "Description"`: Optional release description

## Steps

### 1. Check Current State

```bash
# Ensure on main and up to date
git checkout main
git pull origin main

# Check for uncommitted changes
git status

# Get current version (check common locations)
grep -h "version" pyproject.toml | head -1
# or
# ⚠ NOT `*/__init__.py` — that glob is one directory deep and MISSES a src/ layout,
# which is what this repo uses (src/yurtle_kanban/__init__.py). A detection that
# cannot see the file is why step 3's "update that too" was skippable for a whole
# release (v2.1.0 shipped __version__ = "2.0.1"). Search, do not glob:
grep -rn "__version__" --include="__init__.py" . 2>/dev/null | grep -v "/.git/" | head -2
```

Fail if there are uncommitted changes. All work must be committed first.

### 2. Calculate New Version

Parse current version and calculate new version:

| Current | Bump Type | New Version |
|---------|-----------|-------------|
| 1.1.0 | patch | 1.1.1 |
| 1.1.0 | minor | 1.2.0 |
| 1.1.0 | major | 2.0.0 |

### 3. Update Version Files

Update version in `pyproject.toml`:

```toml
version = "X.Y.Z"
```

If project has `__init__.py` with `__version__`, update that too.

### 4. Update CHANGELOG.md

Add new entry at top of CHANGELOG.md (create if doesn't exist):

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- [New features if any]

### Changed
- [Changes if any]

### Fixed
- [Bug fixes if any]
```

If release message was provided, include it.

### 5. Commit Release on a Branch

A release commit is a commit. It goes through a pull request like every other
one — CLAUDE.md is explicit: "never push directly to main". This is also how
releases have actually landed here (v2.0.0 via PR #56, v2.1.0 via PR #70).

```bash
git checkout -b chore/release-vX.Y.Z
git add pyproject.toml CHANGELOG.md src/yurtle_kanban/__init__.py
# ⚠ __init__.py IS staged here on purpose. Step 3 tells you to update it and this
# line used to omit it, so a release that followed this skill edited the file and
# then left it uncommitted. That is how v2.1.0 shipped with pyproject at 2.1.0 and
# __version__ still at 2.0.1 — the published wheel answered the wrong version for a
# whole release. `git status` before committing: an unstaged __init__.py here is the
# bug, not noise.
git commit -m "chore: release vX.Y.Z

[Release description]

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
git push -u origin chore/release-vX.Y.Z
```

### 6. Merge the Release PR

```bash
gh pr create --fill
gh pr merge --merge --delete-branch       # after someone OTHER than the author approves
```

### 7. Tag the Merged Commit

Tag **after** the merge, so the tag names the commit that is actually on main.
Tagging before it means re-tagging if review changes anything.

```bash
git checkout main
git pull --ff-only origin main

git tag -a vX.Y.Z -m "Release vX.Y.Z

[Release description]"

git push origin vX.Y.Z
```

### 8. Publish the GitHub Release — THIS is what triggers PyPI

⚠ **A tag push does NOT publish.** `.github/workflows/publish.yml` triggers on:

```yaml
on:
  release:
    types: [published]
```

A **GitHub Release** — not a tag. Stopping at `git push origin vX.Y.Z` produces a
tag and **no PyPI publish**, silently: nothing fails, the workflow simply never
runs. Create the release explicitly:

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <(sed -n '/^## \[X.Y.Z\]/,/^## \[/p' CHANGELOG.md | sed '$d')
gh run list --workflow=publish.yml --limit 1     # confirm it FIRED
```

Verify it actually published before calling the release done — the workflow
running is not the same as the artifact landing:

```bash
gh run watch "$(gh run list --workflow=publish.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
pip index versions yurtle-kanban 2>/dev/null | head -2   # or check PyPI directly
```

### 9. Confirm Release

Show:
- New version number
- Tag created
- **GitHub Release created, and the publish workflow's conclusion**
- CHANGELOG entry
- PyPI version live

## When to Release

- **Patch (x.y.Z)**: Bug fixes, documentation updates
- **Minor (x.Y.0)**: New features, new skills added
- **Major (X.0.0)**: Breaking API changes

## Related Skills

- `/done` - Complete work (should consider version bump)
