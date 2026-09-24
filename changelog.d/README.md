# changelog.d

One file per PR, so parallel PRs never edit the same lines of `CHANGELOG.md`.

- Name it after the issue: `changelog.d/<N>.md`, or `<N>-<slug>.md` for a second entry.
- First line: the section, one of Added, Changed, Deprecated, Removed, Fixed, Security:
  `<!-- section: Fixed -->`
- Then the bullet exactly as it should appear in the release notes:

```markdown
<!-- section: Fixed -->
- **`move --assign` reported an assignment it never wrote** when the item had no
  `assignee:` key (#97).
```

At release time `python scripts/assemble_changelog.py X.Y.Z` moves every fragment (and
anything still under `## [Unreleased]`) into a new `## [X.Y.Z] - <date>` section,
grouped by section and ordered by issue number, and deletes the fragments.
