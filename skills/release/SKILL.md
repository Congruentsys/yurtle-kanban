---
name: release
description: Release an internal repo — version bump, CHANGELOG section, reviewed release PR, annotated tag after merge
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(grep *), Bash(gh *), Read, Edit, Write
argument-hint: "[patch|minor|major] [--message 'Description']"
---

# Release an Internal Repo

A versioned release of a repo that is not published to the public: the version bump,
a CHANGELOG section, a release PR reviewed by someone other than the author, and an
annotated tag on the merged commit.

For a public (FOSS) repo, use `/release-foss`: it adds the public GitHub release, the
package publish and the contributor credit that this skill leaves out on purpose. If
your repo has its own release skill with its specifics, follow that one.

## Arguments

- `patch` (default): Bug fixes, minor improvements (0.9.0 → 0.9.1)
- `minor`: New features, non-breaking changes (0.9.0 → 0.10.0)
- `major`: Breaking changes (0.9.0 → 1.0.0)
- `--message "Description"`: Optional release description

## Steps

### 1. Check Current State

```bash
git checkout main
git pull --ff-only origin main
git status                      # must be clean
git describe --tags --abbrev=0  # the previous release tag
```

Fail if there are uncommitted changes. All work must be committed first.

Find **every** file that carries the version. Search, do not glob:

```bash
grep -n '^version' pyproject.toml Cargo.toml 2>/dev/null
grep -n '"version"' package.json 2>/dev/null
# ⚠ NOT `*/__init__.py` — that glob is one directory deep and MISSES a src/ layout
# (src/<package>/__init__.py). A detection that cannot see the file is how a
# release skipped "update that too" and shipped v2.1.0 with __version__ = "2.0.1":
# the built package answered the wrong version for a whole release.
grep -rn "__version__" --include="__init__.py" . 2>/dev/null | grep -v "/.git/" | head -5
```

Write down the list. Step 3 updates every file on it, and step 5 stages every one.

### 2. Calculate New Version

| Current | Bump Type | New Version |
|---------|-----------|-------------|
| 1.1.0 | patch | 1.1.1 |
| 1.1.0 | minor | 1.2.0 |
| 1.1.0 | major | 2.0.0 |

### 3. Update Version Files

Set `X.Y.Z` in every file found in step 1 (e.g. `version = "X.Y.Z"` in
`pyproject.toml`, `__version__ = "X.Y.Z"` in the package's `__init__.py`).

### 4. Update CHANGELOG.md

**If the repo collects changelog fragments** (a fragments directory with an assembler
script), run the assembler for `X.Y.Z` instead of writing the section by hand. Most
assemblers delete the fragments they consume; those deletions are part of the release
commit (step 5).

**Otherwise** add the section at the top of CHANGELOG.md (create it if it doesn't exist):

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- [New features if any]

### Changed
- [Changes if any]

### Fixed
- [Bug fixes if any]
```

If a release message was provided, include it.

### 5. Commit the Release on a Branch

A release commit goes through a pull request like every other commit. Never push it
to main directly.

```bash
git checkout -b chore/release-vX.Y.Z
git add CHANGELOG.md <every version file from step 1>
git add -A <the fragments directory>   # if step 4 assembled fragments: stages their deletions
git status                             # an unstaged version file here is the bug, not noise
git commit -m "chore: release vX.Y.Z

[Release description]

Co-Authored-By: <your agent's trailer, if an agent made the commit>"
git push -u origin chore/release-vX.Y.Z
```

### 6. Merge the Release PR

```bash
gh pr create --fill
gh pr merge --merge --delete-branch       # after someone OTHER than the author approves
```

The release PR is reviewed by someone other than the author, like any other PR.

### 7. Tag the Merged Commit

Tag **after** the merge, so the tag names the commit that is actually on main. Tagging
before it means re-tagging if review changes anything.

```bash
git checkout main
git pull --ff-only origin main

git tag -a vX.Y.Z -m "Release vX.Y.Z

[Release description]"

git push origin vX.Y.Z
```

### 8. GitHub Release: Only If Your Deploy Needs One

The tag is the release. Create a GitHub release
**only if your deploy is triggered by a release**
(a workflow with `on: release: types: [published]`; check with
`grep -rn -A3 "^on:" .github/workflows/`). A release-triggered deploy does nothing on a
tag push alone, silently. If yours is one:

```bash
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes "Internal release vX.Y.Z. See CHANGELOG.md."
gh run list --limit 3                     # confirm the deploy workflow FIRED
```

### 9. Confirm the Release

Show:
- The new version number
- The tag, and the commit on main it names
- The CHANGELOG section
- The deploy workflow's conclusion, if step 8 applied

## When to Release

- **Patch (x.y.Z)**: Bug fixes, documentation updates
- **Minor (x.Y.0)**: New features, new skills added
- **Major (X.0.0)**: Breaking API changes

## Related Skills

- `/release-foss` - The same flow for a public repo, with the public steps
- `/done` - Complete work (should consider version bump)
