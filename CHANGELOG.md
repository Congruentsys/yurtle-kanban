# Changelog

All notable changes to yurtle-kanban are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

New entries go in `changelog.d/` (one file per PR; see `changelog.d/README.md`)
and are assembled into a release section by `scripts/assemble_changelog.py`.

## [Unreleased]

## [3.1.0] - 2026-10-01

### Added

- **`scripts/release_notes.py X.Y.Z`** prints GitHub release notes for a version: its full CHANGELOG section when that fits GitHub's 125,000-character release-body cap, otherwise condensed notes (per-section entry counts, every **Breaking** entry, the Removed and Deprecated sections, and a link to the full section). The release skill's step 8 now builds `--notes-file` with it, so an oversized section no longer makes `gh release create` fail and skip the PyPI publish (#1191).

### Fixed

- The PyPI workflow now rejects release tags that do not match the package versions (#1192; contributed by @PandaHUN777 in #1193).

## [3.0.0] - 2026-09-30

### Added

- **CHANGELOG fragments** (#178): each PR adds `changelog.d/<N>.md` instead of
  editing `CHANGELOG.md`, and `scripts/assemble_changelog.py X.Y.Z` builds the release
  section from them. The pairit skill now writes each issue's tests in their own
  `tests/issues/test_<N>_<slug>.py` and names test commits `test(#<N>): red` (#177),
  so parallel PRs no longer conflict on rebase.
- **`states [--board B] [--type T] [--json]`** (#573): each board's lifecycle, one line
  per status (`abandoned (blocked)` where the theme renames it, `(terminal)` where
  nothing follows); `--json` gives native and canonical names and, per transition, the
  ids of the gates `move` will evaluate. Lifecycle legality only: gates, WIP limits and
  workflow rules can still refuse a legal move. `show` gains `Can move to`, and
  `show --json` gains `next_statuses` (canonical) and `next_status_labels` (native).
- One lifecycle source, `legal_next` (#589): `move`, `states`, `show` and
  `get_allowed_transitions` all read it, and the default table lives only in
  `workflow.py` (the default workflow had a third, disagreeing copy). An item whose
  status its workflow doesn't know can no longer move anywhere (it used to move
  anywhere). A refused move now says
  `Illegal move EXP-1: ready → done. Legal from ready: in_progress, backlog, blocked.`,
  in the theme's own names.
- `sync_and_push`: the one worktree-free compare-and-swap kanban push onto origin's default branch, with distinct outcomes and exit codes (won/local/noop 0, refused 1, lost 3, unreachable 4, busy 5, push refused 6), shared by `claim` and the coordination commands that follow (#574).
- `claim ID [--agent A] [--take-over]`: the race-free claim, one compare-and-swap commit on origin's default branch that moves the item to in progress held by you; a held item is refused ("held by X"), a lost race says "lost to X" (exit 3), the actor must be explicit (`--agent` or `YURTLE_AGENT`, never git `user.name`), and `--take-over` records `kb:takenOverFrom` while gates, WIP and legality still apply (#574).
- `move` refuses an item someone else holds in progress (`--force` does not override it); `--take-over` with an explicit `--agent`/`$YURTLE_AGENT` moves it anyway and records `kb:takenOverFrom`, and MCP `kanban_move_item` takes an optional `agent` (#574).
- `update ID ... --push`: the field-level edits of `update` applied to the item as origin's default branch has it and pushed as one compare-and-swap commit, dependencies checked against the fetched board; a rival's edit to another line survives, a rival's edit of the same field is overwritten (last writer wins), and the exit codes are `claim`'s; `--push --no-commit` is refused (#574).
- **One "can I pick this up?" answer for `claim`, `next`, `list --pickable` and MCP**
  (#575). `KanbanService.pickable(item, actor)` checks, in order: status `ready`,
  unassigned or yours, every `depends_on` met; the first failing clause is the reason
  (`status draft is not a ready status`, `held by X`, `waiting on EXP-3 (unfinished)` /
  `(unknown ID)` / `(dead: abandoned)` / `(cycle: A → B → A)`). hdd boards have no
  ready column, so their items are never pickable.
  - `claim` refuses a non-pickable item with that reason (it keeps `held by X`);
    `--take-over` skips the holder clause and also takes an item in progress.
    `claim --next --agent A` claims the first pickable item it wins, moving on past
    `refused`/`lost`; none left exits 7.
  - `next [--agent A] [--json]`: your own in-progress item first (the one in progress
    longest), else the top pickable item, ordered by `priority_rank`, then priority,
    then the OLDEST ID. `--json` gives `{"id", "kind", "reason"}`, or `null` and exit 7.
    MCP `kanban_suggest_next` returns the same item.
  - `list --pickable [--agent] [--explain] [--json]`: the read-only view; `--explain`
    adds each ready item that is not pickable, with its reason, and a dependency cycle
    is warned about.
  - Finished: canonical `done`, or a theme column marked `closed: true` (hdd
    `abandoned` now is). `states --json` gives `"finished"` per state.
    `dependency_state` and `unmet_dependencies` expose the dependency walk.
- **`update ID`** edits an item's fields and dependencies: `--title`, `--priority`, `--tag/--untag`, `--body/--body-file PATH|-`, `--depends-on A,B` (replace; `""` clears), `--add-dep/--rm-dep`, `--related`, `--allow-unknown` and `--no-commit`. It rewrites only the changed frontmatter lines (plus the `# Title` heading, or the body under it); the status history, comments, unknown keys and native status stay byte-identical. Dependency IDs are upper-cased. A self-dependency, a target on no board (without `--allow-unknown`), a target ID on more than one board, and a cycle this edit would close (`cycle: EXP-1 → EXP-3 → EXP-1`, across boards) are refused with exit 1 and nothing written. The commit holds the item file alone and names the changes (`Update EXP-5: depends_on +EXP-3 -EXP-2, priority high`); a no-op prints `no changes`. The MCP `kanban_update_item` tool shares the same path and gains `depends_on`, `related` and `allow_unknown` (#576).
- **`blocked [--board B] [--all] [--json]`** is dependency-aware (#577): it lists each
  item once, when it is status-blocked (canonical `blocked` and not finished, so hdd
  `abandoned` is no longer listed) or dependency-blocked (ready, in_progress or review,
  with an unmet `depends_on`; `--all` adds backlog). Under each item, a tree of its
  unmet dependencies from #575's `unmet_dependencies`: `unknown ID`, `dead` (needs a
  human), `(see above)` for a node already shown under that item, and a terminating
  `↻ cycle: A → B → A`. `--board` limits the listed items; dependencies are still
  resolved across every board. The help points hdd `implements` edges to
  `hdd critical-path --dev-blockers`. The software and nautical `blocked` skills now
  record an item blocker with `update X --add-dep Y` and keep the status, reserve
  `move X blocked` for non-item blockers (reason in `comment X --body-file -`), and
  warn that on hdd `blocked` means abandoned.
- **`bounce ID (--reason TEXT | --reason-file PATH|-) [--agent A] [--take-over]`** (#578):
  give an ill-defined item back. One compare-and-swap commit, as `claim` makes, moves it
  to its theme's backlog status (exempt from the transition table and WIP limits;
  `* -> backlog` gates still apply; the history node records `kb:bounced`), clears the
  assignee, stamps the frontmatter with `bounce_sha` (the body hash), `bounced_by`,
  `bounced_at` and `bounces`, and adds the reason as a comment. `pickable` gains a clause:
  `claim`, `claim --next`, `next` and `list --pickable` skip an item whose body (the title
  line included) is unchanged since it was bounced. `show` prints `Bounced N×`,
  `show --json`/`list --json` carry `bounces`, and `validate` reports a malformed stamp.
  An item someone else holds needs `--take-over`; a finished one is refused.
- **Aging on `list`** (#579): `list --older-than DURATION` lists open (not finished)
  items that have sat in their status that long; `list --stale` lists in-progress items
  in progress for `--stale-after` (default `24h`) or longer, and prints
  `yurtle-kanban claim ID --take-over --agent <you>` for each. DURATION is
  `^\d+(m|h|d|w)$` (anything else exits 2). Both sort oldest first, unknown age last,
  and add `Age` and `Since` columns. Every `list --json` row gains `since`,
  `since_source` (`history`, then the author date of the last commit that changed the
  `status:` line from one `git log`, then `created`, else `unknown`), `age_seconds`,
  `stale`, `clock_skew` and `board`; an empty `list --json` prints `[]`. `--status`
  takes native or canonical names, per item theme.
- History stamps are written with their UTC offset, and one parser (`parse_stamp`,
  naive = the reader's local time) serves `list`, `metrics` and `next`, so `metrics`
  no longer crashes on mixed naive/aware stamps. Only the canonical history block is read or
  written: the one before `## Comments`, else (legacy: an item commented on before its
  first move) the first one after it. A history block pasted into a comment can no
  longer override real history.
- **`move --resolution R [--superseded-by ID]`** records how an item was finished:
  `completed`, `superseded`, `duplicate` or `wont_do` (one `RESOLUTIONS` for the parser,
  the CLI and MCP `kanban_move_item`; `obsolete`/`merged` are gone). Only on a finished
  status (`completed` on done only); the target is checked (unknown, self, itself
  superseded, cycle), gates judge the proposed item, the history records
  `kb:resolution`/`kb:supersededBy`, and a move to a status that is not finished clears
  it (`kb:clearedResolution`). A `wont_do` dependency is dead, a superseded one takes
  its final target's state. `list --resolution`, `show` rows and a `validate` check
  (#581).
- **`control halt|resume|status`: an emergency stop** for every agent on a repo. `halt`
  (`--reason` or `--reason-file`, required; `--agent`) writes `.kanban/control.yaml` as
  one kanban-only commit on origin's default branch, as `claim` does, and says loudly
  when it did NOT take effect for other agents. While halted, `claim` (`--next`,
  `--take-over`) and a move to in progress (CLI and MCP) are refused and `next` and
  `list --pickable` exit 8; a holder may still finish, and `create`, `comment`,
  `update` and `bounce` are allowed. The state is read from origin as last fetched
  (`claim` reads its own fetch), never fetched for a read; a stale fetch's age is shown.
  A file that doesn't read, or names an unknown mode, counts as a halt, and `validate`
  reports it. `control status --json` gives mode, reason, by, at, source and
  fetched_at (#582).
- Each comment on an item is a `kb:comment` node in the query graph (`kb:author`, `kb:text`, and `kb:at` when it has a time), so SPARQL can search comments again (#635).
- An item keeps its declared type: `WorkItem.declared_type` holds the frontmatter `type` as written, so a `type: spec` item (or a custom theme's type) shows as `spec`, not `task`, in `show` and in `list`'s new Type column (which replaces the type icon before the ID). `--json` and MCP items gain a `declared_type` key next to `item_type`, which stays the canonical type used for workflows and prefixes (#682).
- `blocked --json` and MCP `kanban_get_blocked`: each `unmet` node carries `cycle_kind` — `"supersession"` (its `superseded_by` walk), `"depends_on"` (a dependency cycle) or `null`. It is decided where the `cycle` state is, and `blocked`'s tree reads it instead of re-deriving the kind (#1083).
- `list --type`, `roadmap --type` and MCP's `kanban_list_items` `item_type` accept a type as items declare it (e.g. `spec`), case-insensitively; a canonical type still matches the canonical type as before. A name neither canonical nor declared by any item is refused, and `Valid types:` lists the declared types present (#1131).

### Changed

- docs/THEMES.md adds a recipe for research and maintenance in one repo: an hdd board beside a software board. The dual-board example in docs/hdd-methodology.md now loads as two boards: it adds `version: "2.0"` (without it `boards:` was ignored) and drops the `phases:`/`relationships:` keys, which were never read. It also uses `.kanban/config.yaml` to match `init`, and names the real cross-board link, an experiment's frontmatter `implements:` (#84).
- README: a board's type folders under its root are always scanned, whatever `scan_paths` says; use a dedicated root or `paths.ignore` when the root is broad (#137).
- **Cosmetic fixes from recent reviews** (#210).
  - `_SAFE_LOCAL_NAME` drops its redundant anchors, since it's used with `fullmatch`.
  - The experiment-run validators get clearer comments.
  - The README says `create --push` where it lists commands that skip git.
  - `KanbanService` takes `repo_root: Path | str`.
  - The #162 changelog entry is rewrapped.
- A board scan parses each item's frontmatter once: the graph guard reuses the scan's parse and size checks instead of repeating them (#311).
- The `KanbanService` docstring says that a config with no `repo_root` is bound to
  the first service's repo (#313).
- Graph-guard comments and docstring describe the #311 reuse and its known limits exactly (#322).
- The `KanbanService` docstring also says that a config loaded from a file keeps its own
  `repo_root` (#326).
- CI lints `tests/` as well as `src/` with ruff; long lines and uppercase test names are
  allowed under `tests/` (#333).
- README: the always-scanned type folders paragraph now says default placement, and that it applies to single boards (#490).
- README: the always-scanned type folders caveat says "inside the repo" and words the multi-board clause plainly (#495).
- Clarified the #509 changelog entry and the hook log-path comment (#523).
- The log-handler guard records the dynamic shapes it deliberately doesn't catch, each pinned as a strict xfail. The runtime handler check backstops the attribute-setting gaps. The `extra=` gaps rely on src having no RichHandler at all (#530).
- The log-handler guard tests import rich directly, a hard dependency, so a missing rich fails loudly instead of skipping or passing vacuously (#541).
- The log-handler tests import the MCP server unconditionally, since it never uses the `mcp` package; the unused skip helper is removed (#551).
- The `[mcp]` extra no longer installs the `mcp` package, which the MCP server never imports. The extra is kept, now empty, so `yurtle-kanban[mcp]` still resolves (#555).
- Docs: the hdd-methodology `implements:` placeholder uses a software-board ID, and the indexer module docstring states the deprecation (#564).
- The MCP version test requires the JSON-RPC `result` envelope, and the indexer module docstring states its deprecation once (#567).
- A description edit (`update --body`, `update_item`, MCP) replaces the body up to the status-history block or `## Comments`; a hand-written ```` ```yurtle ```` block below the heading is now part of the body, not its end (#576).
- `validate` also reports dependency cycles and `depends_on` targets that are on no board, across every board, and now reports an ID duplicated across boards; the scan used to keep one of the two items silently, so that check never fired (#576).
- **Breaking: one actor identity, safe free-text input, normalised flags** (#580).
  - The *actor* (a comment's author, `kb:by` on a status change) resolves through one
    function: `--agent`, then `$YURTLE_AGENT`, then git `user.name`, else an error. The
    `"cli"` (comment), `"agent"` (MCP comment) and `"unknown"` defaults are gone, and a
    set-but-blank `YURTLE_AGENT` is an error. `kb:by` is now the actor, not the assignee.
  - The *assignee* is never defaulted: only `--assign` sets it.
  - Identity values (`--agent`, `YURTLE_AGENT`, `--assign`, `list --assignee`) are
    refused when empty, whitespace-only or holding a control character; `list
    --assignee ""` used to match every item.
  - Free text: every `--X` has a `--X-file PATH|-` twin. `-` reads stdin (refused on a
    terminal), read whole before any subprocess; strict UTF-8, CRLF→LF, trailing
    newlines dropped, empty refused; stored verbatim. Every git subprocess (and hook)
    gets `stdin=DEVNULL`.
  - Flag renames, no aliases kept:
    - `comment ID TEXT --author/-a A` → `comment ID --body TEXT | --body-file PATH|- [--agent A]`
      (the positional text is removed);
    - `create --assignee/-a` → `create --assign`; `create --description/-d` →
      `create --body` / `--body-file`;
    - `move --assign/-a` → `move --assign`, plus a new `move --agent` (the actor);
    - `next --assignee/-a` → `next --agent`;
    - `list --assignee/-a` → `list --assignee` (a filter only);
    - the short flag `-a` is removed everywhere.
  - MCP `kanban_add_comment`: an omitted `author` resolves like `--agent` (no `"agent"`
    default).
- The gates docstring says checks see the proposed item (target status, new assignee). Tests pin that an item never counts against its own WIP slot, both within a column and for per-type limits (#598).
- Nautical's status names now live in `themes/nautical.yaml` `status_mappings` (with `stranded` and `approaching_port` added), so nautical items are written with native names (`harbor`, `underway`, …) as hdd and spec items are, and a repo-local theme override can remap, drop or add them. MCP `kanban_move_item` accepts the item's own theme's names as `move` does, writes the native spelling, and refuses another theme's name, listing the legal ones (#604).
- Comments are their own field: the `## Comments` section is parsed into `item.comments` (author, time, text, in order) and is no longer part of `description`. `show` (human and `--json`), MCP `kanban_get_item` and semantic search read the new field, and `update_item(description=…)` never touches the comments section (#605).
- Tests pin WIP-exempt types with within-column moves (#606).
- `move` resolves the acting agent once, in `move_item`; `_update_item_file_with_history` takes the resolved actor. No behaviour change (#630).
- Another theme's status names are no longer read on a board whose theme doesn't define
  them: a software item's `status: arrived` (nautical) or `status: active` (hdd) now reads
  like any unknown status, i.e. backlog. So do `planning` and `completed`, which no theme
  defines. The hard-coded cross-theme tables are gone (#633).
- Workflow rules on `len(item.description)` count only the body: comments are their own field since #605, so an item with a short body and long comments can now fail such a gate (#635).
- `_ID_ALLOCATIONS.json` records store their id space as `prefix` (`EXP`, `IDEA-R`, `H130.`), and records are counted by their `id` alone; a record with no `id` no longer counts (#641).
- With `--push`, a parent that is not on `origin/<default>` (for example, one that exists only locally) is refused: the create names the parent, says it isn't on `origin/<default>`, and creates and pushes nothing. Without `--push` the parent is still updated as a local change (#645).
- Internal: whether a fetched default branch is recorded as `origin/HEAD` is an explicit `record=` argument to `_fetch_default` (from `_resolve_default`), not instance state an earlier call left behind (#698).
- Internal: `_race_to_branch` takes `known` as a required keyword, so no default can decide whether a fetched branch is recorded as `origin/HEAD` (#708).
- A child create parses its parent's turtle block once: the reason no link was written comes out of the attempt (`link_parent`, `parent_state` on every `create_item_and_push` result) instead of a second parse (#750).
- MCP `kanban_add_comment` checks its text with the public `check_encodable`, as the CLI does, not the service's private `_check_text` (#767).
- `claim` and `update --push` judge WIP limits, board paths and ignore patterns by origin's `.kanban/config.yaml` (and the `.kanban/themes/` files it names) at the fetched commit, falling back to the local config when origin has none; gate checks still read the local working tree (#831).
- `claim`, `update --push` and `create --push` now judge the fetched commit by origin's own config throughout: the fetched item is parsed with origin's theme (`.kanban/themes/` read from the commit), the claim's move is checked against origin's theme and `.kanban/workflows/` at that commit (a local-only workflow no longer applies) and writes origin's name for in progress, and `create --push` allocates the id, places the file and finds the parent (with origin's ignore patterns) at origin's board paths; gates still run from the local config, and with no config on origin the local one judges as before (#865).
- With `--json`, every refusal (a refused input, an invalid config, an undecodable argument, an unknown status, type or board, a bad date, a missing query or a SPARQL error) prints exactly one JSON object on stdout, `{"success": false, "error": …}`, and exits 1; `show` and `states` errors gain `success: false`, and `next-id` keeps its keys and gains `error` (#877).
- **`create --push` lists origin's tree once per attempt:** the file-twin (#788) and
  folder-twin (#834) checks share one whole-tree listing, and the file-twin check no
  longer depends on a folder pathspec matching origin's spelling (#903).
- `claim --help` and `update --help` say that origin's own config, themes and workflows judge the fetched item (its parse, the move's legality, WIP, board paths and ignore patterns), and that gates stay local (#918).
- **Parse warnings are de-duplicated with a set**, so a board with thousands of files
  that don't parse no longer checks each new warning against every earlier one (#921).
- Internal: a test pins that `next-id` skips non-object allocation records in the committed `_ID_ALLOCATIONS.json` even with no local copy, and `_local_allocations` documents the skip (#932).
- Internal: the folder-twin guard's `_folder_case_twin` wrapper is retired in favour of `_folder_twin_refusal`, and a write into either spelling of a folder origin already holds twice is pinned as refused (#950).
- Internal: `get_flow_metrics` returns the empty metrics shape for a known item with no history, and `experiment status` refuses through a shared JSON-aware `refuse()` in `_click.py`. Output is unchanged (#962).
- Internal: `_ids_at` and `_items_at` share one `_rev_roots()` for the folders a scan walks, so they can't drift apart again (#954), and stale "under the work paths" docstrings name the placement dirs too (#986).
- Internal: the #1014 docstrings (test_1014, `_ids_at`, `_holder_at`, `_next_id_number_at`, `_scanned_placement_dirs`) describe the amended rule, which is that the id space reads every placement dir (#1028).
- Internal: the Turtle reader builds its prefixed-name pattern once (`_PNAME`) for both places it is used (#1030).
- Internal: the `_ids_at` and `_holder_at` docstrings read plainly (#1034).
- Internal: the repo-root rglob lint reads an f-string glob's placeholders as possibly empty, so `f"*{x}*"` counts as recursive (#1035).
- Internal: the #1021 walker tests pin a flag or `--help` given a value (`--flag=1`) against click (#1042).
- `validate --help` names every bounce-stamp key it checks (`bounced_at` with an offset, `bounced_by` non-empty); test_1041 covers an unquoted naive `bounced_at` and drops unused gate scaffolding (#1051).
- Plain `list --json` no longer consults git history for items without status history: their `since` comes from `created:` (`since_source: created`) or is `unknown`, which takes about 3 s off a large repo's listing. `--older-than`, `--stale` and an explicitly given `--stale-after` still run the full lookup, git included (#1055, amending #579's Acceptance 1).
- Internal: test_1041's section headings match its docstring and the #1041 ruling (#1056).
- MCP `kanban_get_blocked` returns exactly what `blocked --json` returns (`{"items": [...]}`: status-blocked items, never hdd `abandoned`, plus ready/in_progress/review items with unmet dependencies, each with its `unmet` tree), and takes `board` and `all` like `--board`/`--all`. It was a plain status filter returning `blocked_items`/`count`; the CLI and MCP now share one service function, `KanbanService.blocked_report` (#1066).
- Text-mode refusals through the shared `refuse()` (#962) print on **stderr**, as click's own errors and `blocked`'s `Error: Unknown board: …` already did: for example `list`, `states` and `export` with an unknown `--board`, `list`'s unknown status or empty `--assignee`, `claim`'s missing actor, `experiment status` of an unknown experiment, and a halted board's refusal from `next`, `list --pickable` and `claim --next` (still exit 8). Exit codes are unchanged, and colour follows stderr, so `2>err.log` holds no escape codes. With `--json` a refusal is still one JSON object on stdout (#877). Refusals printed outside `refuse()`, such as `move`'s `Item not found` and `Unknown status`, still print on stdout (#1086) (#1080).
- The last refusals that printed on stdout now go through the shared refusal, so they print on stderr with their exit codes unchanged (#1086). These are `move`'s `Error: Item not found`, `Unknown status` (with its `Valid statuses:` line) and halted-board refusal (exit 8); the `Error:` line of a non-zero `claim`/`bounce`/`control`/`update --push` outcome, including a halted board's exit 8; `claim --next`'s "nothing pickable" (exit 7); `comment`'s refusal; the hdd commit, push and `experiment run` refusals; and `epic`/`voyage create --push`'s failed push. A success line still prints on stdout.
- The remaining red refusal lines that printed on stdout now go through the shared refusal, so they print on stderr with their exit codes unchanged (#1090). These are `create`'s `Unknown type` (with its `Valid types:` line), a refused title/body/tags and a failed `--push`; `show`'s `Item not found` (with any `doesn't parse` lines); `board-add`'s `Unknown preset` (with `Available presets:`), `Invalid WIP limit`, `Can't upgrade to multi-board` and `already exists`; `rank`'s refusal; `export`'s `Unknown format`; `next-id`'s `Failed to allocate ID`; an invalid `.kanban/config.yaml`; an argument that isn't valid UTF-8; and `query`'s SPARQL error, broken semantic extra and missing-query line (with its `Example:`). Under `--json` each is still one JSON object on stdout.
- `roadmap --help` now says `--type` accepts a type declared by any item, done ones included, so a type only done items declare shows an empty roadmap rather than a refusal; and `list --board B --type NAME` scans the board once, not twice (#1141).
- Internal: create and create --push have regression tests for skipping non-object allocation records (#1153).
- Internal: end-to-end tests pin #846's skip for `next-id --no-sync` reading a mixed allocation file only on the fetched origin, and for `create --push`'s local rewrite (#1157).
- MCP `kanban_next_id`'s description (and `allocate_next_id`'s docstring) names its two refusal shapes: `{"error": ...}` for bad input or, when allocating locally, a corrupt allocations file in the checkout; the `{"success": false, ...}` dict for everything else, including a corrupt file when syncing, a corrupt origin, no actor, and a refused commit or push (#1170).
- Internal: the MCP `kanban_next_id` refusal-shape table also pins a refused local commit (pre-commit hook) as the `{"success": false}` dict (#1177).
- Internal: each row of the MCP `kanban_next_id` refusal-shape table also pins a message substring, so a row that drifts into a different refusal of the same shape fails (#1181).
- A file named `.kanban` is refused in one wording ("… is a file, where a directory must be: move or remove it"), from the allocations reader as from `init`, `board-add` and `control` (#1183).
- Internal: the #1174 changelog entry quotes the refusal wording #1183 settled on (#1186).
- Internal: the fragment-format test, and the #365 changelog-wording tests, pass once a release has assembled the fragments away (#1188).

### Deprecated

- `WorkItemIndexer` is deprecated. Constructing it emits a `DeprecationWarning` pointing to `KanbanService.scan`, the one way to read a board, because the indexer diverges from it (#434).

### Removed

- `WorkItem.to_yurtle()` (never called; frontmatter is the only source of truth) and `WorkItem.blocks` (never parsed; "X blocks Y" is "Y depends_on X"), with the `blocks` key of `to_dict()` and the `kb:blocks` triple `query` derived from it (#576).
- Removed the unused theme keys: hdd's `id_formats` block, which nothing ever read (hdd IDs come from the item types), and `status_aliases`, which is dead in every built-in theme. The theme-key guard now derives its list from `_THEME_SECTIONS` (#611).
- `KanbanService._commit_and_push_file`, and the `push` argument of `update_parent_turtle_block`: the link is written locally, or rides in the child's commit through `create_item_and_push(parent=...)` (#645).
- `WorkflowParser.validate_transition` and `WorkflowConfig.get_allowed_transitions`: unused second copies of the legality check. `KanbanService.legal_next` is the one source (#651).

### Fixed

- **Boards outside the repo, or reached through a symlink (#174).** An in-repo
  board configured by a symlinked absolute path (macOS `/tmp` → `/private/tmp`)
  now matches anchored ignore patterns such as `work/hidden/*`. For a board
  outside the git repository, `move`, `comment` and `rank` skip git with a
  warning (`… is outside the git repository`) instead of `Git commit failed … 128`,
  and `create --push` creates the item rather than failing (before any pull or
  ID-allocation record). "Outside" means outside the git work tree (`git rev-parse
  --show-toplevel`), so `.kanban/` in a subdirectory still commits a sibling board,
  and a relative `../outside/` root counts as outside. `init --path <outside>` warns
  that the board will not be git-tracked.
- **MCP `create_item`/`update_item` crashed on a non-string `priority`** (a JSON
  number, boolean or list) with `'int' object has no attribute 'strip'`; they now
  refuse it with the usual error and write nothing. The "Unknown priority" message
  is now worded the same by the CLI, the MCP server and the service:
  `Unknown priority: <value>; valid: critical, high, medium, low` (#171).
- **A title, description, assignee, tag or comment with undecodable bytes crashed
  or left a 0-byte item file.** Invalid UTF-8 in argv becomes a lone surrogate
  (`\udcff`), which can't be written. `create`, `update`, `comment` and the
  MCP tools now refuse it up front, `<field> contains invalid UTF-8`, and write
  nothing (#172).
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
- **`create -p` accepted any text** (#106), so an item created with `-p urgent`
  could never be found by `list --priority`, which only accepts the four real
  values. `create` now takes `critical`/`high`/`medium`/`low` (any case), like
  the template-based creates (#99), and the MCP `create`/`update` tools reject
  anything else too. All of them share `PRIORITIES`.
- **`board-add` made every existing item vanish from a default-`init` board**
  (#94). Upgrading to multi-board copied `root` (`work/`) into the default
  board, but `init` keeps items in `kanban-work/*`. The default board now gets
  the directory the old config really scanned: the scan path containing its
  `root`, else the common parent of its `scan_paths`. It also keeps those
  `scan_paths` and its `ignore` patterns (so `_TEMPLATE.md` files stay hidden).
  `list`/`show`/`move` apply each board's own `ignore:` to that board, as `board`
  already did (#124).
  `scan_paths` and its `ignore` patterns (so `_TEMPLATE.md` files stay hidden),
  and per-board `ignore:` lists are now honoured when scanning.
- **Editing a multi-line frontmatter value left its old lines behind** (#105).
  `move -a carol` on an `assignee:` block list produced `carol - alice - bob`;
  multi-line `status`, `priority_rank` or `value_summary` values were kept or
  broke the file. Field edits now replace the whole value, including its
  continuation lines, and leave the next key untouched.
- **`init` wrote a `root: work/` it never used** (#112, decided in #109). It now
  defaults `--path` to the theme's own root (`kanban-work/` for software and
  nautical, `research/` for hdd), writes it as `root:`, scans just that root
  (so new types are covered) and no longer creates a stray `work/`. `--path`
  still wins. A fresh `init --theme spec` followed by `create` no longer crashes.
- **Each item type now goes into its own named folder, and the board always
  scans the folders it writes into** (#113, decided in #109; supersedes #111).
  A type whose theme folder the board didn't scan used to nest inside another
  type's folder (`kanban-work/expeditions/voyages/`), and a type the theme
  doesn't define fell back to the bare root and could vanish. Both now go to
  `<board root>/<type folder>/` (`kanban-work/voyages/`, `kanban-work/expeditions/`),
  and those folders are always scanned. Boards that already worked list exactly
  the same items.
- **Multi-board `create` without a board ignored `default_board`** (#114,
  decided in #109). When several boards' themes define the type, the item now
  goes to `default_board` if its theme defines it. Otherwise it goes to the first
  board in config order, as before.


- **An item whose opening frontmatter line carries a YAML comment
  (`--- # generated`) was silently dropped** (#116). The opening `---` may now
  be followed by a space and a comment, as YAML allows, for both reading and
  writing.
- **An agent name with a quote, backslash or newline broke the item's status
  history** (#120). `kb:by "<agent>"` was written unescaped, so `x"y` made the
  yurtle block invalid Turtle, and a crafted name could inject triples. It's now
  escaped per Turtle string rules, and the status-history reader unescapes it.
  Plain names are written exactly as before.
- **`board-add` silently dropped every item when the single-board scan paths
  share no common parent** (#122). A multi-board board scans one path, and none
  covered e.g. `a/` and `b/`. `board-add` now refuses the upgrade, names the
  scan paths it can't cover, and leaves `.kanban/config.yaml` unchanged.
- **Every frontmatter value `create`/`update` writes now reads back unchanged**
  (#121, extending #104). `--tags "team: core"` was stored as a dict, `yes` as
  `true`, and `#core` or `*a` broke the file. Tags, `depends_on`, `related`,
  `superseded_by`, `resolution` and `compute_requirement` are now quoted when
  YAML would misread them. Inside a flow list, `a, b` stays one element.
  Titles and value summaries escape backslashes and newlines too. Ordinary
  values are written byte-for-byte as before.
- **Priorities are validated once, in the service** (#125). A hook
  `create_item` action or a direct service call could still write
  `priority: urgent`. `create_item`, `create_item_and_push`, `update_item` and
  template creates now lowercase the priority and reject anything outside
  critical/high/medium/low, and MCP accepts any case like the CLI. Existing
  items with legacy values (`P0`, `normal`, `backlog`) still load and rank.
- **Frontmatter edits orphaned list items after a comment line and turned CRLF
  files into LF** (#128). A column-0 `# comment` inside a block list now stays
  with its value when more items follow. A comment before the next key is kept.
  Every edit of an existing item file (move, rank, comments, parent links,
  backfill) now keeps the file's own line endings.
- **`init --path <dir>` scaffolded the theme's `kanban-work/*` folders, which
  nothing then scanned** (#134). With an explicit `--path`, the type folders and
  their `_TEMPLATE.md` files now go under that root (`custom/features/`, …),
  which is where `create` writes. A default `init` is unchanged.
- **A scanned `.md` that starts with `---` but doesn't parse is reported, not
  dropped silently** (#139). `list` and `board` print one stderr line per
  file, naming it and giving a reason (no closing `---`, YAML error, not a
  key: value mapping, invalid opening line), so `--json` output stays clean.
  Plain notes, `_TEMPLATE*` files, ignored paths and Yurtle documents with
  Turtle frontmatter (`@prefix …`) stay silent.
- **HDD and epic templates broke on titles with quotes or backslashes** (#142).
  A title like `is "stale" vs "fresh"?` made the item vanish (this is how
  nusy-product-team's IDEA-003 broke), `\b` became a backspace, and a trailing
  backslash crashed the create. Template titles, units, categories and targets
  are now written as escaped YAML double-quoted strings, and backslashes are
  never treated as regex escapes. Ordinary values render exactly as before.
- **Turtle literals built from titles, targets, units, ids and tags weren't
  newline-safe** (#141). A title like `p\nq` produced invalid Turtle and the
  item lost its knowledge graph. There's now one ECHAR-complete escaper
  (`models.turtle_string`, from #120) used everywhere, and the HDD template
  engine inserts the generated block without re-reading its escapes. Ordinary
  values are written byte-for-byte as before.
- **Multi-board: a type no board's theme defines went to the first board, not
  `default_board`** (#144). Unrouted types now go to `default_board` when it is
  set, and the first board otherwise, completing #114.
- **`board-add` also refuses when legacy per-type paths (`paths.features`,
  `bugs`, `epics`, `tasks`) fall outside the default board, and it no longer
  crashes on mixed absolute and relative scan paths** (#147, extending #122).
  Either case used to drop items silently or print a traceback. `list` no
  longer crashes on mixed scan paths either.
- **A non-string priority (e.g. `priority: 1` in a hooks config) now gets the
  same clear refusal as `urgent`** (#153), not `'int' object has no attribute
  'strip'`. `indexer.py` is documented as unused and single-board-only.
- **Epic linking re-wrote `related` unquoted, and characters YAML forbids broke
  the frontmatter** (#148). Linking an item to an epic split a `related` element
  like `"a, b"` in two. It now uses the shared flow-list writer. Quoted values
  escape characters YAML won't take literally (DEL, C1 controls, NEL, U+FFFE),
  so such a title no longer makes the item vanish. Printable non-ASCII text
  is written as before.
- **A board root outside the repo (`init --path /abs/dir/`, `root: /abs/…`)
  crashed `create` and `list`** (#156). Single-board ignore matching now uses the
  absolute path for files outside the repo, so such boards work and their
  `archive/` and `_TEMPLATE` files stay hidden.
- **Unparseable-item warnings are tighter and cover more cases** (#158). A
  broken YAML item whose first key starts with "prefix"/"base" (`base : x`) is no
  longer mistaken for Turtle frontmatter. Files that can't be read (non-UTF-8) or
  that crash after their frontmatter parses (e.g. `status: [backlog]`) are
  reported too. `show <ID>` names the file and the reason when the ID's file
  exists but doesn't parse.
- **Edits keep every line's own line ending** (#151, extending #128). A file
  with mixed endings is no longer rewritten wholesale to one ending: untouched
  lines keep theirs, and changed lines use the file's majority ending. Epic
  linking and `update` now keep CRLF files CRLF, and `update` keeps the file's
  final newline.
- **Query and `rank` escaping** (#162). The structured query API
  (`QueryEngine.structured_query`) put assignee and tag values into SPARQL
  literals raw, so a quote or backslash broke the query and
  `zz") || contains("", "` matched every item. They're now escaped.
  `rank --summary` escaped only quotes, so a trailing backslash made the item
  vanish and `\b` or a newline was misread. It's now fully quoted.
- **No template substitution re-reads a value as a regex escape anymore** (#161,
  extending #142). `--hypothesis 'H1\'` crashed `experiment create` with
  `re.error`, `--authors 'A\d'` crashed `paper create`, and `\b` or `\g<0>` in
  authors corrupted the file. IDs, paper refs and authors now use function
  replacements, and authors go through the shared flow-list writer. An ID the
  Turtle builder refuses is a clean `Error:` naming the value, not a traceback.
- **Epic linking handles block-style and empty `related:` values** (#169).
  Linking an item whose `related:` was a block list (`- FEAT-009` lines)
  left the old lines behind, broke the YAML, and dropped the item from the
  board. `related: null` crashed. Linking now uses the shared frontmatter
  writer, which replaces the whole value.
- `query` with two or more types or statuses (e.g. "papers with pending hypotheses") no
  longer fails with `ParseException: Expected SelectQuery, found 'FILTER'`: the SPARQL
  `IN` lists are comma-separated (#64).
- `board` draws every item in the column that counts it: on themes whose columns aren't
  canonical status names (hdd `draft`/`active`, spec `proposed`…), a column header said
  `Draft (1)` over an empty column (#87).
- **CLI output no longer interprets Rich markup in user or file text** (#179): titles,
  IDs, comments, descriptions, summaries, paths and error messages now print verbatim
  in every command. `create feature 'a [/b] c'` crashed with a `MarkupError` after the
  file was already written, and a title like `[bold]x[/bold]` silently lost its
  brackets; board cards now fold long titles instead of cropping them. An empty
  `status:` or a numeric/empty `type:` now falls back to the default (backlog / task)
  instead of dropping the item with an `AttributeError` warning.
- **Editing a file with genuinely mixed line endings was quadratic** in its length:
  `difflib` ran over the whole file, about 21 s at 20k lines. Now only the changed
  middle is diffed; the unchanged prefix and suffix map line for line. A 50k-line
  edit takes milliseconds, and the endings kept are unchanged (#181).
- **Validators anchored with `$` accepted a trailing newline** (#183). The
  experiment-run ID check in `create_experiment_run`/`get_experiment_runs` accepted
  `EXPR-130\n`, and query links turned `EXPR-1\n` into an item URI. Both now use
  `re.fullmatch`. HDD commands turn only the Turtle builder's new
  `InvalidTurtleName` into a one-line `Error:`, so any other `ValueError` from
  rendering surfaces as a real error instead of being hidden.
- **The structured query compared tag and assignee values after rdflib had
  rewritten them** (#184). rdflib expands `\uXXXX` escapes across the whole query
  text, so a literal `\U00000022` needle matched `a"b` and `aAb` broke the
  parse. The needles are now bound `Literal`s (`UnifiedGraph.sparql(...,
  bindings=)`), never query text. Also reworded the #162 entry: only the
  structured API was affected.
- **`safe_merge.sh` is tied to the reviewed head** (#186). It reads the PR head
  once and refuses if `origin/<branch>` differs from it, or if the latest
  `reviewed-at-sha:` verdict isn't `approve` at that head. It then merges with
  `--match-head-commit`. A missing branch ref is now reported as missing rather
  than as a conflict. Checks are filtered inside `jq`, so a check name with a
  newline no longer garbles the report, and the tests skip cleanly without `jq`.
- **Frontmatter and epic-link follow-ups to #169** (#188):
  - Adding a missing frontmatter key no longer drops the blank lines before the closing `---`.
  - Linking an item whose frontmatter exists but doesn't parse now reports "frontmatter doesn't parse" with the reason, instead of "No frontmatter".
  - A `related:` that is a mapping or a number is refused, not rewritten as `["{...}"]`.
  - `epic add` and `epic create --items` say "already linked" only when the item really is.
- **"Unknown priority" now shows the value the same way everywhere** (#190). The
  service quoted a string (`'urgent'`) while MCP and the CLI didn't. Printable
  strings now appear as typed and anything else as its repr (`5`, `True`,
  `['high']`, `'a\x1bb'`), so control characters never reach the terminal raw.
  `unknown_priority_message` does the rendering itself.
- `hdd registry` on a board whose root is outside the repository writes `REGISTRY.md` in
  that board root instead of the repo's `research/`, and `--push` skips git there with
  the outside-repo warning (#192).
- **Every CLI command now refuses undecodable argv bytes up front** (#193).
  #172 covered `create`, `update` and `comment`. `epic`/HDD creates still showed a
  traceback. `rank --summary` and `next-id` wrote and committed first and then
  crashed. `experiment run` and `paper --authors` saved the text escaped. The
  root command group now checks every argument (`argument N contains invalid
  UTF-8`) before any command runs.
- **An empty `ignore:` key crashed every scan** with `'NoneType' object is not
  iterable`. A bare `ignore:` under `paths:` or a board now means "no ignore
  patterns", and saving a config with an empty list keeps it empty instead of
  reloading as the defaults (#194).
- **`scripts/assemble_changelog.py` hardening** (#197).
  - Fenced code blocks under `## [Unreleased]` are no longer split on a `### ` or `## [` line inside them.
  - A CRLF CHANGELOG stays CRLF.
  - An empty fragment is refused.
  - `## [Unreleased] - TBD` keeps its text on the heading.
  - `--date` must be a real YYYY-MM-DD.
  - A failed write keeps every fragment and exits cleanly.
  - CONTRIBUTING now says that this step replaces step 4 of the release skill.
- **Follow-ups to #174 for boards outside the repo** (#198).
  - The README now says that ignore patterns for a board outside the repo (absolute or `../`) match the file's absolute path, so they should be `**/…` globs.
  - `init` no longer crashes when `git` isn't on PATH. It shares one `git_toplevel` helper with the service.
  - A relative `repo_root` no longer doubles every path handed to `git add`, which made commits fail with exit 128.
- **Line-ending preservation, follow-ups to #181** (#202):
  - An edit whose changed middle is large no longer goes to `difflib` whole. It is split on lines that occur once on each side (patience diff). A middle with no such lines, such as runs of identical lines, maps position for position. Two far-apart edits in a 50k-line mixed file now take milliseconds.
  - A kept lone `\r` followed by an inserted blank line is no longer written as one `\r\n`, which dropped the blank line on re-read.
- **More bare config keys no longer crash** (#204, following #194).
  - A bare `kanban.paths:`, `paths.scan_paths:`, `boards:`, `boards[].scan_paths:`, `boards[].wip_exempt_types:`, `gates:` or `workflows:` now means empty. A bare `boards:` is the same as having none.
  - A single `ignore:` pattern written as a string is that one pattern, not a list of its characters. Before, it hid every item, and `board-add` wrote it back split into characters.
- **Numeric `assignee:`, `priority:` or `value_summary:` in frontmatter crashed
  `list`, `roadmap` and `history`** (#206). They are now read as text, like `id` and
  `title` since #179. On a narrow terminal, board cards still show the ID and
  title whole, but tags and the assignee wrap between words (or end in "…")
  instead of being split mid-word.
- **`safe_merge.sh` no longer discards uncommitted work** (#208). It force-removed
  the PR's worktree before merging, so modified, staged or untracked files there
  were lost, even when the merge was then refused. It now refuses while the
  worktree has uncommitted changes. The pairit skill also notes that a verdict
  comment must start exactly with `reviewed-at-sha:`.
- **Appending a frontmatter key after a trailing `|+` / `>+` block scalar lost the
  value's kept newlines** (#211). The new key now goes after the scalar's
  trailing blank lines, so the value is unchanged. Also removed a dead guard
  left from #188.
- **Line-ending preservation keeps more endings in large repeated regions and runs
  faster** (#213). Large changed regions are split on unique lines repeatedly,
  within a budget linear in the file size, instead of only once, so a 50k-line
  file with repeated blocks keeps its CRLF lines. A smaller per-segment `difflib`
  budget makes many mid-sized edits about 5× faster.
- **Warnings no longer pass control characters from repo files to the terminal**
  (#215). A hook file's item type, title or action type reached stderr raw
  through `Hook create_item failed: …` and `Unknown action type: …`. An ESC
  sequence could clear the screen and a newline could forge a log line. Every
  package logger now escapes non-printable characters (`\x1b`, `\n`) in warnings
  and errors. Debug output is unchanged.
- **An empty or space-padded rejected priority is now visible in the message**
  (#216). `'Urgent '` showed as `Unknown priority: Urgent ; …` and `''` as an empty
  slot. Such values are now quoted (`'Urgent '`, `''`); `urgent` still appears
  as typed.
- **The service and MCP refuse undecodable text on every write path** (#219).
  #193 covered the CLI. `rank_item(value_summary=…)`, `allocate_next_id` (and MCP
  `kanban_next_id`) and `create_experiment_run` (being, run_by, params) now
  refuse a lone surrogate before writing or committing anything. Template
  rendering refuses one in any variable, where paper `authors` had slipped past
  as escaped text. `check_encodable` also checks tuples.
- **More config nulls and wrong types handled** (#220, following #204).
  - A bare `root:`, `theme:`, `boards[].path:`, `name:` or `preset:` now means its default.
  - A bare list entry under `boards:` is skipped.
  - `ignore: 5` or a mapping is refused with a clear `Invalid .kanban/config.yaml: ignore: …` error instead of a traceback. Before, a mapping was silently read as a list of its keys.
  - A bare `boards:` with `namespace:` or `default_board:` warns that those keys are ignored.
- **`assemble_changelog.py` follow-ups to #197** (#223).
  - A fragment with an unclosed code fence is refused by name. It would have broken every later release.
  - A line like ```` ```x``` inline ```` no longer opens a fence (CommonMark).
  - If deleting fragments fails after the CHANGELOG was written, the error says so and names every fragment that remains.
- **A YAML boolean in `assignee:`, `priority:` or `value_summary:` reads as
  `true`/`false`**, YAML's spelling, instead of Python's `True`/`False` (#225). A test
  now also pins that a long ID on a narrow board card folds instead of being cut.
- **`safe_merge.sh`'s uncommitted-changes check can't be switched off by git
  config** (#229). With `status.showUntrackedFiles=no` set, an untracked file in
  the PR worktree didn't show up and was deleted with the worktree. The check now
  runs `git status --porcelain --untracked-files=normal --ignore-submodules=none`.
- **Appending a frontmatter key keeps every existing value exactly** (#232).
  Trailing spaces on a block scalar's last line are no longer stripped. The
  placement before or after a trailing blank run is now chosen by checking that
  the parsed YAML is unchanged, so `|+`/`>+` values keep their newlines under
  quoted keys, nested keys, list items and `-`-prefixed keys too.
- **Log escaping now covers child loggers and exception text** (#234, following
  #215). The escaping moved from a per-logger filter to a log-record factory, so
  every `yurtle-kanban*` warning is escaped when it is created, including those
  from child loggers made with a plain `getLogger`. An exception message in a
  logged traceback (`logger.exception`, `exc_info=True`) is escaped too, while
  the traceback keeps its layout.
- **`list --priority` ignores empty segments and reports each bad value** (#238).
  `high,,low` and `high,` now filter as intended instead of failing on `''`. Only
  commas gives "No priority given". Each unknown value gets its own message, so
  the joined list is no longer quoted as one string (`'x, '`).
- **More defence against undecodable text** (#239, following #219).
  - `move_item` (assignee, message, closed_by), `update_run_status` (status, outcome) and `update_parent_turtle_block` (child_id) refuse a lone surrogate before writing.
  - `check_encodable` checks nested lists, tuples and dicts, keys included, and raises a dedicated `InvalidText`.
  - Every CLI command group shows `InvalidText` as a one-line error. Other exceptions keep their traceback (#183).
- **An explicit empty string in config keeps its meaning again** (#241). #220 made
  every falsy value fall back to a default, so `boards[].path: ""` (the repo root)
  silently became `work/`. Now only a bare (null) key means the default.
- **`assemble_changelog.py`'s unclosed-fence error gives the opener's line**
  (#243), counted in the CHANGELOG or in the fragment file, e.g.
  `unclosed code fence (```) opened at line 3`.
- **`title: true` / `id: true` read as `true`, not Python's `True`** (#245), the same
  as the other text fields since #225.
- **`safe_merge.sh` refuses when the PR's worktree is mid-rebase or hides edits**
  (#247). A worktree in the middle of a rebase of the branch has a detached HEAD,
  so it went unnoticed. Files marked skip-worktree or assume-unchanged hide
  their edits from `git status`. Both now stop the merge with a clear message.
- **Frontmatter append: faster, with better fallbacks** (#249, following #232).
  - With no blank run before `---` (the common case), a key is appended with no YAML parsing.
  - Otherwise the layout check uses libyaml when available.
  - A `.nan` value no longer defeats the check.
  - Frontmatter that doesn't parse falls back to spotting a column-0 `|+`/`>+` last value, so its kept newlines survive.
- **CLI error and warning lines no longer pass control characters to the
  terminal** (#251, following #215/#234 for logging). Rich's `escape()` only
  escapes markup, so a gate message from `.kanban/config.yaml` or an item ID
  typed on the command line could clear the screen or forge output lines. Error
  lines now go through one helper that also shows control characters as
  `\x1b`/`\n`.
- **A logged SyntaxError's source line no longer ends in a literal `\n`** (#252).
  The escaping from #234 turned the line's own trailing newline into visible
  text. That newline is now kept as layout, and only control characters inside
  the line are escaped.
- **An empty or blank `theme:` / `boards[].preset:` now warns** (#256). It loads
  no theme, so the board has no WIP limits or workflows. The value is kept as
  written (#241), but a warning suggests removing the key to get the default.
- **`assemble_changelog.py` splits lines on `\n` only** (#260). A form feed, a
  `\u2028` or a lone `\r` inside an entry no longer shifts reported line numbers,
  and no longer turns text after it into a `###` or `## [` heading.
- **A frontmatter value that contains itself no longer crashes JSON output**
  (#262). A YAML anchor used inside its own node (`tags: &a [x, *a]`) made
  `list --json` and `export --format json` fail with "Circular reference
  detected". Such a field is now ignored with a warning and the item is still
  listed. A hook's `create_item` with cyclic `tags` creates the item without them.
- **`safe_merge.sh` gives the right hint for a sparse-checkout worktree** (#265).
  Every file outside a sparse cone is skip-worktree, so the merge is still
  refused. The message now says to run `git sparse-checkout disable` instead
  of "clear the flag".
- **Frontmatter append on unparseable frontmatter picks the layout more carefully**
  (#268). A column-0 `# comment` after a `|+` value, or a `: |+` inside a comment
  (`k: v #: |+`), no longer moves the new key after the trailing blank lines. A
  test also pins the libyaml loader.
- **Every CLI error and warning line now escapes control characters** (#270,
  following #251). This covers `show`'s "doesn't parse" reason, the `validate` and
  `hdd validate` findings, epic warnings, metrics and git-failure messages, and
  every "Unknown status/type/…" line. A test now fails if a new red or yellow line
  prints a value without escaping it.
- **`theme:` / `boards[].preset:` must be a theme name** (#272). A number, list or
  mapping is refused with a clear `Invalid …config.yaml` error; a list or mapping
  used to crash with `unhashable`. An unknown name still loads but warns, listing
  the themes that exist, built-in and in `.kanban/themes/`.
- **`safe_merge.sh` names every reason a file's edits are hidden** (#276). A
  sparse worktree that also has an assume-unchanged file now gets both hints. A
  legacy shared `core.sparseCheckout=true` with no sparse patterns no longer gets
  the sparse hint for a hand-set skip-worktree file.
- **A "billion laughs" frontmatter value no longer hangs or floods output** (#277).
  Shared YAML aliases nested a few levels deep can expand to billions of values;
  `list` hung and `board` printed hundreds of MB. A field that expands past
  10,000 values is now ignored with a warning, in the item and in its RDF graph,
  and the item is still listed.
- **Frontmatter nested too deeply for the YAML parser gets a clear warning**
  (#280). Such a file is still skipped, but the warning now says so plainly
  instead of `RecursionError: maximum recursion depth exceeded`.
- **More output lines escape control characters** (#285, following #270).
  - `validate`'s file lines.
  - `hdd critical-path`'s Chain, Implements and Assignee lines, and the blockers' Assignee line.
  - The hdd parent-update line.
  - Every hdd create `File:` line.
- **Two repos in one process no longer share a theme override** (#287). The theme
  cache was keyed by name, so the first repo to load `nautical` decided it for
  every repo, for example in the MCP server or a dashboard. It is now keyed by
  the theme file that wins the lookup, and `KanbanConfig.get_theme()` looks in the
  config's own repo.
- **`safe_merge.sh` sparse detection, follow-ups to #276** (#288).
  - A cone-mode sparse checkout that keeps only root files now gets the `git sparse-checkout disable` hint.
  - A file whose path contains "assume-unchanged" no longer triggers the clear-the-flag hint.
- **The last hdd metadata lines escape control characters** (#295, following
  #285). This covers the blockers view's `Status:`, critical-path's `Runs:` and
  `experiment run`'s `Path:`.
- **Every graph-building path is guarded against "billion laughs" frontmatter**
  (#296, following #277). `hdd backfill` and linking a new hypothesis or
  experiment to its parent no longer hang. A field that aliases an anchor inside a
  dropped field is blanked for the graph too, so the item keeps its other triples.
- **Frontmatter too deep to parse gives a clear warning in epic linking too** (#297,
  following #280). The frontmatter reader and the unparseable-reason helper now
  treat a `RecursionError` from the YAML parser as "frontmatter nested too deeply
  to parse", instead of letting it escape as a traceback.
- **A config built directly (not loaded) resolves themes in the service's repo**
  (#300, following #287). `KanbanService` fills in `config.repo_root` when it is
  unset, so a repo's `.kanban/themes/` override is found whatever the cwd. A loaded
  config keeps its own repo root.
- `hdd registry --output`, `hdd critical-path --agent` and `experiment run`/`status` echo their argument through `safe()`, so a control character in it can no longer forge terminal output (#306).
- `experiment run` no longer crashes with `AttributeError` when the experiment file was rewritten since the scan and its frontmatter no longer parses to a mapping; the run is recorded with an empty hypothesis (#309).
- The HDD `* create` confirmation lines, the `experiment status` run table, the
  `critical-path` experiment rows and its `--dev-blockers` rows, and the `backfill` table
  print their values through `safe()`, so a control character in a title, ID or run field
  can no longer forge terminal output (#317).
- Frontmatter that parses to a YAML list or scalar is treated as unparseable everywhere, so HDD cross-references, link validation, readiness and `backfill` skip such a file instead of crashing with `AttributeError`; an empty `hypothesis:` is recorded in a run's config.yaml as `""`, not `null` (#321).
- A hooks or workflow file whose frontmatter is a YAML list or scalar is treated like
  broken YAML (no config): hooks no longer log an `AttributeError` warning, and
  `parse_workflow_file` no longer raises one to its caller (#330).
- `KanbanService` accepts a `str` repo root as its signature says (it raised `TypeError`),
  and a relative root no longer makes workflow lookups depend on the current directory
  (#336).
- A config, theme or experiment-run YAML file that parses to a list or scalar no longer
  crashes with `AttributeError`/`TypeError`: the config is reported as invalid, the
  theme is treated as unknown (with one warning), a bad run is skipped in listings, and
  `update_run_status` refuses it with a `ValueError` (#338).
- `query` without the `search` extra (numpy, sentence-transformers) no longer crashes with
  `ModuleNotFoundError: numpy`: a natural-language query falls back to graph-only results
  with a one-line hint, and `query --semantic` exits with a one-line install hint (#346).
- Hooks run by `KanbanService` resolve relative `log` paths against the repo and run
  `shell`, `nats_publish` and `notify` subprocesses in the repo, not the process cwd, so
  a command run from elsewhere no longer writes `.kanban/hooks.log` there (#347).
- `KanbanService(hooks_config=...)` and `HookEngine(...)` accept a `str` path, as a `Path`
  (#348).
- `query` returns each item once, even when it matches through two types, tags or
  assignees (for example a second `rdf:type` in its yurtle block) (#349).
- A theme whose `columns`, `item_types`, `status_mappings`, `transitions`, `id_formats`,
  `status_aliases` or `theme` section is not a mapping (a list, scalar or null) no longer
  crashes `board`, `list`, `create`, `init`, `move` or `epic create`: the section is ignored,
  with one warning, as if absent (#351).
- A broken local theme override, whether unparseable or not a mapping, now falls through to
  the next theme (the built-in) with one warning naming the file. Before, an unparseable
  one fell through silently and a list or scalar one left the board with no theme.
  "Available:" theme lists leave out names that don't load (#352).
- `QueryEngine.structured_query` binds status and type values as IRIs instead of splicing
  them into the SPARQL text, so a `ParsedQuery` value like `done.` or `user story` can no
  longer break or inject into the query; the NL decomposer no longer repeats a status
  ("blocked and stranded") (#355).
- Hooks: `trigger()` no longer changes the caller's `HookContext`, so one context reused
  across engines runs in each engine's own repo. Values substituted into a `log` path
  template are path-safe: a title like `../../x` or an ID with `/` can no longer add
  directories or write outside the configured folder (#357).
- `query`: a search extra that is installed but won't import (e.g. torch missing) falls
  back to graph-only, or makes `--semantic` exit with a one-line install hint, instead of
  a traceback. `--json` with no results prints `[]`, and the `--semantic` error no longer
  wraps (#358).
- `HookEngine(repo_root=...)` accepts a `str` root like a `Path`, so hook actions always
  see a `Path` (#359).
- A theme column or item type that is not a mapping, or a `theme.name` that is not a
  string, is ignored with one warning instead of crashing `board`, `list`, `create`, `init`
  or `epic create` (#363).
- A theme file that is empty (`{}` or blank), or has nothing left once its bad sections
  are ignored, no longer shadows the built-in theme: it falls through with a warning
  ("is empty", or one per ignored section), and a custom one counts as unknown. A
  symlink override that can't be followed (dangling or a loop) now warns instead of
  being skipped silently (#365, #381).
- `QueryEngine.structured_query` skips status and type values that can't be IRIs (a space,
  quote, or one of `<>{}|\^`) instead of building them, so rdflib no longer logs "does not
  look like a valid URI". Such a value still matches nothing as an include or type filter,
  and excludes nothing (#367).
- Hook `log` path templates also map `:` to `_` in substituted values (no Windows
  drive-relative escape) and render an empty value as `_`, so `logs/{assignee}/x.jsonl`
  keeps its segment and `{assignee}` alone still names a file (#369).
- `query --json` keeps stdout pure JSON: the `--verbose` parse block and error messages
  (bad SPARQL, missing or broken search extra) go to stderr. A broken sentence-transformers
  reports its real import error, the graph-only hint says "unavailable", and graph-only
  results honour `--top` (#371, #360).
- `query` returns an item once even when its yurtle block gives it a second `kb:numericId`;
  it is ordered by its highest one (#373).
- Hook actions always see a `Path` repo root, even when a caller builds a `HookContext`
  with a `str` `repo_root` (#375).
- `query --top` rejects 0 and negative values with a usage error instead of printing
  nothing or dropping the last results (#377).
- A theme column's `wip_limit`/`order` that is not a whole number, an item type's
  `path`/`id_prefix` that is not text, or a column with a non-text id is ignored with one
  warning instead of crashing `board`, `list` or `create` (#378).
- `query` orders and range-filters items by the numeric ID derived from their own ID: a
  `kb:numericId` in an item's yurtle block is ignored, so a string value no longer sorts it
  to the top and an extra value no longer satisfies an id range (#385).
- `query --sparql --json` honours `--top` (default 20), returning the same rows as the table
  (#387).
- A hook context whose repo root isn't a path (an int or bytes set by a library caller)
  no longer makes `trigger()` raise: it warns and uses the engine's root (#388).
- A theme with an empty `columns` section (written as `{}` or emptied by ignored entries)
  now gets the default columns instead of a board with none that hid every item, and a
  whole-number float `wip_limit`/`order` such as `3.0` is read as the integer (#391).
- A yurtle block can no longer redefine the facts an item's frontmatter owns (`kb:id`,
  `kb:status`, `kb:title`, `kb:priority`, `kb:created`, `kb:priorityRank`,
  `kb:description`) for any item, so `query` status and id filters and ordering follow the
  frontmatter; added types, tags, assignees and relations still merge (#395).
- `query` notes on stderr when it cut the results to `--top` ("showing the first N
  results"), in every mode including `--json`; `docs/query.md` says `--top` caps JSON too
  (#397).
- A hook context root whose `__fspath__` raises (any error, not only `TypeError`) warns
  and falls back to the engine's root instead of escaping `trigger()` (#399).
- A negative theme `wip_limit` is ignored with a warning instead of marking the column
  always over its limit; `wip_limit: 0` means no limit everywhere, including WIP
  violations and `move` checks; and multi-board default columns get the same limits as a
  single board (Ready 5, In Progress 3, Review 2) (#402).
- In the query graph, `<>` in an item's yurtle block now means that item: its types, tags
  and `move` history hang off the item instead of one node shared by every file (the
  parser resolved `<>` against the current directory) (#404).
- `WorkItemIndexer` ignores blank-node subjects when it reads an item's type, id and status,
  so a `kb:statusChange` history entry (or any nested node) no longer decides them; the
  `query --sparql` examples filter to IRI subjects (#407).
- A hook context root whose `repr()` also raises no longer escapes `trigger()` from its
  own warning; the warning names its type instead (#408).
- A per-type WIP limit of 0 no longer blocks `move` (0 means no limit, as on the board).
  Board WIP limits from `config.yaml` and `wip-policy.md` are checked like theme ones: a
  negative, fractional or non-number limit is ignored with a warning instead of marking a
  column always over or crashing, and `4.0` is read as 4 (#411).
- `<>` in an item's yurtle block maps to the item in the query graph even when the
  current directory changed between scanning and building the graph (library callers)
  (#413).
- `WorkItemIndexer` takes an item's type only from `rdf:type` and its id and status from
  that typed subject, so a `kb:related kb:Feature` link or a second subject in the file
  no longer changes them (#416).
- Hook warnings never raise on caller-supplied values: a failing action whose error can't
  be printed, a non-mapping entry in `actions:` (now skipped with a warning), or an
  unprintable item id at the depth limit no longer escape `trigger()` (#417).
- Saving config.yaml (e.g. `board-add`) keeps every board's `wip_limits` exactly as written;
  bad values are only ignored at runtime, not erased. A per-type WIP map whose entries are
  all ignored leaves the theme's limit in place, and `board-add --wip-limit` rejects a
  negative value (#420).
- The IRI `<>` resolved to at parse time is kept in a registry keyed by the graph object
  instead of an attribute set on rdflib's `Graph`, and `WorkItemIndexer` graphs record it
  too, so their `<>` facts map to the item in the query graph (#421).
- A hooks file with a part of the wrong shape (non-mapping `hooks` or hook, non-list event,
  `actions` or `item_types`, non-text `from`/`to`) no longer crashes `create`/`move` from
  `trigger()`: the bad part is ignored at load with one warning, and the valid hooks still
  run (#425).
- `WorkItemIndexer` indexes items whose type comes only from YAML frontmatter (`type:`),
  reading their frontmatter id and status, so it no longer skips almost every real item; with
  several statuses on an item it picks deterministically (#426).
- Saving config.yaml writes a board's WIP limits as changed in code (the user's original
  text is kept only while it still describes the board), and a copied or pickled board
  config no longer saves a corrupt `wip_limits` (#428).
- Recording which IRI `<>` meant can no longer fail a graph parse (a graph that can't be
  weakly referenced just falls back to the current directory), and a late cleanup can't
  erase a newer graph's entry (#430).
- Hooks files: an unknown event name (e.g. a typo like `on_created`) is ignored with a
  warning instead of silently never firing; `item_types: expedition` works as a one-item
  list again; and a YAML boolean in `from`/`to` (`on`, `yes`) says to quote it (#432).
- On themes with their own status names (hdd `draft`/`active`/`complete`/`abandoned`), the
  `list` table shows those names, `create` writes the theme's initial status, single-board
  `move` writes the theme's name as multi-board already did, and a scan reads every such name
  back (`abandoned` was read as backlog). `--json` output and `--status` filters stay
  canonical (#439).
- A hooks file with `on_stale` or `on_wip_exceeded` hooks now warns at load that
  yurtle-kanban doesn't emit those events yet, instead of the hooks silently never running
  (#440).
- `move` enforces WIP limits on columns named by the theme (hdd `active`, spec
  `implementing`/`proposed`): it found the target column by id, which never matched those,
  so a full column still took the item (#442).
- The "declared but not emitted yet" hook warning (#440) is only logged when a valid hook
  for that event survives the shape checks, so a malformed entry gets one accurate
  warning instead of two (#446).
- `show`, `next`, the `move` and `rank` confirmations, `roadmap` (table and `--export md`)
  and `query`'s tables name statuses the way the item's theme does (hdd `active`), like
  `list` (#439); JSON stays canonical. A scan reads a theme status name through the item's
  own board, so two boards' themes can give one name different meanings (#448).
- On a single board, `move` checks the configured theme's own transitions (hdd lets
  draft→active and forbids abandoned→active), as a multi-board board already did, instead
  of the default workflow; refusals name the theme's statuses (#450).
- A theme override written while a service is running (e.g. the MCP server) places new
  items at once again; the theme is memoised only for the length of a scan (#454).
- A theme `transitions` entry that isn't a list is ignored with a warning (a lone status
  name counts as a one-item list) instead of crashing `move`, and
  `get_allowed_transitions` answers from the theme's transitions as `move` enforces them
  (#457).
- Outside a scan, a theme status name added since the last scan is recognised at once
  (long-lived services such as the MCP server); board-level reads (`get_board`,
  `get_items --board`) share one theme lookup per read (#459).
- A theme transitions list keeps only its status names (a number or mapping in it is ignored
  with a warning), so `get_allowed_transitions` no longer crashes on one; it lists each
  status once and only statuses `move` accepts (#461).
- `get_allowed_transitions` no longer offers a status listed under its canonical spelling in
  a theme that names it differently (hdd `in_progress` for `active`), which `move` refuses
  (#467).
- The skills push guard now checks every `&&`/`||`/`;`/`|`/`&` segment and looks past `sudo`/`env`/`KEY=val` prefixes, and no longer treats `-o -n` as a dry run (#472).
- `get_allowed_transitions` offers exactly the statuses `move` accepts, including a status
  whose theme name is also another status's canonical name (it used to leave such targets
  out) (#474).
- The skill/help command guard now checks short flags, hidden aliases, `KEY=val` prefixes, quoted values, arbitrary command depth and backtick spans, and fails on any unchecked `yurtle-kanban` mention (#476).
- `hdd registry` on a multi-board config writes the registry under the HDD board when that board is outside the git repository, and `--push` no longer commits an index of untracked files. The default location and the push skip now use one definition of "outside the repo" (the git toplevel), and the "Registry written to" path is normalised (#478).
- A leading `~` in configured paths (single-board `root`/`scan_paths`/legacy type paths/theme type paths, a multi-board board `path`) now means the home directory wherever the path is used — scan, placement, board lookup — instead of a literal `~` folder in the repo; the config keeps its `~` spelling (#479).
- `move` and the offered transitions always see theme `transitions` entries as lists of names, however the theme reached the service: one `_clean_transitions` normaliser is shared with the loader, so a lone-string entry no longer substring-matches in `move` or iterates characters in the offer (#480).
- A single-board config's `ignore:` or `scan_paths:` written directly under `kanban:`, as the README example showed, was silently dropped. It is now read when `paths:` does not set that key. If both set it, `paths.*` wins and the other copy draws a warning. The README example now uses `paths.ignore` (#482).
- The skills merge guard now reads every shell segment and treats `git switch main` like `git checkout main`, so `git checkout main && git merge …` on one line is caught. The HDD experiment skill now merges a validated experiment through its PR (`gh pr merge`) instead of a local merge on main (#485).
- The skill/help command guard now agrees with click on `--`, a value given to a flag, a missing option value, short clusters and nargs>1, and its allow-list entries are anchored (#488).
- The push and merge guards now read quotes (a `#` or separator inside a quoted commit message no longer hides a chained push), backslash continuations, the `command`/`time`/`nohup`/`exec`/`!` prefixes, an absolute git path and `then`/`do`/`else`; `-on` is no longer taken as a dry run (#489).
- A transitions warning for a theme dict that bypassed the loader now names the board and preset (or the theme's own name) instead of just `theme` (#492).
- The remaining unexpanded `~` sites are fixed: `WorkItemIndexer` scans, `init --path "~/x"` (no more literal `~` directory), and the board-root, single-board-path and `board-add` coverage checks now treat `~` and absolute spellings of one place as the same (#494).
- With nested boards, a path now belongs to the deepest board that holds it, whatever the config order (scan attribution and the hdd registry follow). A board path of `../` is now correctly outside the repo (#501).
- The skills merge guard now catches a quoted `main`, `checkout -B`/`switch -C main`, and `pull <remote> <branch>`, `rebase` and `cherry-pick` onto main. Pathspec checkouts no longer open or close the check window, and `--detach main` is no longer taken as being on main (#502).
- `scan_paths` given as a single string (single-board, kanban-level or a multi-board board) is now one path instead of a string whose characters were iterated; a non-list, non-string value is refused naming `scan_paths`, and a non-mapping `kanban.paths` is refused naming `kanban.paths` (#503).
- A test now forbids package code from installing a log handler that interprets Rich markup, so board, theme and title names in warnings always print literally (#505).
- The push and merge guards cover their remaining limits:
  - `if`/`while`/`until`/`elif` and `builtin` prefixes, relative git paths, `{ …; }` brace groups (#489), `$'…'` quotes and escaped `#`;
  - `-fo -n` and quoted `-n` values are no longer taken as dry runs;
  - `refs/heads/main`, `checkout -t origin/main`, `reset --hard` and `am` on main, and `update-ref refs/heads/main` are caught;
  - pull flags that take a value no longer cause false positives.

  What a line scanner cannot reach is documented (#507).
- Three path edge cases are fixed:
  - scan paths that share only `/` no longer produce a `//` board path;
  - `init` quotes `root` only when YAML wouldn't read it back as written (`~`, `a: b`), and escapes `scan_paths` safely;
  - a hook `log` action path the config author writes as `~/…` goes under the home directory, while a `~` coming from item data (an assignee `~x`) stays a plain path segment inside the repo (#509).
- `_repo_relative` makes a relative path absolute against the cwd before comparing it, so a bare `..` no longer counts as inside the repo (#511).
- The skill/help command guard now agrees with click on `-- <option>` at a group, and an allow-listed line can no longer hide another command mention (#516).
- Config hardening:
  - an empty `scan_paths` entry is dropped with a warning and never scans the repo root;
  - a non-string entry is refused, naming `scan_paths`;
  - only a null `kanban.paths` means empty, and `0`, `''` or `[]` are refused.

  README: the type-folder rule now says "inside the repo" (#517).
- The log-handler guard now flags any `RichHandler` reference or `markup` extra, not just direct calls (#518).
- The log-handler guard also flags `markup` attribute stores and `setattr`, a `markup` dict key or `extra=dict(markup=…)`, and any `logging.config.fileConfig` use; its message says prose mentions of RichHandler count (#524).
- The skill/help command guard now reads `-1`-style words as options, as click does, and says when an `allowed-tools` glob must come first. Its docstring states that positional-argument counts are not checked (#525).
- A whitespace-only `scan_paths` string loads as no paths, the same as null. The dropped-entry warning names its source (`kanban.paths`, the kanban level or the board) and shows the entry (#527).
- Push/merge guards: quotes inside a token (`-o'a -n'`) and long options that take a value no longer look like dry runs. `git branch -f`/`-C`/`-M main` is caught. `reset --hard @{u}`/`FETCH_HEAD` (sync) and `ORIG_HEAD`/`HEAD~N` (cleanup) and `am --resolved` are accepted. The #509 entry's `init` quoting is: `root` when needed, `scan_paths` always (#529).
- Config warnings name an unnamed board `default`, as it loads, instead of `None`. Empty scan-path warnings read `` `scan_paths` entry '' on board 'alpha' `` or `` … in kanban.paths `` (#535).
- The merge guard reads `branch` flag clusters, treats `@{push}`/`<remote>/HEAD` resets as syncs, and reads every `am` flag (#536).
- The command guard rejects a group ending at `--`, as click does (#537).
- An unnamed board's preset warning now says `board 'default'`, not `None`. The skill/help command guard rejects a bare group at the end of the input (`yurtle-kanban hdd`), as click does (#542).
- The merge guard no longer reads `am -Sr x.patch` (a gpg key `r`) or a `--continue` inside a quoted `--resolvemsg` as resume mode. The `am` resume check walks quote-aware tokens and knows which options take a value (#543).
- Merge guard: an unambiguous abbreviation of an `am` value option (`--resolvem`) consumes its value, as git does. The `am` cluster class is built from a named constant. The log-handler tests skip the mcp server only when `mcp` itself is missing; a broken internal import fails loudly (#547).
- The command guard's click differential now covers 445 shapes, including eager options combined with unknown words or options on every command path. Command callbacks are stubbed, so no real command runs (#549).
- The command guard's click comparison also covers options valid only on a parent group placed after a command (`move --version`), now 624 shapes (#553).
- The command guard's click comparison fails if no parent-only option is found, and it covers short aliases (#556).
- The MCP server's `initialize` reply reports the package version instead of a hard-coded `0.1.0` (#561).
- The MCP server's success replies (`initialize`, `tools/list`, `tools/call`) now put their payload under the JSON-RPC `result` key. Before, it sat at the top level, so standard MCP clients could not read it (#563).
- MCP server protocol fixes:
  - a failed tool call returns `result.isError: true`;
  - a notification (a request without an `id`) is never answered, even when its method is unknown;
  - `handle_request` signals errors explicitly, so a success payload is never mistaken for one;
  - an internal error reply now carries the request's `id` (#568).
- MCP server input handling (#568):
  - a malformed line gets -32700 and a non-object JSON value gets -32600, both with `"id": null`;
  - a bad line, such as invalid UTF-8 or JSON nested too deeply, no longer ends the session;
  - blank lines get no reply, now pinned by tests (#571).
- `update_item` (and the MCP `kanban_update_item` tool) edits only the changed fields; it no longer rewrites the whole file, which deleted the status history, unknown keys and native status (#583).
- Kanban commits (`move`, `comment`, `rank`, update, `create --push`, `next-id`, the HDD parent link, `hdd registry --push`, `experiment run --push`) commit only their own files: files you had staged stay staged and out of the commit, and unstaged work is untouched. A refused commit (a pre-commit hook saying no, now also run for `create --push`) is an error that shows the hook's output and pushes nothing; the edit stays in the working tree (#584).
- `create --push` fetches and pushes one explicit ref, the remote's default branch: the commit is built on the fresh `origin/<default>` without touching your worktree, index or branch, a lost race retries with a new id, and exhausted retries or an unreachable remote exit non-zero with no local commit, no stray item file and a clean tree (#585).
- `move --assign` no longer fails assignee gates/rules — validation sees the proposed item (new status and assignee), a refused move writes nothing, and the moving item never counts against its own WIP slot (#586).
- `move` resolves a status name through the item's own theme only (its native names plus the six canonical ones): another theme's name (`active` for a nautical item, `accepted` for a software one) is refused, even with `--force`, and the error lists only the item theme's legal names (#587).
- The `spec` theme's native status names (`draft`, `proposed`, `implementing`, `accepted`) now work. Its mapping used the unread `status_aliases` key, written backwards (canonical → native). Its name and description now sit under `theme:` like every other theme (#588).
- `next-id` claims its id with the same compare-and-swap push as `create --push` (fetch the default branch, commit the allocation record on it, push `<sha>:refs/heads/<default>`), never touching your branch, index or tree; a lost race retries with a new id, a refused or unreachable remote fails with git's reason, and `--json` exits non-zero on failure. HDD `idea`/`literature`/`measure`/`experiment`/`hypothesis create --push` allocate their id against the fetched base on every attempt, and the remote id scan also reads frontmatter ids, so no duplicate ids (#590).
- Built-in themes load from a checkout's own `themes/` before the pip-installed share copy. An editable install makes that copy once, so edits to `themes/` in a dev checkout used to be ignored (#592).
- `epic`/`voyage create --push` no longer crashes reading the push result. A failed push now prints the reason and exits 1 (#593).
- Frontmatter edits (`update`, `rank`, `move`) match quoted keys such as `'title':` or `"priority":` and change that line in place instead of appending a duplicate key, and `update --tags` keeps an existing block list as a block list with the same indentation (#596).
- Theme lookup treats a source tree as a checkout only when `pyproject.toml` sits beside its `themes/`, so a stray `themes/` next to an installed wheel can't shadow the share copy. Source paths are de-duplicated after resolving symlinks (#602).
- `--push` failures now show git's own reason on one clean line (including the last rejection when retries run out); a push that landed stays a success even if the local fast-forward fails; and `epic create --push` on a feature branch says to pull the default branch instead of printing a `File:` path that is not in the checkout (#603).
- Theme lookup counts a source tree as a checkout only when the `pyproject.toml` beside it is yurtle-kanban's own. The `_theme_dirs` docstring names that signal (#612).
- A theme whose `status_mappings` has an entry that isn't a string (a list, mapping, number or null value, or a non-text key) no longer crashes `create` / `move`: the entry is dropped with one warning naming the theme file and `status_mappings.<entry>`, and the other mappings keep working (#613).
- `hdd registry --push` no longer runs a bare `git push`: an unchanged registry now says so and pushes nothing, and a changed one is pushed alone to its upstream — if the branch holds other unpushed commits it refuses (exit 1) and names them, and with no upstream it warns and pushes nothing; either way the registry commit is kept locally (#614).
- A theme whose `status_mappings` keys fold to the same name (`on-hold` / `on hold` / `on_hold`, or `doing` / `Doing`) now keeps the first one: each later key is dropped with one warning naming the theme file, the dropped key and the kept one, so `move` writes the kept key and it reads back as the status it was moved to (#615).
- An item file that spells a theme status any folded way (`on_hold`, `On Hold` for `on-hold`) reads as that status (#615, #659).
- Frontmatter edits (`update`, `rank`) keep YAML comments: a trailing `# comment` on the key line is kept with its spacing (scalars, flow and block lists), and comment lines inside a rewritten block list stay right before the item they precede; a `#` inside a quoted value or without whitespace before it isn't a comment (#619).
- **ID allocation records and `experiment run` no longer record `unknown` as the actor.** `allocated_by` (`next-id`, `create --push`, with or without a remote) and a run's `run_by` now come from the same resolver as `move`: `--agent`, then `$YURTLE_AGENT`, then git `user.name`. With no identity the command is refused with the "No actor" message before anything is written, committed or pushed. `experiment run` gains `--agent` (`--run-by` is kept as an alias); a blank value is refused (#620).
- The checkout signal only counts `name = "yurtle-kanban"` inside the `[project]` table, and allows a trailing comment. Removed an orphan comment from `hdd.yaml` (#622).
- `experiment run --push` now pushes only the run's own commit: it refuses (exit 1, naming them) when other commits are unpushed, and warns without pushing when the branch has no upstream, as `hdd registry --push` does; a rejected push, of either, shows git's output folded onto one clean line (#623).
- `epic`/`voyage create --push --items` no longer links items to an epic that landed on the default branch but not in this checkout; it says to pull and then run `epic add` (or `voyage add`). Every `--push` create now prints the same "pull" note from one shared helper (#625).
- **Multi-board: one board's theme names mapped another board's columns** (#633). The
  column → status map was merged across every board (plus a hard-coded nautical/spec/hdd
  table), so a name two custom themes define differently drew items in the wrong column
  on whichever board was listed first. Each board now maps its columns through the six
  canonical names plus its own theme's `status_mappings` only; a custom board's column
  that neither names shows no status.
- `create --push` refuses an explicit id (`--id M-001`, `--id H130.1`, or `item_id=`) that the fetched default branch already holds, naming the file that holds it, and checks again on every refetched base after a lost race; nothing is pushed and your branch is untouched. A paper-scoped `hypothesis create --paper 130 --push` recomputes `H130.n` on each fetched base, so it never reuses a rival's id (#634).
- `create --push` result message (seen by API and MCP callers) now uses the same "Pushed to origin/<branch>; not in this checkout yet" wording as the CLI, and `voyage create --push --items` warns that it committed only the voyage, not "the epic" (#637).
- A long-lived service (the MCP server's) no longer serves stale item state: every write (`move`, `update`, `comment`, `rank`, parent-link and turtle backfill) re-reads that item's file before its checks and refreshes the cached item afterwards, so a second move in one MCP session is judged from the current status, reads after a write are current, and an edit made outside the service before a write is respected (#638).
- Block-list edits (`update --tags`, …) keep each kept item's line as written, trailing `# comment` and spelling included, also for items YAML reads as non-strings (`- 2026`, `- yes`, `- null`, no longer re-quoted) and for lists without a comment line; a removed item's comment goes with it, and an invalid date (`2026-02-30`) no longer raises (#639).
- `next-id --no-sync` and a plain local `create` no longer re-issue an id that is already on the already-fetched `origin/<default>`; they read that ref locally, with no network (#641).
- An explicit id that differs only in padding (`--id EXP-3`, `measure create --id M-1`) is refused against a default branch that holds `EXP-003` / `M-001` (#641).
- The id scan of the fetched default branch counts only `id:` lines in each file's leading frontmatter, not body or code-block lines (#641).
- Local `hypothesis create --paper 130` uses the same allocator as everything else, so it sees an `H130.n` that exists only as a filename or an allocation record (#641).
- A `--push` create whose text, rendered for the id allocated on a fetched base, is not valid UTF-8 is refused cleanly, and nothing is pushed (#641).
- **An unknown-status refusal listed a jumble of names** (#643). A nautical
  `move EXP-001 active` printed canonical names, native names and folded alias keys,
  alphabetised (`approaching, approaching_port, arrived, backlog, …`). `move` and MCP
  `kanban_move_item` now list each status once, in workflow order, spelt as the
  item's theme names it (`harbor, provisioning, underway, approaching, arrived,
  stranded`); a status the theme doesn't rename keeps its canonical name. Canonical
  names and aliases are still accepted, just not listed.
- **`create` refused no forged comments section** (#644): `create --body/--body-file` (with or without `--push`), `create_item` and MCP `kanban_create_item` now refuse a `## Comments` line outside a fence, with `update_item`'s message; the CLI prints one error line and exits 1, before anything is written or pushed.
- **Text between `## Comments` and the first comment heading was dropped**: it now reads as a leading comment with an empty author and `created_at` null (`show`, `--json`, MCP `kanban_get_item`, search) and stays unchanged on disk. `Comment.created_at` is optional.
- Human `show` indents every line of a multi-line comment to its text column. The `add_comment` docstring and README say that `yurtle`/`turtle` fences are stripped from comment text.
- `literature create --idea`, `hypothesis create --paper` and `experiment create --hypothesis` with `--push` put the parent's inverse link in the child's own compare-and-swap commit on `origin/<default>`, made to the parent as that base holds it (a lost race rebuilds it on the new base, keeping the rival's change). They no longer commit the link on the checked-out branch and run a bare `git push`, which could publish unrelated local commits and split the child and its link across two branches (#645).
- `states` keeps one line per state when piped or on a narrow terminal (#651).
- **Multi-board: an item whose type only a later board's theme defines was dropped.**
  A `type: spec` (or `rfc`, `spike`, `adr`, or a custom theme's type) item on a board
  that is not listed first was mapped through the first board's theme, so it vanished
  from `show` and `list`. The type is now mapped through the theme of the board that
  holds the file (#652).
- Frontmatter list fields (`tags`, `depends_on`, `related`, `superseded_by`) read every entry as text (`2026` → "2026", `yes` → "true", as #225 reads scalars) and drop null entries, so `update_item(tags=item.tags + [...])` round-trips; kept list lines and their comments stay as written (#653).
- A date or timestamp entry reads as ISO text, and a single scalar value (`tags: 2026`) as a one-entry list (#653).
- `next-id` with a remote in a dotted id space (`next-id H130.`) no longer crashes after the id has been claimed on origin (which burned the id); it reads the number the way the allocator does (#655).
- A theme `status_mappings` target spelled another way (`wip: in-progress`) is the same status as `in_progress`: `move` writes the name the refusal lists, the last one listed for that status (#659).
- Small follow-ups: a `--push` create whose title ends in a period no longer gets two; a blank `experiment run --run-by` names both spellings of the option; the changelog assembler also refuses an indented second section line (#660).
- **The separator is part of an id** (#661): an explicit id collides only with one
  that has the same text before its number, separator included, and the same number.
  `EXP-3` is still `EXP-003`, but `EXP3` no longer collides with `EXP-003`, and `H1` no
  longer collides with `H-001`. An item file with old Mac line endings (CR only) is
  skipped with one warning saying to convert it to LF. After fetching the default
  branch, yurtle-kanban records it locally as `origin/HEAD`, as `git clone` does, so
  `next-id --no-sync` and local `create` find the real default instead of a stale
  `origin/main`.
- Multi-board: `create` and `create --push` take the ID prefix from the theme of the board the new file lands on, not the first board's; a type on no board is read through every board's theme, first wins; a scan loads each board's theme once, not once per item (#665).
- **Refusals are a typed error, so a bug keeps its traceback** (#666): a refused input is now an `InputRefused` (a `ValueError`, from `yurtle_kanban.models`; `InvalidText` is one). It covers text that can't be written as UTF-8, a forged `## Comments` line, an empty or control-character identity, no actor, an unreadable `--X-file`, and a malformed experiment id. CLI `create` and `experiment run` print a clean `Error:` line only for these; any other `ValueError` now surfaces with its traceback. Every command group turns an `InputRefused` into a one-line error. MCP is unchanged.
- **Templated creates are checked for a forged comments section**: `idea`/`hypothesis`/every HDD create and `epic`/`voyage create` (with or without `--push`, including each per-base re-render when an id races) refuse rendered content with a `## Comments` line outside a fence, before anything is written or pushed. A user field that reaches the body raw (such as `{{TITLE}}` on a body line) can no longer forge comments.
- `scripts/assemble_changelog.py` refuses a fragment with a second `<!-- section -->` line, which it used to file under the first section as text; the 641, 645 and 651 fragments are split one section per file (#673).
- The edges of a `--push` create's parent link (#645): a parent a rival removed from
  `origin/<default>` is refused as "not on origin/<default> (it was removed there)"
  rather than "push X first"; when the local fast-forward is blocked by an uncommitted
  edit to the parent, the pull note names the file ("commit or stash your edit to
  <path> before pulling"); and with no remote, a parent with uncommitted edits refuses
  the create before anything is written, its link worked out before the child's file
  is (#674).
- A nested entry in a frontmatter list field (`- [a, b]`, `- {k: v}`) reads as its YAML flow text, so list fields are always text and `update_item(tags=item.tags + [...])` keeps every line instead of raising `TypeError` (#675).
- A nested entry written in block style (a bare `-` with its own indented lines) no longer corrupts the file when the list is updated: the list is written fresh as valid YAML. Flow-style fields and block-map entries are rewritten with their nested entries as strings; the text re-reads the same (#675).
- A theme's `transitions` names, source and target, now mean their status before
  legality is decided, resolved as `move` resolves a typed name (canonical first, then
  the theme's own names, folded). With `doing` and `wip` both mapped to in_progress,
  `ready: [doing]` reaches in_progress whichever name the theme writes; `in-progress`
  and a mapped status's canonical spelling work too. A transition name that is no
  status is ignored, with one warning naming the theme file (#683).
- A filename like `EXP-003.v2.md` holds `EXP-003` again (only a paper-scoped `H1.2` stem does not hold `H1`), and a default branch guessed because the remote advertises no HEAD is no longer recorded as `origin/HEAD` (#685).
- Multi-board `create`: when the theme of the board a new item lands on does not define its type, the ID prefix comes from the first board (config order) whose theme does, not the built-in prefix (#688).
- A parent git has never tracked is refused as "not committed" rather than as having "uncommitted edits", and when a `--push` create lands its parent link on origin but not in this checkout, the line says "Linked X on origin/<branch>" instead of "Updated X with inverse reference" (#693).
- A frontmatter list written fresh (it had a multi-line entry) keeps the key's own item indent and the comment lines before its first item (#695).
- A theme's empty item-type definition (`expedition: {}`) no longer gives a truncated prefix (`EXPE`) in single-board mode: it counts as not defined, as in multi-board mode, and the built-in prefix applies. A null definition, which the theme loader already drops, is also handled defensively (#700).
- A theme `status_mappings` key that is the canonical name (or an alias) of a different status (`done: review`) is dropped at load with one warning: it could never mean its target, and `move review` wrote `done`, which read back as done. `move review` now writes `review` (#701).
- A parent added to the index but never committed is refused as "not committed", like an untracked one, rather than as having "uncommitted edits" (#705).
- Theme load drops trap `status_mappings` keys (#701) before de-duplicating folded keys (#615), so the dedupe keeps a usable key and never reports keeping one that is then dropped (`done: review` + `Done: done` keeps `Done`) (#712).
- Parent rename detection reads `git diff -z`, so a `git mv` to a non-ASCII or quoted name still counts as an edit of a committed parent, and HDD creates say when the named parent is on no board instead of silently writing no inverse reference (#718).
- MCP tools enforce their array and boolean arguments: `kanban_update_item` (`tags`, `depends_on`, `related`, `allow_unknown`), `kanban_create_item` (`tags`) and `kanban_next_id` (`sync_remote`) refuse a string where an array is expected instead of writing it as a list of characters, and refuse `"false"` for a boolean. The service refuses a bare string ID list too (#719).
- A description or body that leaves a code fence open is refused on create and update (CLI and MCP), naming the line: the open fence would swallow the status-history block, and the next body edit would delete the history (#720).
- `update` compares dependency and related IDs ignoring case: `--add-dep EXP-3` on an item
  whose file says `exp-3` is no change (the line and its comment stay), and a real edit's
  commit message no longer names it as `+EXP-3 -exp-3`. `--tag ""` or a blank tag is
  refused, and so is an `update` of an ID that is on more than one board, naming both
  files; nothing is written (#721).
- An HDD create whose parent gets no inverse reference says exactly why, once: the parent is on no board, has no turtle block, or already links to the child (by name). The service no longer also logs a warning for the first two (#724).
- A fenced block's dropped `kb:comment` (a forgery, #635) no longer leaves its author/text behind as an orphan blank node in the query graph: nested, self-referencing and cyclic forged nodes are skipped too, while a node that a kept triple points at stays with everything under it. An end-to-end test pins `comment` → SPARQL (#726).
- A body edit on an item whose body already has an unclosed code fence (hand-edited, or from before #720) is refused instead of deleting the status history or comments the fence swallowed, and `validate` reports such items as SWALLOWING FENCE (JSON type `swallowing_fence`), including a closed fence that quotes the history opener (#727, #769).
- MCP: an expected refusal (bad input) is logged as one warning line instead of a full traceback; `item_id` and `prefix` must be strings (a clear error instead of an `AttributeError` message); an explicit `null` for `allow_unknown` or `sync_remote` means the argument was omitted, and `"arguments": null` means none (a non-object is refused) (#728).
- Duplicate IDs are detected case-insensitively (`exp-5` and `EXP-5` in two files), and the MCP server rescans before `kanban_update_item`, so a long-lived server sees duplicates as the files are now (#732).
- MCP: a missing or null required argument is refused as `<arg> is required` (from each tool's schema), not a bare KeyError with a traceback (#735).
- A parent whose turtle block can't be parsed (or has no URI subject) is reported as such when a child is created under it, not as 'already links', and without a duplicate parse warning (#737).
- MCP `kanban_update_item` refuses a bad argument before scanning, and a `depends_on` edit scans the board once, not twice (#740).
- An item whose file spells its ID in lowercase (`id: exp-9`) is reachable as `EXP-9` by update, move, show, comment and MCP; an explicit-ID create (`measure`/`hypothesis create --id`) refuses an ID that exists in another case (#741).
- `move`, `comment` and `rank` (CLI, service and MCP) refuse an item whose ID is on more than one board, naming every file, as `update` does; MCP move and comment see a duplicate written after the server started (#742).
- `validate`'s swallowing-fence report (text and JSON) and the body-edit refusal name what the fence swallows: the status history, the comments, or both (#743).
- A forged `kb:comment` node in a turtle block no longer merges into the query graph when only a free-floating blank node points at it: a blank node stays only if a triple about an IRI reaches it (#744).
- MCP: a `tools/call` with `"params": null` means no params, and a non-object `params` is answered with -32602 "params must be an object"; neither is a -32603 with a traceback (#745).
- `create --push` puts the new item in the case-folded index like a plain create, and the move/update/comment/rank commit messages name the item as its file spells it (#751).
- Next-ID allocation counts an ID whatever its case (`exp-12` in frontmatter, a filename, origin or `_ID_ALLOCATIONS.json`), so it never re-issues it as `EXP-12` (#752).
- A child create whose parent ID is on more than one board is refused before anything is written, and `epic add` / `voyage add` / `create --items` refuse to link a duplicated item, naming every file (#754).
- MCP `kanban_add_comment` refuses unwritable comment or author text before rescanning the board (#755).
- `validate`'s swallowing-fence report says the fence "runs over" what follows it, and it and the body-edit refusal suggest rewording a quoted heading inside it (#758).
- `create --push --id` refuses an ID that origin holds in another case (`m-042` vs `M-042`), naming the rival, and a `--push` parent spelled in another case is found on origin (#764).
- Next-ID allocation judges every source by one id-space rule: `IDEA-R-003` no longer counts toward a bare `IDEA` (#765).
- `parent_link_state` checks the child type's relation before the parent, the same order as the link itself, so both report 'no-relation' for an unknown child type (#766).
- MCP: a required argument given as an empty or whitespace-only string is refused as `<arg> is required`, like a missing one (#768).
- `_stem_holds` compares the stem's own head slice, so a character whose upper case is longer (`ß`) can't misalign the ID number it reads (#775).
- Next-ID allocation counts an ID toward a prefix only when the text before its number is exactly the prefix and its separator, so a dashless `EXP012` no longer counts toward `EXP` from frontmatter, origin or allocation records, matching filenames (#776).
- `create --push` with a parent checks the child type's relation first, reads origin's ids once, and links only into the file whose frontmatter `id:` is the parent, never an id-less outline named after it, matching a local create (#777, #796, #819).
- Every refusal of user input, item state or config is an `InputRefused` (`from_string`, config loading, the service, git commit refusals). MCP reports only those as refusals; any other error is logged with its traceback as a bug (#786).
- A templated create's unclosed-fence refusal says the line is "of the rendered item file", since it counts the template's lines too (#787).
- `create --push` links a child into the parent item itself, not a lookalike file named after it (an outline, a draft folder, another item's file): a file holds an ID by its frontmatter `id:`, and by its name only when it has no `id:` (#788, #792). A push create is also refused when origin already has a file at the new item's path, or one whose name differs from it only in case, instead of replacing it (#788).
- The board's duplicate IDs compare ids as origin's holders do (`_id_key`): `EXP-3` and `EXP-003` are one ID (#641), so `validate` reports the pair, writers and dependencies refuse either spelling as ambiguous, and a local child create under such a parent is refused as `--push` already was (#795).
- MCP: an argument the tool's schema types as a string but given as another JSON type is refused as `<arg> must be a string`, and `null` for any optional argument means omitted, instead of crashing (#801).
- `next-id` (CLI and MCP) refuses a prefix no ID could have (`a b`, `../x`, a NUL): a prefix is a letter, then letters or digits (any script) in dash-separated segments, with an optional trailing `.` (#802).
- A missing run `config.yaml` or theme template raises `MissingFile`, a refusal that is still a `FileNotFoundError`, so CLI and MCP report it as one line rather than a bug (#803).
- `sync_and_push`: a Change equal to the base, or one that commits nothing locally, is `noop`; local mode is decided per Change (only a file outside the repository goes local), a local hook refusal says the edit is kept in the working tree, a refusal ends "(nothing was changed)", and timeouts report the real attempt count (#805).
- Every git command whose output yurtle-kanban reads runs with `LC_ALL=C`, so a race or a push refusal is recognised whatever the user's locale (#806).
- Item files with non-ASCII names (`ß-12-x.md`, `EXP-020-naïve.md`) are seen on origin by the push-time holder checks, next-ID counting and `claim`: git no longer quotes their names out of the listing (#808).
- A parent link is appended to the parent's turtle block as one line, `<subject> <predicate> <child> .`, using the prefixes the block already declares (else full IRIs); the block is no longer re-serialized, so relative `<#ID>` IRIs, order, comments and line endings stay as written (#812).
- `claim` and `update --push` refuse when origin's board files can't all be read (a failed `git archive`, or an `export-ignore` in `.gitattributes` dropping one) instead of counting WIP short, and a claim fires `on_assign` only when the holder changes (#814).
- A theme's `id_prefix` that no ID could have (`../x y`, a NUL) is dropped with a warning and the type's default prefix is used, so `create` never writes outside the type folder or crashes (#816).
- The ID-prefix grammar NFC-normalizes a prefix (a decomposed `ÉXP` is `ÉXP`), allows combining marks within a segment, and allows a trailing `.` only after a final ASCII digit with no dash (`H130.` yes, `H-1.` and `H٣.` no); its messages name the trailing `.`. Every ID comparison (allocation, counting, lookup, duplicates, holders at origin) now uses one fold, `NFC(upper(NFC))`, so any spelling of an ID, including the six Greek letters whose upper case is not NFC (`ΐ ΰ ῒ ῗ ῢ ῧ`), is one ID (#817).
- An `_ID_ALLOCATIONS.json` that exists but is not a valid JSON list is now refused by `create --push` and `next-id` on every path (remote, no remote, `--no-sync`), naming the file and saying to fix or remove it, instead of being replaced by a fresh list that dropped every earlier allocation (#818).
- `claim --take-over` and `move --take-over` follow one rule: `kb:takenOverFrom` is recorded only when the take-over overrode something (a holder, or an in-progress item with no holder, recorded as `""`); both `--help` texts say how an in-progress item with no holder is treated (#823).
- `claim` and `update --push` print a refused, lost, unreachable, busy or push-refused outcome as one `Error:` line, like every other refusal (#825).
- The pre-commit hook run by `claim`, `update --push` and `create --push` sees the user's locale again; only git commands whose output yurtle-kanban reads run under `LC_ALL=C` (#826).
- An item file whose `id:` or `---` line holds a character YAML reads as a line break (`\\r`, NEL, U+2028/U+2029) no longer loses its id, or another file's ids, from origin's view: git grep's output is read raw and split on `\\n` only (#830).
- Origin's board files are read as blobs (`ls-tree` + `cat-file --batch`) instead of through `git archive`, so `.gitattributes` export-subst/export-ignore can't change what `claim` and `update --push` count (#832).
- `update --push` checks new dependencies against origin's tree plus every external board's working-tree items, so an in-repo item can depend on an external one, and a duplicate across the two is refused (#833).
- `create --push` refuses when origin spells a folder on the new item's path in another case (`Research/` vs `research/`), which a case-insensitive filesystem would merge (#834).
- The inverse link attaches to the subject whose local name is the parent id (case-folded), else the first-written subject, instead of rdflib's hash-ordered first subject; the declared-prefix check no longer matches `prefix` inside a longer word (#838).
- `init`'s template prefix for a theme type with no `id_prefix` is grammar-valid: the key's letters and digits from its first letter, upper-cased, at most four, else `ITEM` (#840).
- A user's `diff.relative=true` no longer hides a staged rename from the parent-commit check, or a staged move from `_commit_paths`, when the board is in a subdirectory: both `git diff --cached` calls pass `--no-relative` (#842).
- `next-id` now skips non-object allocation records instead of reusing an existing ID (#846).
- `next-id --json` prints its refusal as JSON on stdout (exit 1) on every path, not only the remote one; a corrupt allocations file's refusal ends "nothing was changed", which is true of `next-id` and `create` alike (#847).
- A local `git commit` (no remote, `move`, `next-id`, `hdd`) and the `hdd --push` push run the user's pre-commit and pre-push hooks in the user's locale, not `LC_ALL=C`: their output is only shown. The compare-and-swap pushes whose `[rejected]` text is matched stay under C (#848).
- `move --take-over` of an in-progress item with no holder commits "(taken over from no holder)" instead of "(taken over from )", matching `claim` (#850).
- `claim`, `update --push` and the #777 parent link look up the item at origin with the board's ignore patterns applied, as the scan does: an ignored (e.g. archived) item is not found, and an ignored copy is no duplicate. The id space (`next-id`, explicit-id collisions) still counts ignored files (#856).
- Every `-z` path listing (`ls-tree`, `diff --cached -z`) is read raw and decoded as UTF-8, as #830's grep is, so a lone `\r` in a filename is kept rather than turned into `\n` and every listing spells a name the same way (#859).
- The `kanban-auto-close` workflow closes an item an agent still holds: it moves with `--take-over --agent "github-actions[bot]"`, recording the take-over in the item's history, and a failed move's warning now carries the refusal's reason (#860).
- A structurally wrong kanban config (`boards: 5`, `boards: [7]`, `kanban: 5`, a non-string board `name`/`path`, a `wip_limits` or `gates` that is not a mapping, and the like) is now refused with a one-line error naming the field and the shape it needs, locally and when read from origin, never a traceback (#864).
- **A claim judged by origin's theme could use another repo's override:** run with the
  working directory inside a different repo, that repo's `.kanban/themes/` could replace
  origin's built-in theme (its WIP limits, statuses). Origin's own overrides, then the
  built-ins, are the only themes that judge now (#866).
- `epic` / `voyage` `show`, `add` and `create --items` find the epic and items by any spelling of their ID, and count a `related:` entry as a link under any case or Unicode form; the CLI and MCP item commands fold IDs with `fold_id`, so their messages show the NFC ID (#868).
- The case-twin guards on `create --push` (#788 file, #834 folder) compare names as APFS does, NFC plus casefold, so a `Café` spelled NFC and one spelled NFD are twins and refused. `Straße` vs `STRASSE` stays refused, since APFS folds them too (#869).
- The case-twin folder guard refuses a write into either spelling when origin already has a folder in two spellings, and says so. It no longer says "would add a folder". The allocation-only push (`next-id` with a remote) is guarded too (#870).
- `init`'s fallback template prefix NFC-normalises the type key first, so a key typed with a combining accent (`e` + U+0301 + `xp`) gives `ÉXP`, not `EXP` (#875).
- With no subject named after the parent, the inverse link goes onto the subject whose term is written first in the block. Terms are read as whole IRI refs and prefixed names outside strings, comments and directives, not by substring position, so `<#PAPER-10>` is no `<#PAPER-1>` and a comment can't pick a subject (#876).
- `update --push`'s dependency duplicate check compares resolved paths, so one outside-board file reached through two spellings (a symlink, `/tmp` vs `/private/tmp`) is not a duplicate. A file's parse warning is recorded once, not again on every push attempt (#879).
- **Reading origin's board for a claim's WIP count** lists the tree once, not twice, and
  refuses `git cat-file --batch` output whose header names another object or that is
  cut short of its stated size, instead of misreading it (#880).
- **`hdd … --push` no longer times out the user's pre-push hook after 30 s**, and a git
  timeout on that push path refuses with a one-line error, the commit kept, instead of a
  traceback (#888).
- The `kanban-auto-close` workflow's failed-move warning escapes the move's output as workflow-command data (`%` → `%25`, CR → `%0D`, LF → `%0A`), so the whole reason survives in one annotation and the line has no trailing space (#894).
- Config loading: a non-string `version` is refused, except the numbers 1 and 2, read as "1.0" and "2.0"; a bare `version:` keeps the default. An unquoted `version: 2.0` is YAML's float and silently loaded as v1, dropping `boards:`. Every board refusal reads `` `<field>` on board <name> must be …, got … ``, and the loader's backstop keeps the original traceback in the debug log (#900).
- **`epic`/`voyage` leftovers from #868:** `add`/`create --items` warnings name an item
  in NFC, `show` no longer lists the keys of an epic's malformed (mapping) `related:` as
  linked items, and an item file without `id:` gets its fallback ID in NFC (#904).
- `metrics <unknown>` and `experiment status <unknown>` (no item and no runs) are refusals, exit 1, in JSON under `--json`. `metrics --json` of a known item with no history prints its usual shape with empty metrics instead of plain text (#905).
- **`init`'s fallback template prefix keeps combining marks** after a kept letter
  (`हिन्दी` no longer loses its vowel signs), and counts a letter plus its marks as one
  of its four characters, folded as IDs are, so the cut never splits one (#907).
- **`query --json --semantic` without a working search extra** prints one JSON refusal
  (`{"success": false, "error": <install hint>}`) on stdout and exits 1, as every
  `--json` refusal does since #877, instead of the hint on stderr and an empty stdout
  (#908).
- The parent-link fallback reads the block in document order: a triple on a `@prefix`/`@base` line counts, `base:`/`prefix:` are ordinary prefixes, local names follow Turtle's PN_LOCAL (`:`, `%xx`, `\`-escapes), and a redeclared prefix or base applies from where it is written (#910).
- `claim`, `update --push` and `create --push` push with no timeout, since the push runs the user's pre-push hook (#584). A slow hook, such as a test gate, no longer turns a good push into a timeout (#925).
- `create --push` / `next-id` with a remote no longer crash after the push lands when origin holds a file whose name isn't UTF-8. The post-push fast-forward reads git's output raw, never raises, and warns that the checkout wasn't updated (#928).
- **`--json` usage errors print JSON:** a bad option, value, missing argument or unknown
  subcommand under `--json` prints `{"success": false, "error": <click's message>}` on
  stdout, still exiting 2; and a `--json` that is another option's value
  (`--assignee --json`) no longer turns an undecodable-argument refusal into JSON (#929).
- Config refusals and warnings name where the value sits: a v1 `ignore` or `scan_paths` refusal says `in kanban.paths`, `in paths`, `in kanban` or `at the top level`, and a dropped board WIP limit warns `` config.yaml: `wip_limits.X` on board 'a' `` (#946).
- **`next-id` / `create --push` could hand out an ID already on origin** when that item
  sat in a type's folder outside the configured scan paths: origin's ID listing now reads
  the same roots as the scan, work paths plus placement folders (#954).
- The parent-link fallback reads prefix names by Turtle's PN_PREFIX: a prefix may start with a letter in any script (`é:`, `αβ:`) and never ends in `.`, so `true.:Beta` reads as `true`, the statement's `.`, then `:Beta` (#956).
- **`claim` / `update --push` crashed reading origin's `.kanban/workflows/`**
  (`AttributeError: 'list' object has no attribute 'values'`) since #865 and #880 met on
  main (#957).
- `_board_loads` (the fetched-tree item lookup's ignore filter) skips placement dirs outside the repo root, as the scan does, so it agrees with the scan when `paths.root` is an absolute path elsewhere in the git top (#963).
- **A refused config value is no longer echoed whole**: a huge number in `version` (or any
  field) is cut in the error, and one past Python's 4300-digit limit no longer crashes the
  refusal (#964).
- **`--json` usage-error edges:** a `--json` that is the value of a short-option cluster
  (`query -vn --json`) is not a JSON request; a usage error in the root's own options
  (`yurtle-kanban --bogus list --json`) prints JSON too. A command without a `--json`
  option still answers `--json` with a JSON usage error (exit 2) (#971).
- `claim` offers "to take it over use claim --take-over" only where a take-over would pass (the item is ready or in progress, its dependencies met); after a WIP-limit refusal `claim --next` skips the other candidates of that item type (other types are still tried) and, if nothing is won, exits with that refusal's reason and code, and its exit-7 line names the last refusal's reason; the README command table lists `next --json`, `list --pickable`/`--explain` and `claim --next` (#990).
- After a `--push` lands, the local fast-forward (`git merge --ff-only`) runs the user's post-merge hook with no timeout, as every hook-running git command does (#584). On a diverged `main`, `create --push` says "not in this checkout yet" once, with no warning of git's `hint:` advice squashed onto one line beside it; `next-id` prints a note of its own that the checkout was not updated; and where git's failure is still warned about, only its `error:`/`fatal:` lines are shown. The unreachable "Timed out pushing…" messages are gone (the pushes have had no timeout since #925), and `update --help` no longer says workflows apply to `update --push`, which never changes status (#995).
- In a config with no `kanban:` key, the "must be a mapping" refusal says `paths`, and the "is ignored because" warning says `` `ignore` `` / `` `paths.ignore` ``, not `kanban.…`; the #946 "at the top level" wording is pinned (#1000).
- The parent-link fallback's Turtle reader ends a `true`/`false` keyword at a following `.`, admits combining marks, `·` and U+203F–2040 inside prefix and local names (PN_CHARS), and skips blank-node labels whole, so `_:a.b` is never read as a `b:` name (#1001).
- A config refusal naming an int too big to print keeps its sign (`int -<N-bit number>`), and `_shape`'s docstring states its real cut. The rglob lint catches `**` anywhere in a glob pattern, positional or `pattern=`, and the tests share one `_has_install_hint` (#1011).
- `metrics`: an error from the flow-metrics service is a refusal (exit 1, JSON under `--json`) instead of a plain yellow note; a long refusal stays on one line, as every refusal does (#1012).
- One `_scanned_placement_dirs()` (placement dirs inside the repo root) is shared by the scan, `_board_loads` and the fetched-tree item reader. Origin's items are read exactly where a scan reads them, while the id space still counts every placement dir, so a committed id is never reissued (#1014).
- The parent-link fallback reads a prefixed name written right after a blank-node label (`_:x:k` is `_:x` then `:k`, as rdflib reads it) (#1020).
- The `--json` argv walker follows click for an optional-value option (it takes the next argument only when that doesn't look like an option) and stops at an unknown letter in a short cluster, as click fails there, so a usage error then still answers in JSON (#1021).
- The `--json` argv walker follows click on `=` forms: an unknown `--name=value`, or a flag or `--help` given a value, is where click fails (a later `--json` was asked for); a single-dash `=` token is a short cluster (`-vn=3` is `-v`, then `-n` taking `=3`); `--json=…` itself counts as a request. Help option names come from the command's own `context_settings` (#1036).
- **`validate` checks a bounce's `bounced_at` and `bounced_by`**: a time without a UTC offset, or
  an empty or missing actor on a bounced item, is reported like a malformed `bounce_sha` (#1041).
- On a diverged `main`, `claim` and `update --push` say "Your checkout does not show this yet" once, with no second, logged warning beside it, as `create --push` does since #995. Where the post-push fast-forward failure is still warned about, git's indented lines that continue a kept `error:`/`fatal:` line (such as the names of the files a merge would overwrite) are kept, and a test now pins that git's `hint:` advice is left out (#1043).
- When `claim`, `update --push` or `create --push` land but can't fast-forward this checkout, the note now says why: "… (fast-forward refused: <git's error/fatal lines>)" (#1048).
- `move --resolution` follow-ups (#1053): a finished → finished move off canonical done drops a `completed` resolution (hdd `complete` → `abandoned` no longer leaves `abandoned` + `completed`, and `list --resolution completed` no longer lists it); a `--superseded-by` target already on a hand-made cycle is refused as "leads into a supersession cycle" rather than "would make a cycle"; `pickable`/`claim` name a redirect's missing final target, not the item that redirects to it. `validate`'s `bad_supersession` branches are now tested.
- The "fast-forward refused" note drops git's own `error:`/`fatal:` labels ("fast-forward refused: Not possible to fast-forward, aborting."); the logged warning keeps git's raw lines (#1057).
- `unmet_dependencies` (#1061) names a redirect's missing final target (state `unknown`) rather than the existing item that redirects to it, agreeing with `pickable`'s reason; the `kb:clearedResolution "completed"` record of a finished → finished move is now pinned by a test.
- The "fast-forward refused" note drops only the `error:`/`fatal:` label git puts at the start of each of its lines, not the same text mid-line: an untracked file named `error: odd.md` blocking the fast-forward is named as `error: odd.md`, not `odd.md`, and a reason that is only a label shows that label rather than an empty `(fast-forward refused: )`. The logged warning keeps git's raw text (#1062).
- The halt state (#582) is read once per scan: `pickable()` looped over one scan's items, and `list --pickable`, no longer run the control file's git reads per call; a new scan, a fetch or `control halt|resume` reads it afresh, and `control status`, `next`, `list --pickable`, `move` and MCP `kanban_suggest_next` always do. `control status --json` on a bad control file now carries an `error` key explaining the halt (#1067).
- `blocked --board X` and `list --board X` refuse a board name no config knows with `Unknown board: X` (exit 1; a JSON refusal under `--json`), as `states` does, and MCP `kanban_get_blocked` returns that error for its `board`; a single-board repo refuses any name but `default`. `blocked`'s tree marks a dependency on a supersession cycle `— cycle`, and one `blocked` run builds the dependency index once instead of once per item (#1068).
- **`KanbanService.get_blocked_items()` no longer lists hdd `abandoned` items** as blocked;
  a finished item is never blocked (#1075).
- `blocked`'s tree marks a supersession-cycle node `— cycle` even when an unrelated `depends_on` cycle's `↻` line appears below it; `list`, `states` and `export` refuse an unknown `--board` with the same `Error: Unknown board: X` line `blocked` prints, and `export --board` no longer silently falls back to the default board (#1077).
- `blocked`: a dependency on a supersession cycle is marked `— cycle` even when a `depends_on` `↻ cycle` line below it runs through it; the `↻` suppression applies only to dependency cycles (#1081).
- A rewrite of `.kanban/_ID_ALLOCATIONS.json` that drops non-object records warns how many it dropped, naming the file; and the id readers (the checkout's file and the one committed on the fetched origin/main) refuse a file that isn't a JSON list, as the writers do, instead of flooring at 0 and handing out an id that may be taken — `next-id --no-commit`, plain `create`, `idea create` and `epic create` included (#1095).
- The "fast-forward refused" note strips git's `error:`/`fatal:` label once: git's `fatal: error: x` reads `(fast-forward refused: error: x)`, and an untracked `error: odd.md` blocking the fast-forward is named in full, in both the `--push` create note and the pushed-change message (#1124).
- Item lookups and the dependency "on no board" check compare ids as `_id_key` does, an exact match first: on a board holding only `EXP-003`, `show EXP-3`, `move EXP-3` and `update EXP-3` find it, and `update X --add-dep EXP-3` is accepted and written as the board spells it (`EXP-003`), so the dependency graph and cycle checks see the edge. `EXP3` is still another id (#661), and two spellings of one id on the board are still refused as a duplicate (#795) (#1125).
- `update X --rm-dep EXP-3` removes a stored `EXP-003`, as `--add-dep` reads it (#1125): each target drops the dependency spelled exactly so, else the one with the same `_id_key`. In one call, `--add-dep EXP-3 --rm-dep EXP-003` adds nothing, and `update EXP-003 --add-dep EXP-3` is refused as "EXP-003 can't depend on itself". `--rm-dep EXP3` still removes nothing (#661) (#1136).
- The dependency graph resolves each stored `depends_on` target to the item on the board by `_id_key`, an exact spelling winning (#641): a file that says `EXP-05` points at `EXP-5`, so `validate`, `blocked`, `pickable`/`next`, and the `update --add-dep` cycle check (local and on push) see a cycle closed through any spelling; `EXP5` (#661) and ids on no board are unchanged (#1139). A dependency on an item's own id in another spelling (`EXP-5` depending on `EXP-05`) is now reported as a one-node cycle rather than a dangling dependency.
- `update` no longer writes a second spelling of a dependency the item already stores: with `depends_on: [EXP-05]`, `--add-dep EXP-5` (or `exp-005`) and `--depends-on EXP-05,EXP-5` report "no changes" and leave the file untouched, locally and with `--push`; a `--push` add-dep closing a cycle through a stored `EXP-05` is refused as a cycle (#1146).
- The "dropping N non-object record(s)" warning for `_ID_ALLOCATIONS.json` now prints once per command, even when a `create --push` or `next-id` push loses a race and retries; the hdd create commands' refusal of a corrupt allocation file is pinned by tests (#1158).
- **`next-id` said "(committed and pushed to remote)" under `--no-commit` or with no
  remote.** It now says where the allocation went (pushed, committed locally, or not
  recorded), and `--json` gains a `recorded` key with the same answer (#1159).
- A non-UTF-8 or unreadable `.kanban/_ID_ALLOCATIONS.json` (in the checkout or committed on origin) makes `next-id`, `create` and `create --push` exit 1 with a one-line `Error:` naming the file, instead of a `UnicodeDecodeError` / `PermissionError` traceback; nothing is written (#1161).
- `allocate_next_id` (and MCP `kanban_next_id`) returns the refusal dict, not an exception, for a corrupt allocations file on the fetched origin when allocating locally (`sync_remote=False` / `commit_allocation=False`), as the compare-and-swap path does; a corrupt local file still raises (#1162).
- The "dropping N non-object records" allocation warning prints only once a `create --push` / `next-id` push has landed, never when every attempt lost the race or the remote refused it (#1165).
- The checkout's `_ID_ALLOCATIONS.json` is read without an existence check first, so a file deleted just before the read counts as missing (a fresh list), not "could not be read" (#1167).
- `allocate_next_id`'s local path reads the checkout's allocations file once, so a local refusal can never come back as the origin refusal dict (#1169).
- A file named `.kanban` where the allocations directory must be is refused naming `.kanban` ("is a file, where a directory must be: move or remove it"), not `_ID_ALLOCATIONS.json` (#1174).
- With `.kanban` a file, `init` and `control halt` refuse with one `Error:` line naming `.kanban` as a file, not a `FileExistsError` traceback or a raw git error, and write nothing; `board-add` saves the new board to the config file it loaded (`.yurtle-kanban/config.yaml` too), never creating `.kanban/`, and with no config at all refuses the same way; its can't-upgrade refusal (#122) names that config file (#1179).

### Security

- **An `epic show` / `epic add` "not found" error printed the ID raw on stderr**, so an
  ID holding ESC or a newline could clear the screen or forge a line; the `hdd` create
  commands' "already exists" and "Failed:" errors did the same with an item title or a
  git message. Every `ClickException` value now has its control characters escaped
  (shown as `\x1b` / `\n`); brackets stay verbatim, since click prints plain text, not
  Rich markup. A static check keeps it so (#1085).
- **A refused input passed through to click printed its text raw on stderr**, so a
  duplicate item whose second copy sat under a directory named with ESC or a newline
  could clear the screen or forge a line through `epic add` / `voyage add` and the `hdd`
  create commands' `--idea` / parent link. Every `InputRefused` pass-through (the
  command group's handler, `epic add`, and the `hdd` template render and parent-link
  refusals) now escapes control characters once (shown as
  `\x1b` / `\n`); text a producer already escaped comes through unchanged, and brackets
  stay verbatim (#1091).
- **`show`, `list`, `board`, `roadmap` and `history` printed an item's text with its
  control characters raw**: they escaped Rich markup only, so a title, assignee, tag,
  dependency or `superseded_by` holding ESC (YAML `\e`) could clear the screen, and one
  holding a newline could forge an output line. Every field these commands render now
  has its control characters escaped (shown as `\x1b` / `\n`); the description and
  comments `show` prints are escaped line by line, so they stay multi-line. `--json`
  output is unchanged (JSON encodes it) (#1093).
- **`roadmap --export md` printed item fields raw**, so an ESC in a title, priority or
  assignee could clear the screen and a newline forged a list line. The id, title,
  priority and assignee now show control characters as `\x1b` / `\n`; `--json` stays
  raw (#1101).
- **`epic show` and `voyage show` printed an item's text with its control characters
  raw**: the header line, the linked-items table and the Research Interlinks section
  (paper, hypothesis, experiment, measure and literature labels, hypothesis target,
  measure unit and category) escaped Rich markup only, so a title, assignee or Turtle
  label holding ESC (YAML `\e`, Turtle `\u001B`) could clear the screen, and one holding
  a newline could forge an output line. Every field these views render now has its
  control characters escaped (shown as `\x1b` / `\n`); printable text, brackets and
  backslashes render as written (#1105).
- **`epic`/`voyage` `create`, `add` and `show`'s "Link items with" hint printed ids,
  titles and file paths with only markup escaped**, so an ESC or newline in an item id,
  title or board path could clear the screen or forge a line. They now show control
  characters as `\x1b` / `\n` (#1110).

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
