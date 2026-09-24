# Changelog

All notable changes to yurtle-kanban are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **`move --assign` reported an assignment it never wrote** whenever the item
  had no `assignee:` key, so `list --assignee` lost track of claimed work
  (#97). `move` now adds `status:`/`assignee:` when absent instead of only
  updating an existing line, and `create` always writes `assignee: null`,
  matching the scaffolded templates.
- **`-p/--priority` was ignored by every template-rendered `create`** —
  `idea`, `literature`, `paper`, `hypothesis`, `experiment`, `measure` and
  `epic` (#99). The rendered template was written verbatim, so the priority
  was whatever the template hardcoded (`medium`) or absent. It is now written
  into the frontmatter, and into any `kb:priority` triple the template carries.
  Note: `epic create` without `-p` now gets its documented default, `high`.
  `-p` on these commands now only accepts critical/high/medium/low (any
  case), since the value is written into Turtle. Frontmatter edits now find the
  closing `---` by line, so writing a field no longer cuts a title like
  `"A --- B"` in two (reading such an item back is still #103).
- **`create` could write an item where its own board never looks** (#102). Theme
  per-type paths (`kanban-work/features/`, …) took priority over the configured
  root, so on a board with `root: work/` (or a multi-board `path: work/`) every
  created item was invisible to `board`/`list`. A theme path is now kept only
  when a scanned path contains it, which covers every board laid out like its
  theme, including a default `init`. Otherwise the type folder is placed under the
  board's scanned root. The broader config-model question is #109.
- **An item whose frontmatter contains `---` (e.g. title `"A --- B"`) vanished
  from the board** (#103). The reader, the description extractor and the Turtle
  backfill found frontmatter by the substring `---`. They now share the
  line-anchored match the writers use (#101).
- **Assignee values that YAML reads as something else were corrupted** (#104). `move -a` and
  `create --assignee` wrote values unquoted, so `team: core` made the item
  vanish, `yes` became `true`, `null`/`#core` dropped the assignee and `[core]`
  became a list. They are now quoted when needed and read back as the same string.
  Plain names (`agent-x`) are written as before.

## [2.2.0] - 2026-08-30

### Fixed

- **`__version__` reported the wrong version and had since v2.1.0.**
  `pyproject.toml` said `2.1.0` while `src/yurtle_kanban/__init__.py` said
  `2.0.1`, so the published 2.1.0 wheel answered `yurtle_kanban.__version__
  == "2.0.1"`. CLAUDE.md names both as surfaces that "must stay in sync";
  they were not. Both now read `2.2.0`. The release procedure that allowed
  it is fixed below, so this cannot silently recur.
- `init` no longer scaffolds paper-first HDD guidance.
- `--paper` is optional across the whole HDD family, not just hypotheses, so a
  new user can state a first hypothesis without inventing a paper.
- Shipped skills no longer teach a push to `main`, and every command a skill
  prints is now a command the CLI actually accepts.
- The nautical skills no longer assume one project's directory tree.
- `main` is green again: 13 ruff errors, red since 2026-03-08.
- The test-generated `.kanban/hooks.log` is untracked.

### Changed

- **The release skill now publishes.** Three defects, in sequence, which together
  explain the `__version__` drift above and would have skipped PyPI here:
  1. **Detection** — step 1 used `grep "__version__" */__init__.py`, a glob one
     directory deep that misses a `src/` layout, so it finds nothing in this
     repo and the operator never learns the second version surface exists.
  2. **Staging** — step 3 says to update `__init__.py`, but step 5 staged only
     `pyproject.toml CHANGELOG.md`, so a release that followed the skill edited
     that file and then never committed it.
  3. **Publishing** — the skill ended at `git push origin vX.Y.Z`, but
     `publish.yml` triggers on `release: types: [published]` — a GitHub Release,
     not a tag push. The documented procedure produced a tag and **no PyPI
     publish**, silently: nothing fails, the workflow simply never runs.

### Added

- Guards pinning the paper-optional behaviour so #90 cannot be silently
  reverted, widened to suffixed placeholders.

[2.2.0]: https://github.com/Congruentsys/yurtle-kanban/compare/v2.1.0...v2.2.0
