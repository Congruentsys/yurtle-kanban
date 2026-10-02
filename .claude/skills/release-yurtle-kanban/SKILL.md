---
name: release-yurtle-kanban
description: Release yurtle-kanban itself — changelog.d assembly, both version files, sized GitHub release notes, the PyPI publish via publish.yml, and contributor credit. Follows the shipped release-foss skill with this repo's specifics.
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(grep *), Bash(gh *), Bash(python *), Bash(pip index *), Bash(ls *), Bash(head *), Bash(sort *), Bash(tr *), Bash(paste *), Read, Edit, Write
argument-hint: "[patch|minor|major] [--message 'Description']"
---

# Release yurtle-kanban

This repo's own release. It follows the shipped `skills/release-foss/SKILL.md` step for
step; this file says what is specific to yurtle-kanban. It is repo-local on purpose: the
scripts it runs are not in the package, so a consumer repo must never be told to run them
(#1201).

**Who decides:** a release is the Captain's call. The one exception is the fleet's
patch/minor releases under the external-PR process (#1195), if and once that process is
merged; until then, ask.

## Steps

### 1. Check Current State

```bash
git checkout main
git pull --ff-only origin main
git status                      # must be clean
git describe --tags --abbrev=0  # the previous release tag
ls changelog.d/                 # the fragments this release will assemble
```

The version lives in **two** files, and both must move together:

- `pyproject.toml` → `version = "X.Y.Z"`
- `src/yurtle_kanban/__init__.py` → `__version__ = "X.Y.Z"`

(v2.1.0 shipped with `__version__` still at 2.0.1 because a release missed the second
file; `scripts/check_release_version.py` now refuses that in `publish.yml`, but only
after the release exists.)

### 2. Calculate New Version

Per release-foss's semver table. A removed or renamed CLI command or flag is `major`.

### 3. Update Both Version Files

```bash
grep -n '^version' pyproject.toml
grep -n '__version__' src/yurtle_kanban/__init__.py
```

Edit both to `X.Y.Z`.

### 4. Assemble the CHANGELOG from changelog.d

Do not write the section by hand. Each merged PR left a `changelog.d/<N>.md` fragment:

```bash
python scripts/assemble_changelog.py X.Y.Z   # --date YYYY-MM-DD to override today
```

It moves every fragment (and anything still under `## [Unreleased]`) into
`## [X.Y.Z] - <date>`, grouped by section and ordered by issue number, and **deletes the
fragments it consumed**. With nothing to release it changes nothing. Read the new
section, and add `thanks @login` to each external contributor's entry. External means
the PR author is not an OWNER, MEMBER or COLLABORATOR; list them among the PRs in this
release:

```bash
TAG=$(git describe --tags --abbrev=0)
IN_RELEASE=$(git log "$TAG"..HEAD --format=%s | grep -oE '#[0-9]+' | tr -d '#' | sort -u | paste -sd'|' -)
gh search prs --repo Congruentsys/yurtle-kanban --merged-at ">=$(git log -1 --format=%cs "$TAG")" --limit 1000 \
  --json number,author,authorAssociation \
  --jq '.[] | select(.authorAssociation as $a | ["OWNER","MEMBER","COLLABORATOR"] | index($a) | not) | "#\(.number) @\(.author.login)"' \
  | grep -wE "^#($IN_RELEASE)"
```

For a **major** (X.0.0), check that every removal and break reaches users through the
upgrade guide (the deprecation policy in CONTRIBUTING.md):

```bash
python scripts/check_upgrade_guide.py X.Y.Z   # non-zero = stop: add the missing UPGRADING.md entries
```

It fails unless each `### Removed` entry, and each `**Breaking` entry, of the new section
has its `#N` in `UPGRADING.md`. Non-zero: stop, write the missing guide entries, and run it
again. For a minor or patch it has nothing to check.

### 5. Commit the Release on a Branch

```bash
git checkout -b chore/release-vX.Y.Z
git add CHANGELOG.md pyproject.toml src/yurtle_kanban/__init__.py
git add -A changelog.d                 # stages the DELETED fragments; a plain `git add` misses them
git status                             # nothing left unstaged: not a version file, not a fragment
git commit -m "chore: release vX.Y.Z

[Release description]

Co-Authored-By: Claude <the model you are> <noreply@anthropic.com>"
git push -u origin chore/release-vX.Y.Z
```

Use the trailer of the model actually making the commit (`git log -5 --format=%B | grep
Co-Authored` shows the current form), never a model name copied from an old commit.

### 6. Merge the Release PR

```bash
gh pr create --fill
gh pr merge --merge --delete-branch       # after someone OTHER than the author approves
```

### 7. Tag the Merged Commit

```bash
git checkout main
git pull --ff-only origin main

git tag -a vX.Y.Z -m "Release vX.Y.Z

[Release description]"

git push origin vX.Y.Z
```

### 8. Publish the GitHub Release: This Is What Triggers PyPI

A tag push does **not** publish. `.github/workflows/publish.yml` runs on
`release: types: [published]`, runs `scripts/check_release_version.py` (the tag must
match both version files), builds, and publishes to PyPI.

```bash
python scripts/release_notes.py X.Y.Z > /tmp/notes-vX.Y.Z.md   # non-zero = stop and fix
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file /tmp/notes-vX.Y.Z.md
gh run list --workflow=publish.yml --limit 1     # confirm it FIRED
```

`scripts/release_notes.py` applies release-foss's 125,000-character rule: the section when
it fits, else condensed notes with a link to the full section, else it refuses with a
non-zero exit and prints nothing (#1191). Never pass the CHANGELOG section by hand:
v3.0.0's was 137,199 characters. Append the thank-you line for external contributors to
the notes file before `gh release create`.

### 9. Confirm PyPI

```bash
gh run watch "$(gh run list --workflow=publish.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
pip index versions yurtle-kanban 2>/dev/null | head -2   # X.Y.Z must be listed
```

### 10. Thank External Contributors

Comment on each external contributor's merged PR with the release link and a thank-you
by @login (release-foss step 10).

### 11. Confirm the Release

Show the version, the tag, the GitHub release URL, `publish.yml`'s conclusion, the
version live on PyPI, and the contributors credited.
