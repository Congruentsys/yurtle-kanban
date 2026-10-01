---
name: release-foss
description: Release a public (FOSS) repo — version bump, CHANGELOG, reviewed release PR, tag after merge, a public GitHub release with notes cut to fit, the registry publish confirmed live, and credit to external contributors
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(grep *), Bash(gh *), Read, Edit, Write
argument-hint: "[patch|minor|major] [--message 'Description']"
---

# Release a Public (FOSS) Repo

A release of a public package: the version bump, the CHANGELOG, a release PR reviewed by
someone other than the author, a tag on the merged commit, a public GitHub release, the
package published to its registry, and thanks to the people outside the team who
contributed.

For an internal repo (no public notes, no registry, no contributor thanks) use
`/release` instead. If your repo has its own release skill with its specifics (scripts,
version files, workflows), follow that one: it builds on this.

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
# the published package answered the wrong version for a whole release.
grep -rn "__version__" --include="__init__.py" . 2>/dev/null | grep -v "/.git/" | head -5
grep -rn "^const Version\|^VERSION *=" --include="*.go" --include="*.rb" . 2>/dev/null | grep -v "/.git/" | head -5
```

Write down the list. Step 3 updates every file on it, and step 5 stages every one.

### 2. Calculate New Version

| Current | Bump Type | New Version |
|---------|-----------|-------------|
| 1.1.0 | patch | 1.1.1 |
| 1.1.0 | minor | 1.2.0 |
| 1.1.0 | major | 2.0.0 |

A removed or renamed public API, flag or command is `major`, whatever else is in it.

### 3. Update Version Files

Set `X.Y.Z` in every file found in step 1 (e.g. `version = "X.Y.Z"` in
`pyproject.toml`, `__version__ = "X.Y.Z"` in the package's `__init__.py`). Then search
again for the OLD version and confirm only history (the CHANGELOG, lockfiles of other
packages) still has it:

```bash
grep -rn "OLD.VERSION" --include="*.toml" --include="*.py" --include="*.json" . | grep -v "/.git/"
```

### 4. Update CHANGELOG.md

**If the repo collects changelog fragments** (a fragments directory with an assembler
script, e.g. towncrier or a repo-local script), run the assembler for `X.Y.Z` instead
of writing the section by hand. Most assemblers delete the fragments they consume; those
deletions are part of the release commit (step 5).

**Otherwise** add the section at the top of CHANGELOG.md (create it if it doesn't exist),
in [Keep a Changelog](https://keepachangelog.com) form:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- [New features if any]

### Changed
- [Changes if any]

### Fixed
- [Bug fixes if any]
```

Name each external contributor on their entry, by GitHub login: `(#123, thanks @login)`.
External means the PR author is not an OWNER, MEMBER or COLLABORATOR of the repo. Find
them among the PRs merged since the last tag (`gh pr list` has no `authorAssociation`
field, so this uses `gh search prs`; the date search is day-granular, so the `grep` keeps
only PRs whose numbers appear in `<tag>..HEAD`, not ones the last release already credited):

```bash
TAG=$(git describe --tags --abbrev=0)
IN_RELEASE=$(git log "$TAG"..HEAD --format=%s | grep -oE '#[0-9]+' | tr -d '#' | sort -u | paste -sd'|' -)
gh search prs --repo OWNER/REPO --merged-at ">=$(git log -1 --format=%cs "$TAG")" --limit 1000 \
  --json number,author,authorAssociation \
  --jq '.[] | select(.authorAssociation as $a | ["OWNER","MEMBER","COLLABORATOR"] | index($a) | not) | "#\(.number) @\(.author.login)"' \
  | grep -wE "^#($IN_RELEASE)"
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

### 8. Publish the GitHub Release

Most publish workflows trigger on a **published GitHub release**, not on a tag push:

```yaml
on:
  release:
    types: [published]
```

Check yours (`grep -rn -A3 "^on:" .github/workflows/`). If it is release-triggered,
stopping at the tag push publishes **nothing**, silently: nothing fails, the workflow
simply never runs.

**Release notes must fit GitHub's 125,000-character cap.** GitHub refuses a longer
release body, and a refused `gh release create` creates no release, so a
release-triggered publish never runs. The rule:

1. If the version's CHANGELOG section fits, the notes are that section.
2. If it doesn't, the notes are a condensed summary: the entry count per section, every
   breaking change, the Removed and Deprecated entries, the contributor credits, and a
   link to the full section in the CHANGELOG at the tag
   (`https://github.com/OWNER/REPO/blob/vX.Y.Z/CHANGELOG.md`).
3. If even the condensed notes don't fit, stop and shorten them. Never let
   `gh release create` be the thing that finds out.

```bash
# write the notes to a file per the rule above, then:
wc -m < /tmp/notes-vX.Y.Z.md       # must be under 125000
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file /tmp/notes-vX.Y.Z.md
```

End the notes with a thank-you line naming every external contributor by @login:
"Thanks to @login1 and @login2 for their contributions to this release."

### 9. Confirm the Publish Workflow Fired AND the Package Is Live

The workflow running is not the same as the package landing on its registry:

```bash
gh run list --limit 3                           # the publish workflow must be listed for vX.Y.Z
gh run watch "$(gh run list --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Then check the registry itself for `X.Y.Z`:

| Registry | Check |
|----------|-------|
| PyPI | `pip index versions <package>` or `https://pypi.org/project/<package>/X.Y.Z/` |
| npm | `npm view <package>@X.Y.Z version` |
| crates.io | `cargo search <crate> --limit 1` |

A failed or skipped publish is not a release. Fix it before going on.

### 10. Thank External Contributors

On each external contributor's merged PR, leave a comment:

```bash
gh pr comment <N> --body "Released in vX.Y.Z: https://github.com/OWNER/REPO/releases/tag/vX.Y.Z. Thank you, @login!"
```

### 11. Confirm the Release

Show:
- The new version number and the tag
- The GitHub release URL
- The publish workflow's conclusion
- The version live on the registry
- The external contributors credited and thanked

## When to Release

- **Patch (x.y.Z)**: Bug fixes, documentation updates
- **Minor (x.Y.0)**: New features, new skills added
- **Major (X.0.0)**: Breaking API changes

## Related Skills

- `/release` - The same flow for an internal repo, without the public steps
