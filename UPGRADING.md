# Upgrading yurtle-kanban

How to move a repo, a script or an integration from one major version to the next. Each
section lists every change a user can notice, with what to use instead. Removals follow the
[deprecation policy](CONTRIBUTING.md#deprecation-policy): deprecated in a minor release
first, removed in the next major, and documented here.

## 2.x → 3.x

### Step 1: scan your repo first

Run the scanner at the root of each repo that calls yurtle-kanban (3.3.0 and later), first,
before changing anything by hand:

```bash
yurtle-kanban upgrade-check            # PATH defaults to the git top level, else the current directory
yurtle-kanban upgrade-check --json     # machine-readable, for CI
```

It reports each place that uses a form 3.x changed: file, line, the old form, a suggestion and
a confidence (`high`, or `low` for mentions in docs, comments and quoted text). Exit codes:
`0` nothing found, `1` findings, `2` a bad PATH or usage, `3` an unexpected error. With
`--json` it prints `{"findings": [...], "heuristic": true, "not_checked": [...]}`, plus
`notes` and `skipped` when it has them.

It is a heuristic. A clean run means no known 2.x pattern was found, not that nothing changed:
it does not check Python API use (see [Removed APIs](#removed-apis)), scripts that read
refusals from stdout (they moved to stderr), code that parses the `--json` refusal shape, MCP
clients, readers that parse the plain-text output by hand, or `resolution:` values in item
files. Read the rest of this section for those.

### Renamed CLI flags (#580)

3.0.0 renamed these flags with no aliases. **3.3.0 restores the old forms as deprecated
aliases**: each still works, prints one warning on stderr naming its replacement, and is hidden
from `--help`. They are removed in **4.0**, so move to the new form now. Giving an old form
together with its new form is a usage error (exit 2).

| Command | 2.x form | 3.x form |
|---|---|---|
| `move` | `move ID STATUS -a A` | `move ID STATUS --assign A` |
| `create` | `create … --assignee A` or `-a A` | `create … --assign A` |
| `create` | `create … --description T` or `-d T` | `create … --body T` (or `--body-file PATH`, `-` for stdin) |
| `comment` | `comment ID TEXT` | `comment ID --body TEXT` (or `--body-file PATH`, `-` for stdin) |
| `comment` | `comment ID … --author A` or `-a A` | `comment ID … --agent A` |
| `next` | `next --assignee A` or `-a A` | `next --agent A` |
| `list` | `list -a A` | `list --assignee A` |

The short flag `-a` is gone everywhere in 3.x; only the deprecated aliases above still accept it.

**`next --agent` changed meaning.** In 2.x, `next --assignee A` was a filter on the assignee.
In 3.x, `next --agent A` names the actor who is asking: it offers that actor's own in-progress
items first, then pickable ones. The alias maps the old spelling to the new option, so check a
script that relied on the filter.

`comment ID TEXT` takes exactly one argument (quote multi-word text), and `-` there is literal
text, never stdin; use `--body-file -` to read stdin. MCP `kanban_add_comment` keeps its
`author` field: only its `"agent"` default is gone (see below).

### Who is acting: actor resolution (#580)

The *actor* (a comment's author, `kb:by` on a status change) resolves in one order: `--agent`,
then the `YURTLE_AGENT` environment variable, then git `user.name`, else the command refuses
with an error. The 2.x defaults `"cli"`, `"agent"` and `"unknown"` are gone, and a set but
blank `YURTLE_AGENT` is an error. `kb:by` is now the actor, not the assignee.

The *assignee* is never defaulted: only `--assign` sets it. Identity values (`--agent`,
`YURTLE_AGENT`, `--assign`, `list --assignee`) are refused when empty, whitespace-only or
holding a control character; `list --assignee ""` no longer matches every item.

**To do:** set `YURTLE_AGENT` in agents' and CI environments (or pass `--agent`), and pass
`--assign` where a 2.x script relied on a default assignee.

### Status names: themes write their own (#604, #633)

Nautical boards now write their native status names into item frontmatter (`harbor`,
`underway`, `approaching_port`, `stranded`, …), as hdd and spec boards already did. A
software-theme name such as `in_progress` is no longer what the file holds on those boards, and
another theme's names are no longer read on a board whose theme doesn't define them (an
unknown status reads as backlog).

**To do:** don't compare raw frontmatter, for example `status == 'in_progress'` on a nautical
board. Read the canonical `status` from `yurtle-kanban list --json` or `show --json`, which map
every theme's names. `upgrade-check` flags raw status comparisons in repos whose theme is
nautical, hdd or spec.

### Comments are their own field (#605)

The `## Comments` section is parsed into `comments` (author, time, text) and is no longer part
of `description`. `show --json`, MCP `kanban_get_item` and semantic search read the new field.
A workflow rule on `len(item.description)` now counts only the body (#635).

### Refusals print on stderr (#962, #1086, #1090)

Every text-mode refusal (`Error: Item not found`, `Unknown status`, `Unknown type`, a halted
board, …) now prints on **stderr**, with its exit code unchanged. A script that read refusals
from stdout must read stderr, or better, check the exit code.

### `--json` refusals (#877)

With `--json`, every refusal prints exactly one JSON object on stdout,
`{"success": false, "error": "…"}`, and exits 1. Parse `success` and `error` rather than an
empty stdout or a hint on stderr. `next-id --json` keeps its keys and gains `error`.

### Resolutions (#581)

3.x records how an item finished with `move ID STATUS --resolution R`: `completed`,
`superseded`, `duplicate` or `wont_do`. `superseded` and `duplicate` each need exactly one
`--superseded-by ID`. 2.x had no `--resolution` option, but an item file may carry a hand-written
`resolution:` in its frontmatter. The values `obsolete` and `merged` are not 3.x resolutions: 3.x
logs a warning for them and ignores them. Re-record those items:

| 2.x frontmatter | 3.x |
|---|---|
| `resolution: obsolete` | `--resolution wont_do`, or `--resolution superseded --superseded-by ID` when another item replaced it |
| `resolution: merged` | `--resolution superseded --superseded-by ID`, or `--resolution duplicate --superseded-by ID` |

A `wont_do` item is a dead dependency: anything that depends on it stops being pickable.
`upgrade-check` does not scan item files, so look for these values with
`grep -rn "resolution: \(obsolete\|merged\)" <board dirs>`.

### The allocation file (#818)

A `.kanban/_ID_ALLOCATIONS.json` that is not a valid JSON list (or not valid UTF-8, or not
readable) is refused, naming the file, instead of being replaced with a fresh list. Fix or
remove it; a missing file starts a fresh list.

### MCP (#580, #1066)

- `kanban_get_blocked` returns exactly what `blocked --json` returns, `{"items": [...]}`, each
  item with its `unmet` dependency tree, and takes `board` and `all`. In 2.x it was a status
  filter returning `blocked_items` and `count`.
- `kanban_add_comment` keeps its `author` field, but an omitted `author` resolves like `--agent`
  (`YURTLE_AGENT`, then git `user.name`); the `"agent"` default is gone.

### Removed APIs

These Python names are gone. Nothing in the CLI used them; `upgrade-check` does not look for
them.

| Removed | Use instead |
|---|---|
| `WorkItem.blocks`, the `blocks` key of `to_dict()`, and the `kb:blocks` triple in `query` (#576) | `depends_on`: "X blocks Y" is "Y depends on X" |
| `WorkItem.to_yurtle()` (#576) | the item's frontmatter, the only source of truth |
| The theme keys hdd `id_formats` and `status_aliases` (#611) | nothing: they were never read; IDs come from the item types |
| `KanbanService._commit_and_push_file`, and the `push` argument of `update_parent_turtle_block` (#645) | `create_item_and_push(parent=...)`, which puts the link in the child's commit |
| `WorkflowParser.validate_transition`, `WorkflowConfig.get_allowed_transitions` (#651) | `KanbanService.legal_next` |

`WorkItemIndexer` is deprecated (#434): constructing it warns. Use `KanbanService.scan`.

The full list of 3.0.0 changes is in the [changelog](CHANGELOG.md).
