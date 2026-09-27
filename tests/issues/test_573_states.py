"""Issue #573 — `states`: one legality source, rendered (absorbs bug #589).

The spec is the issue's REVISED body (Expected + Acceptance) and the comments on it:

- ONE source: `legal_next(board_config, theme, item_type, from_status)
  -> list[WorkItemStatus]` — theme `transitions` if the board's theme has them, else
  the per-type workflow's state graph if `.kanban/workflows/<type>` exists, else the
  single default table. A `from_status` the workflow doesn't know -> `[]` (fail
  closed). `_validate_transition` and `get_allowed_transitions` both call it; only
  one default table is left in the source.
- A move refused on legality names the legal targets in native names:
  `Illegal move EXP-1: ready → done. Legal from ready: in_progress, blocked.`
- `yurtle-kanban states [--board B] [--type T] [--json]` renders `legal_next`
  (lifecycle legality only); human output `name → next1, next2`, `native (canonical)`
  where they differ, `(terminal)` for terminal states; `--json` is a list of
  `{board, theme, type, states: [{name, canonical, terminal, next: [{name, canonical,
  gates}]}]}`.
- `show` gains `Can move to: ...` (native); `show --json` gains `next_statuses`
  (canonical) and `next_status_labels` (native), same order; `status` stays canonical.

Ambiguities resolved here (test partner's reading; the driver may challenge):

1. `legal_next` is looked up as a `KanbanService` attribute (it needs the repo's
   workflows); a module-level `service.legal_next` is accepted too. It is called
   with the item type as a string (`item.item_type.value`), as `states --type T` has it.
2. "native name" = what `service.status_label` shows for an item at that status
   (#448's convention: the reverse of the theme's `status_mappings`). hdd `active`,
   spec `implementing`; nautical has no `status_mappings`, so its native == canonical
   in `states` (its harbor/underway names are still ACCEPTED by `move`, #587).
3. `states` lists at least every status the theme names natively (hdd: draft,
   active, complete, abandoned); a status it does not list has no legal next.
   `terminal` == "no legal next". Canonical values are unique per board entry.
4. Without `--type`, the entry has `"type": null` and no workflow is consulted
   (`legal_next(..., item_type=None, ...)`); extra per-type entries are tolerated.
   With `--type T`, one entry per board with `"type": "T"`.
5. Human `states`: every name that differs from its canonical name is printed
   `native (canonical)` (Expected 2 says so). `show`'s `Can move to:` line is
   checked leniently (the issue's example annotates only `abandoned (blocked)`):
   each entry is `native` or `native (canonical)`, and `abandoned (blocked)` must be
   there. The show line may be a table row (`Can move to` then the list), colon
   optional.
6. `gates` in `states --json` = ids of the gates `move` would evaluate for that
   (canonical from, canonical to): the item's board's `gates`, else the top-level
   (v1) `gates` — the same lookup as `_evaluate_gates`.
7. In the refusal, the legal-target list is compared after stripping any
   ` (canonical)` suffix; its order is not pinned. The first sentence is pinned as
   the issue writes it: `Illegal move <ID>: <from native> → <to native>`.
8. The `WorkflowParser._get_default_states` list (used only without rdflib) is not
   counted as a "default table"; the one-table check covers dict tables keyed by the
   six statuses.

The big equivalence (Acceptance 1) uses the service API (`resolve_status_name` then
`move_item(validate_workflow=True, skip_gates=True, skip_wip_check=True)`, as the
CLI does) against `states --json` from the CLI; a sample goes through the CLI `move`.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import io
import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban import service as service_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItem, WorkItemStatus
from yurtle_kanban.service import KanbanService

ROOT = Path(__file__).resolve().parents[2]
THEMES_DIR = ROOT / "themes"
SRC_DIR = ROOT / "src" / "yurtle_kanban"

CANONICAL = [s.value for s in WorkItemStatus]  # backlog ready in_progress review done blocked

# ---------------------------------------------------------------------------
# The lifecycles, pinned independently of the code
# ---------------------------------------------------------------------------

DEFAULT_TABLE: dict[str, list[str]] = {
    "backlog": ["ready", "blocked"],
    "ready": ["in_progress", "backlog", "blocked"],
    "in_progress": ["review", "done", "blocked", "ready"],
    "review": ["done", "in_progress", "blocked"],
    "done": [],
    "blocked": ["ready", "in_progress", "backlog"],
}

HDD_TABLE: dict[str, list[str]] = {  # themes/hdd.yaml `transitions`, canonical
    "backlog": ["in_progress", "blocked"],
    "in_progress": ["done", "blocked", "backlog"],
    "done": [],
    "blocked": ["backlog"],
}

CUSTOM_MAPPINGS: dict[str, str] = {
    "todo": "ready",
    "doing": "in_progress",
    "on-hold": "blocked",
    "shipped": "done",
}
CUSTOM_TRANSITIONS: dict[str, list[str]] = {  # keys/entries: native, else canonical
    "backlog": ["todo"],
    "todo": ["doing", "on-hold", "backlog"],
    "doing": ["review", "on-hold", "shipped"],
    "review": ["shipped", "doing"],
    "on-hold": ["todo", "doing"],
    "shipped": [],
}
CUSTOM_TABLE: dict[str, list[str]] = {
    "backlog": ["ready"],
    "ready": ["in_progress", "blocked", "backlog"],
    "in_progress": ["review", "blocked", "done"],
    "review": ["done", "in_progress"],
    "blocked": ["ready", "in_progress"],
    "done": [],
}

# the workflow for `feature` on the workflow board: no `blocked` state (unknown ->
# fail closed), and deliberately unlike the default table
WORKFLOW_TABLE: dict[str, list[str]] = {
    "backlog": ["ready"],
    "ready": ["in_progress", "backlog"],
    "in_progress": ["review", "ready"],
    "review": ["done", "in_progress"],
    "done": [],
}

# native -> canonical names `move` accepts per theme (besides the canonical six), #587
NATIVE_NAMES: dict[str, dict[str, str]] = {
    "software": {},
    "nautical": {
        "harbor": "backlog",
        "provisioning": "ready",
        "underway": "in_progress",
        "approaching": "review",
        "arrived": "done",
    },
    "spec": {
        "draft": "backlog",
        "proposed": "ready",
        "implementing": "in_progress",
        "accepted": "done",
    },
    "hdd": {
        "draft": "backlog",
        "active": "in_progress",
        "complete": "done",
        "abandoned": "blocked",
    },
    "custom": dict(CUSTOM_MAPPINGS),
}

# native labels (`status_label`, ambiguity 2): canonical -> native, where it differs
LABELS: dict[str, dict[str, str]] = {
    "software": {},
    "nautical": {},
    "spec": {v: k for k, v in NATIVE_NAMES["spec"].items()},
    "hdd": {v: k for k, v in NATIVE_NAMES["hdd"].items()},
    "custom": {v: k for k, v in CUSTOM_MAPPINGS.items()},
}


@dataclasses.dataclass(frozen=True)
class Kind:
    """One single-board repo the equivalence runs on."""

    name: str
    theme: str  # the board's theme (preset)
    item_type: str
    prefix: str
    table: dict[str, list[str]]  # canonical from -> canonical next, theme order
    workflow: bool = False


KINDS: dict[str, Kind] = {
    k.name: k
    for k in (
        Kind("software", "software", "feature", "FEAT", DEFAULT_TABLE),
        Kind("nautical", "nautical", "expedition", "EXP", DEFAULT_TABLE),
        Kind("spec", "spec", "spec", "SPEC", DEFAULT_TABLE),
        Kind("hdd", "hdd", "idea", "IDEA", HDD_TABLE),
        Kind("custom", "custom", "feature", "FEAT", CUSTOM_TABLE),
        Kind("workflow", "software", "feature", "FEAT", WORKFLOW_TABLE, workflow=True),
    )
}

# every theme's names: the candidates N of Acceptance 1
CANDIDATES: list[str] = sorted(
    set(CANONICAL) | {n for names in NATIVE_NAMES.values() for n in names}
)


def _fold(name: str) -> str:
    return name.lower().replace("-", "_").replace(" ", "_")


def _expected_resolve(kind: Kind, name: str) -> str | None:
    """`name` as a status of `kind`'s theme (canonical value), or None."""
    legal = {c: c for c in CANONICAL}
    legal.update({_fold(n): c for n, c in NATIVE_NAMES[kind.theme].items()})
    return legal.get(_fold(name))


def _item_id(kind: Kind, status: str) -> str:
    return f"{kind.prefix}-{CANONICAL.index(status) + 101}"


def _label(kind: Kind, status: str) -> str:
    return LABELS[kind.theme].get(status, status)


# ---------------------------------------------------------------------------
# Repo builders
# ---------------------------------------------------------------------------

WORKFLOW_MD = """---
type: kanban-workflow
id: feature-workflow
version: 1
applies_to: feature
---

# Feature Workflow (#573 test)

```yurtle
@prefix workflow: <https://yurtle.dev/kanban/workflow/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@base <https://yurtle.dev/kanban/workflow/feature/> .

{states}
```
"""


def _workflow_md(table: dict[str, list[str]], applies_to: str = "feature") -> str:
    blocks = []
    for state, nexts in table.items():
        targets = ",".join(f"<state/{n}>" for n in nexts)
        blocks.append(
            f"<state/{state}>\n    a workflow:State ;\n"
            f'    workflow:name "{state}" ;\n'
            f'    workflow:isInitial "{str(state == "backlog").lower()}"^^xsd:boolean ;\n'
            f'    workflow:isTerminal "{str(not nexts).lower()}"^^xsd:boolean ;\n'
            f'    workflow:transitions "{targets}" .\n'
        )
    return WORKFLOW_MD.format(states="\n".join(blocks)).replace(
        "applies_to: feature", f"applies_to: {applies_to}"
    )


def _custom_theme() -> dict[str, Any]:
    theme = yaml.safe_load((THEMES_DIR / "software.yaml").read_text())
    theme["theme"]["name"] = "custom"
    theme["status_mappings"] = dict(CUSTOM_MAPPINGS)
    theme["transitions"] = {k: list(v) for k, v in CUSTOM_TRANSITIONS.items()}
    return theme


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)


def _commit_all(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


def _seed(path: Path, item_id: str, item_type: str, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntype: {item_type}\nstatus: {status}\n"
        f"title: Item {item_id}\n---\n\n# Item {item_id}\n"
    )


def _single_cfg(theme: str, gates: str = "") -> str:
    return f"kanban:\n  theme: {theme}\n  paths:\n    root: work/\n{gates}"


def _build(root: Path, kind: Kind, *, gates: str = "") -> Path:
    """A single-board repo for `kind`, one item per canonical status."""
    kanban = root / ".kanban"
    kanban.mkdir(parents=True)
    (kanban / "config.yaml").write_text(_single_cfg(kind.theme, gates))
    if kind.theme == "custom":
        (kanban / "themes").mkdir()
        (kanban / "themes" / "custom.yaml").write_text(
            yaml.safe_dump(_custom_theme(), sort_keys=False)
        )
    if kind.workflow:
        (kanban / "workflows").mkdir()
        (kanban / "workflows" / "feature.yurtle.md").write_text(_workflow_md(WORKFLOW_TABLE))
    for status in CANONICAL:
        item_id = _item_id(kind, status)
        _seed(root / "work" / f"{item_id}.md", item_id, kind.item_type, status)
    _commit_all(root)
    return root


MULTI_CFG = """version: "2.0"
boards:
  - name: dev
    preset: software
    path: dev/
    scan_paths:
      - dev/
  - name: research
    preset: hdd
    path: research/
    scan_paths:
      - research/
    gates:
      "* -> done":
        - id: ship_gate
          check: context.shipped
          message: "needs shipping"
default_board: dev
"""


def _build_multi(root: Path) -> Path:
    kanban = root / ".kanban"
    kanban.mkdir(parents=True)
    (kanban / "config.yaml").write_text(MULTI_CFG)
    _seed(root / "dev" / "FEAT-101.md", "FEAT-101", "feature", "in_progress")
    _seed(root / "research" / "IDEA-101.md", "IDEA-101", "idea", "in_progress")
    _commit_all(root)
    return root


# ---------------------------------------------------------------------------
# Running things
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _in_repo(root: Path) -> Iterator[io.StringIO]:
    """cwd = root, cli's console routed to a wide colourless buffer, fresh theme cache."""
    old_cwd = Path.cwd()
    old_console = cli.console
    buf = io.StringIO()
    cli.console = Console(file=buf, width=300, color_system=None, force_terminal=False)
    config_mod._theme_cache.clear()
    os.chdir(root)
    try:
        yield buf
    finally:
        os.chdir(old_cwd)
        cli.console = old_console
        config_mod._theme_cache.clear()


def _cli(root: Path, args: list[str]) -> tuple[Result, str]:
    """Run the CLI in `root`: (result, console + stdout + stderr text)."""
    with _in_repo(root) as buf:
        result = CliRunner().invoke(main, args)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`{' '.join(args)}` crashed: {result.exception!r}") from (
            result.exception
        )
    return result, buf.getvalue() + result.output


def _cli_json(root: Path, args: list[str]) -> Any:
    result, text = _cli(root, args)
    assert result.exit_code == 0, f"`{' '.join(args)}` exit {result.exit_code}:\n{text}"
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"`{' '.join(args)}` isn't JSON: {e}\n{result.stdout}") from e


def _service(root: Path) -> KanbanService:
    config_mod._theme_cache.clear()
    service = KanbanService(KanbanConfig.load(root / ".kanban" / "config.yaml"), root)
    # speed only: `move` asks git for the user once per move (kb:by); not legality
    service._get_git_user = lambda: "T"  # type: ignore[method-assign]
    return service


def _item(service: KanbanService, item_id: str) -> WorkItem:
    item = service.get_item(item_id)
    assert item is not None, item_id
    return item


def _legal_next(service: KanbanService, item: WorkItem) -> list[WorkItemStatus]:
    """`legal_next(board_config, theme, item_type, from_status)` for `item`."""
    fn = getattr(service, "legal_next", None) or getattr(service_mod, "legal_next", None)
    assert fn is not None, "no `legal_next` on KanbanService (or in yurtle_kanban.service)"
    board_config, theme = service._item_theme(item)
    return list(fn(board_config, theme, item.item_type.value, item.status))


def _entry(data: Any, *, board: str | None = None, type_: str | None = None) -> dict:
    """The one `states --json` entry for (board, type); board None = the only board."""
    assert isinstance(data, list), f"`states --json` isn't a list: {data!r}"
    matches = [
        e for e in data
        if isinstance(e, dict) and e.get("type") == type_
        and (board is None or e.get("board") == board)
    ]
    assert len(matches) == 1, f"want one entry board={board} type={type_}, got {data!r}"
    return matches[0]


def _next_from(entry: dict, status: str) -> list[str]:
    """Canonical next statuses from `status` in a `states` entry ([] if not listed)."""
    for state in entry["states"]:
        if state["canonical"] == status:
            return [n["canonical"] for n in state["next"]]
    return []


# ---------------------------------------------------------------------------
# Module-scoped repos (built once; tests that move restore the file bytes)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def repos(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return {
        name: _build(tmp_path_factory.mktemp(f"r573-{name}"), kind)
        for name, kind in KINDS.items()
    }


@pytest.fixture(scope="module")
def multi(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_multi(tmp_path_factory.mktemp("r573-multi"))


_STATES_CACHE: dict[tuple[str, str], Any] = {}


def _states_json(root: Path, *args: str) -> Any:
    key = (str(root), " ".join(args))
    if key not in _STATES_CACHE:
        _STATES_CACHE[key] = _cli_json(root, ["states", *args, "--json"])
    return _STATES_CACHE[key]


def _kind_entry(repos: dict[str, Path], kind: Kind) -> dict:
    """The lifecycle `kind`'s items follow: `states --type <item type> --json`."""
    return _entry(_states_json(repos[kind.name], "--type", kind.item_type), type_=kind.item_type)


# ---------------------------------------------------------------------------
# 0. Premises: the pinned tables match the theme files
# ---------------------------------------------------------------------------


def test_premise_tables_match_theme_files() -> None:
    hdd = yaml.safe_load((THEMES_DIR / "hdd.yaml").read_text())
    assert hdd["status_mappings"] == NATIVE_NAMES["hdd"]
    to_c = hdd["status_mappings"]
    assert {
        to_c[k]: [to_c[n] for n in v] for k, v in hdd["transitions"].items()
    } == HDD_TABLE
    spec = yaml.safe_load((THEMES_DIR / "spec.yaml").read_text())
    assert spec["status_mappings"] == NATIVE_NAMES["spec"]
    for theme in ("software", "nautical", "spec"):
        assert "transitions" not in yaml.safe_load((THEMES_DIR / f"{theme}.yaml").read_text())
    assert {
        CUSTOM_MAPPINGS.get(k, k): [CUSTOM_MAPPINGS.get(n, n) for n in v]
        for k, v in CUSTOM_TRANSITIONS.items()
    } == CUSTOM_TABLE


def test_premise_every_kind_seeds_and_labels(repos: dict[str, Path]) -> None:
    """Each repo parses to one item per canonical status, labelled as LABELS says."""
    for kind in KINDS.values():
        service = _service(repos[kind.name])
        for status in CANONICAL:
            item = _item(service, _item_id(kind, status))
            assert item.status.value == status, (kind.name, item)
            assert service.status_label(item) == _label(kind, status), (kind.name, status)


# ---------------------------------------------------------------------------
# 1. One source: legal_next (#589)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind_name", list(KINDS))
def test_legal_next_matches_pinned_lifecycle(repos: dict[str, Path], kind_name: str) -> None:
    kind = KINDS[kind_name]
    service = _service(repos[kind.name])
    got = {
        s: [x.value for x in _legal_next(service, _item(service, _item_id(kind, s)))]
        for s in CANONICAL
    }
    want = {s: kind.table.get(s, []) for s in CANONICAL}
    assert got == want


@pytest.mark.parametrize("kind_name", ["hdd", "workflow", "software"])  # the three paths
def test_validation_and_offer_both_follow_legal_next(
    repos: dict[str, Path], kind_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch `legal_next` to a sentinel: move-validation and the offer both follow it."""
    kind = KINDS[kind_name]
    service = _service(repos[kind.name])
    item = _item(service, _item_id(kind, "backlog"))
    sentinel = [WorkItemStatus.REVIEW]
    if hasattr(KanbanService, "legal_next"):
        monkeypatch.setattr(service, "legal_next", lambda *a, **k: list(sentinel))
    else:
        assert hasattr(service_mod, "legal_next"), "no `legal_next` to patch"
        monkeypatch.setattr(service_mod, "legal_next", lambda *a, **k: list(sentinel))

    assert service.get_allowed_transitions(item) == ["review"]
    verdicts = {t.value: service._validate_transition(item, t)[0] for t in WorkItemStatus}
    assert verdicts == {t: t == "review" for t in CANONICAL}


@pytest.mark.parametrize("kind_name", list(KINDS))
def test_validate_and_offer_agree_every_pair(repos: dict[str, Path], kind_name: str) -> None:
    """Acceptance 2 (and #589 on all three paths): for every (from, to),
    `_validate_transition` accepts iff `get_allowed_transitions` offers."""
    kind = KINDS[kind_name]
    service = _service(repos[kind.name])
    bad = []
    for status in CANONICAL:
        item = _item(service, _item_id(kind, status))
        offered = service.get_allowed_transitions(item)
        for target in WorkItemStatus:
            ok, _ = service._validate_transition(item, target)
            if ok != (target.value in offered):
                bad.append(f"{status} -> {target.value}: valid={ok}, offered={offered}")
    assert not bad, "\n".join(bad)


def _is_status_table(node: ast.Dict) -> bool:
    """A dict literal keyed by exactly the six statuses whose values are lists/tuples."""
    keys = set()
    for key in node.keys:
        if isinstance(key, ast.Attribute) and isinstance(key.value, ast.Name) and (
            key.value.id == "WorkItemStatus"
        ):
            keys.add(key.attr.lower())
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value.lower())
        else:
            return False
    return keys == set(CANONICAL) and all(
        isinstance(v, ast.List | ast.Tuple) for v in node.values
    )


def test_only_one_default_table_in_source() -> None:
    found = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict) and _is_status_table(node):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert len(found) == 1, f"default transition tables: {found}"


# ---------------------------------------------------------------------------
# 2. Acceptance 1 — the equivalence: move succeeds iff `states` lists it
# ---------------------------------------------------------------------------

EQUIV = [(k, s) for k in KINDS for s in CANONICAL]


@pytest.mark.parametrize(("kind_name", "status"), EQUIV, ids=[f"{k}-{s}" for k, s in EQUIV])
def test_move_succeeds_iff_states_lists_it(
    repos: dict[str, Path], kind_name: str, status: str
) -> None:
    kind = KINDS[kind_name]
    root = repos[kind.name]
    entry = _kind_entry(repos, kind)
    listed = _next_from(entry, status)
    service = _service(root)
    item = _item(service, _item_id(kind, status))
    assert item.file_path is not None
    original = item.file_path.read_bytes()

    bad = []
    try:
        for name in CANDIDATES:
            resolved = service.resolve_status_name(item, name)
            moved = False
            if resolved is not None:
                try:
                    service.move_item(
                        item.id, resolved, commit=False, validate_workflow=True,
                        skip_gates=True, skip_wip_check=True,
                    )
                    moved = True
                except ValueError:
                    pass
                finally:
                    item.file_path.write_bytes(original)
                    item.status = WorkItemStatus(status)
            want_resolved = _expected_resolve(kind, name)
            expected = want_resolved is not None and want_resolved in listed
            if moved != expected:
                bad.append(
                    f"{name!r} (resolves to {resolved and resolved.value}): "
                    f"move {'succeeded' if moved else 'refused'}, states next={listed}"
                )
    finally:
        item.file_path.write_bytes(original)
    assert not bad, f"{kind.name} from {status}:\n" + "\n".join(bad)


@pytest.mark.parametrize("kind_name", list(KINDS))
def test_states_next_matches_pinned_lifecycle(repos: dict[str, Path], kind_name: str) -> None:
    """The equivalence can't pass with both sides wrong: `states` is the lifecycle."""
    kind = KINDS[kind_name]
    entry = _kind_entry(repos, kind)
    got = {s: _next_from(entry, s) for s in CANONICAL}
    assert got == {s: kind.table.get(s, []) for s in CANONICAL}


@pytest.mark.parametrize("kind_name", list(KINDS))
def test_states_agrees_with_offer_and_show(repos: dict[str, Path], kind_name: str) -> None:
    """`states`, `get_allowed_transitions` and `show --json` render the same source."""
    kind = KINDS[kind_name]
    root = repos[kind.name]
    entry = _kind_entry(repos, kind)
    service = _service(root)
    for status in CANONICAL:
        item_id = _item_id(kind, status)
        offered = service.get_allowed_transitions(_item(service, item_id))
        shown = _cli_json(root, ["show", item_id, "--json"])
        assert offered == _next_from(entry, status), (status, offered)
        assert shown.get("next_statuses") == offered, (status, shown)


CLI_NAMES = ["done", "blocked", "in_progress", "ready", "active", "abandoned",
             "underway", "shipped", "on-hold"]
CLI_KINDS = ["hdd", "custom", "workflow", "nautical"]


@pytest.mark.parametrize("kind_name", CLI_KINDS)
def test_cli_move_sample_matches_states(repos: dict[str, Path], kind_name: str) -> None:
    """A sample through the real `move` (gates skipped; no WIP limit is near)."""
    kind = KINDS[kind_name]
    root = repos[kind.name]
    entry = _kind_entry(repos, kind)
    bad = []
    for status in ("backlog", "in_progress", "blocked"):
        item_id = _item_id(kind, status)
        path = root / "work" / f"{item_id}.md"
        original = path.read_bytes()
        listed = _next_from(entry, status)
        for name in CLI_NAMES:
            try:
                result, text = _cli(root, ["move", item_id, name, "--skip-gates", "--no-commit"])
            finally:
                path.write_bytes(original)
            want = _expected_resolve(kind, name)
            expected = want is not None and want in listed
            if (result.exit_code == 0) != expected:
                bad.append(f"from {status}, {name!r}: exit {result.exit_code}, next={listed}")
    assert not bad, f"{kind.name}:\n" + "\n".join(bad)


# ---------------------------------------------------------------------------
# 3. Acceptance 3 — workflow: an unknown current state fails closed
# ---------------------------------------------------------------------------


def test_workflow_unknown_state_move_refuses_every_target(repos: dict[str, Path]) -> None:
    kind = KINDS["workflow"]
    service = _service(repos[kind.name])
    item = _item(service, _item_id(kind, "blocked"))  # `blocked` is not in the workflow
    assert item.file_path is not None
    original = item.file_path.read_bytes()
    accepted = []
    try:
        for target in WorkItemStatus:
            ok, _ = service._validate_transition(item, target)
            try:
                service.move_item(
                    item.id, target, commit=False, validate_workflow=True,
                    skip_gates=True, skip_wip_check=True,
                )
                accepted.append(f"move -> {target.value}")
            except ValueError:
                pass
            finally:
                item.file_path.write_bytes(original)
                item.status = WorkItemStatus.BLOCKED
            if ok:
                accepted.append(f"_validate_transition -> {target.value}")
    finally:
        item.file_path.write_bytes(original)
    assert not accepted, accepted


def test_workflow_unknown_state_offer_is_empty(repos: dict[str, Path]) -> None:
    kind = KINDS["workflow"]
    root = repos[kind.name]
    item_id = _item_id(kind, "blocked")
    service = _service(root)
    assert service.get_allowed_transitions(_item(service, item_id)) == []
    shown = _cli_json(root, ["show", item_id, "--json"])
    assert shown.get("next_statuses") == [] and shown.get("next_status_labels") == [], shown
    entry = _kind_entry(repos, kind)
    assert _next_from(entry, "blocked") == []
    # show's human line lists nothing
    result, text = _cli(root, ["show", item_id])
    assert result.exit_code == 0, text
    line = next((ln for ln in text.splitlines() if "Can move to" in ln), "")
    assert not set(re.findall(r"[a-z_]+", line)) & set(CANONICAL), line


def test_cli_move_from_unknown_workflow_state_refused(repos: dict[str, Path]) -> None:
    kind = KINDS["workflow"]
    root = repos[kind.name]
    item_id = _item_id(kind, "blocked")
    path = root / "work" / f"{item_id}.md"
    original = path.read_bytes()
    accepted = []
    for name in CANONICAL:
        try:
            result, _ = _cli(root, ["move", item_id, name, "--skip-gates", "--no-commit"])
        finally:
            path.write_bytes(original)
        if result.exit_code == 0:
            accepted.append(name)
    assert not accepted, accepted


# ---------------------------------------------------------------------------
# 4. Acceptance 4 — an illegal move names the legal targets, natively
# ---------------------------------------------------------------------------

REFUSALS = [
    # kind, from, typed target, from native, to native, legal natives
    ("hdd", "backlog", "complete", "draft", "complete", {"active", "abandoned"}),
    ("software", "ready", "done", "ready", "done", {"in_progress", "backlog", "blocked"}),
    ("workflow", "ready", "done", "ready", "done", {"in_progress", "backlog"}),
    ("custom", "backlog", "shipped", "backlog", "shipped", {"todo"}),
]


def _strip_canonical(name: str) -> str:
    return re.sub(r"\s*\([a-z_]+\)$", "", name.strip())


@pytest.mark.parametrize(
    ("kind_name", "status", "target", "from_native", "to_native", "legal"),
    REFUSALS, ids=[r[0] for r in REFUSALS],
)
def test_illegal_move_lists_legal_targets(
    repos: dict[str, Path], kind_name: str, status: str, target: str,
    from_native: str, to_native: str, legal: set[str],
) -> None:
    kind = KINDS[kind_name]
    root = repos[kind.name]
    item_id = _item_id(kind, status)
    path = root / "work" / f"{item_id}.md"
    original = path.read_bytes()
    try:
        result, text = _cli(root, ["move", item_id, target, "--skip-gates", "--no-commit"])
    finally:
        path.write_bytes(original)
    assert result.exit_code != 0, text
    flat = " ".join(text.split())
    assert f"Illegal move {item_id}: {from_native} → {to_native}" in flat, text
    match = re.search(rf"Legal from {re.escape(from_native)}:\s*([^\n]*)", flat)
    assert match, f"no 'Legal from {from_native}:' list:\n{text}"
    listed = match.group(1).split(".")[0]
    names = {_strip_canonical(n) for n in listed.split(",") if n.strip()}
    assert names == legal, f"listed {names}, want {legal}:\n{text}"


# ---------------------------------------------------------------------------
# 5. `states --json` shape, `--type`, terminal, gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind_name", list(KINDS))
def test_states_json_shape(repos: dict[str, Path], kind_name: str) -> None:
    kind = KINDS[kind_name]
    data = _states_json(repos[kind.name])
    entry = _entry(data, type_=None)
    assert set(entry) >= {"board", "theme", "type", "states"}, entry
    assert entry["theme"] == kind.theme
    assert entry["type"] is None
    canonicals = [s["canonical"] for s in entry["states"]]
    assert len(canonicals) == len(set(canonicals)), canonicals
    # every status the theme names natively is listed (ambiguity 3)
    assert set(LABELS[kind.theme]) <= set(canonicals), canonicals
    for state in entry["states"]:
        assert set(state) >= {"name", "canonical", "terminal", "next"}, state
        assert state["canonical"] in CANONICAL, state
        assert state["name"] == _label(kind, state["canonical"]), state
        assert state["terminal"] is (not state["next"]), state
        for nxt in state["next"]:
            assert set(nxt) >= {"name", "canonical", "gates"}, nxt
            assert nxt["canonical"] in CANONICAL, nxt
            assert nxt["name"] == _label(kind, nxt["canonical"]), nxt
            assert nxt["gates"] == [], nxt  # no gates configured here


def test_states_json_hdd_names(repos: dict[str, Path]) -> None:
    """Acceptance 5 (json): abandoned carries canonical blocked."""
    entry = _entry(_states_json(repos["hdd"]), type_=None)
    by_name = {s["name"]: s for s in entry["states"]}
    assert {"draft", "active", "complete", "abandoned"} <= set(by_name), list(by_name)
    assert by_name["abandoned"]["canonical"] == "blocked"
    assert by_name["complete"]["terminal"] is True
    assert [(n["name"], n["canonical"]) for n in by_name["active"]["next"]] == [
        ("complete", "done"), ("abandoned", "blocked"), ("draft", "backlog"),
    ]


@pytest.mark.parametrize("kind_name", [k for k, v in KINDS.items() if not v.workflow])
def test_type_irrelevant_without_workflow(repos: dict[str, Path], kind_name: str) -> None:
    kind = KINDS[kind_name]
    plain = _entry(_states_json(repos[kind.name]), type_=None)
    typed = _kind_entry(repos, kind)
    assert typed["states"] == plain["states"]
    assert typed["type"] == kind.item_type


def test_type_selects_workflow_graph(repos: dict[str, Path]) -> None:
    root = repos["workflow"]
    feature = _entry(_states_json(root, "--type", "feature"), type_="feature")
    bug = _entry(_states_json(root, "--type", "bug"), type_="bug")
    plain = _entry(_states_json(root), type_=None)
    assert {s: _next_from(feature, s) for s in CANONICAL} == {
        s: WORKFLOW_TABLE.get(s, []) for s in CANONICAL
    }
    for entry in (bug, plain):  # no workflow for `bug` / no type: the default table
        assert {s: _next_from(entry, s) for s in CANONICAL} == DEFAULT_TABLE


def test_theme_transitions_win_over_workflow(tmp_path: Path) -> None:
    """hdd has `transitions`: a workflow for `idea` changes neither `states` nor move."""
    root = _build(tmp_path / "r", KINDS["hdd"])
    wf = root / ".kanban" / "workflows"
    wf.mkdir()
    (wf / "idea.yurtle.md").write_text(_workflow_md(DEFAULT_TABLE, applies_to="idea"))
    entry = _entry(_cli_json(root, ["states", "--type", "idea", "--json"]), type_="idea")
    assert {s: _next_from(entry, s) for s in CANONICAL} == {
        s: HDD_TABLE.get(s, []) for s in CANONICAL
    }
    service = _service(root)
    item = _item(service, "IDEA-101")  # backlog/draft
    assert service._validate_transition(item, WorkItemStatus.READY)[0] is False
    assert service._validate_transition(item, WorkItemStatus.IN_PROGRESS)[0] is True


V1_GATES = """  gates:
    "* -> done":
      - id: ship_gate
        check: context.shipped
        message: "needs shipping"
    "in_progress -> review":
      - id: self_review
        check: context.self_reviewed
        message: "self-review"
"""


def test_states_json_gates_single_board(tmp_path: Path) -> None:
    """Acceptance 7: `* -> done` on every transition into done and no other."""
    root = _build(tmp_path / "r", KINDS["software"], gates=V1_GATES)
    entry = _entry(_cli_json(root, ["states", "--json"]), type_=None)
    seen_done = 0
    for state in entry["states"]:
        for nxt in state["next"]:
            want = []
            if nxt["canonical"] == "done":
                want.append("ship_gate")
                seen_done += 1
            if state["canonical"] == "in_progress" and nxt["canonical"] == "review":
                want.append("self_review")
            assert sorted(nxt["gates"]) == sorted(want), (state["canonical"], nxt)
    assert seen_done == 2  # in_progress -> done, review -> done


def test_states_json_gates_per_board_keyed_canonical(multi: Path) -> None:
    """The research (hdd) board's `* -> done` gate lands on `→ complete`; dev has none."""
    data = _cli_json(multi, ["states", "--json"])
    research = _entry(data, board="research", type_=None)
    dev = _entry(data, board="dev", type_=None)
    active = next(s for s in research["states"] if s["name"] == "active")
    gates = {n["name"]: n["gates"] for n in active["next"]}
    assert gates == {"complete": ["ship_gate"], "abandoned": [], "draft": []}, gates
    for state in dev["states"]:
        for nxt in state["next"]:
            assert nxt["gates"] == [], (state, nxt)


# ---------------------------------------------------------------------------
# 6. `--board`
# ---------------------------------------------------------------------------


def test_states_every_board_in_turn(multi: Path) -> None:
    data = _cli_json(multi, ["states", "--json"])
    plain = [e for e in data if e.get("type") is None]
    assert [(e["board"], e["theme"]) for e in plain] == [("dev", "software"), ("research", "hdd")]


def test_states_board_restricts(multi: Path) -> None:
    data = _cli_json(multi, ["states", "--board", "research", "--json"])
    assert {e["board"] for e in data} == {"research"}, data
    entry = _entry(data, board="research", type_=None)
    assert entry["theme"] == "hdd"
    assert {s: _next_from(entry, s) for s in CANONICAL} == {
        s: HDD_TABLE.get(s, []) for s in CANONICAL
    }


def test_states_board_restricts_human(multi: Path) -> None:
    result, text = _cli(multi, ["states", "--board", "dev"])
    assert result.exit_code == 0, text
    assert "abandoned" not in text and "draft" not in text, text
    assert re.search(r"^\s*in_progress\s*→", text, re.M), text


@pytest.mark.parametrize("json_flag", [[], ["--json"]], ids=["human", "json"])
def test_states_unknown_board_errors(multi: Path, json_flag: list[str]) -> None:
    result, text = _cli(multi, ["states", "--board", "nope", *json_flag])
    assert result.exit_code != 0, text
    assert result.exit_code != 2 or "No such command" not in text, text
    assert "nope" in text, text


# ---------------------------------------------------------------------------
# 7. Human `states` output and help
# ---------------------------------------------------------------------------


def _states_line(text: str, left: str) -> str | None:
    """The part after `→` on the line whose state column is exactly `left`."""
    for line in text.splitlines():
        head, sep, tail = line.partition("→")
        if sep and head.strip() == left:
            return tail.strip()
    return None


def test_states_human_hdd(repos: dict[str, Path]) -> None:
    """Acceptance 5 (human): native (canonical) where they differ; (terminal)."""
    result, text = _cli(repos["hdd"], ["states"])
    assert result.exit_code == 0, text
    assert "abandoned (blocked)" in text, text
    active = _states_line(text, "active (in_progress)")
    assert active is not None, text
    assert [n.strip() for n in active.split(",")] == [
        "complete (done)", "abandoned (blocked)", "draft (backlog)",
    ], active
    assert _states_line(text, "abandoned (blocked)") == "draft (backlog)", text
    assert _states_line(text, "draft (backlog)") == "active (in_progress), abandoned (blocked)"
    terminal = [ln for ln in text.splitlines() if "complete (done)" in ln and "(terminal)" in ln]
    assert terminal, text


def test_states_human_default_names_once(repos: dict[str, Path]) -> None:
    result, text = _cli(repos["software"], ["states"])
    assert result.exit_code == 0, text
    assert _states_line(text, "in_progress") == "review, done, blocked, ready", text
    assert _states_line(text, "backlog") == "ready, blocked", text
    assert any("done" in ln and "(terminal)" in ln for ln in text.splitlines()), text


def test_states_help_states_limits() -> None:
    result = CliRunner().invoke(main, ["states", "--help"])
    assert result.exit_code == 0, result.output
    help_text = " ".join(result.output.lower().split())
    for word in ("gate", "wip", "rule", "workflow", "--type", "--board", "--json"):
        assert word in help_text, f"help doesn't mention {word!r}:\n{result.output}"


# ---------------------------------------------------------------------------
# 8. `show`: Can move to / next_statuses / next_status_labels
# ---------------------------------------------------------------------------


def test_show_json_hdd_active(repos: dict[str, Path]) -> None:
    """Acceptance 6."""
    data = _cli_json(repos["hdd"], ["show", _item_id(KINDS["hdd"], "in_progress"), "--json"])
    assert data["status"] == "in_progress"
    assert data.get("next_statuses") == ["done", "blocked", "backlog"], data
    assert data.get("next_status_labels") == ["complete", "abandoned", "draft"], data


@pytest.mark.parametrize("kind_name", ["software", "custom", "workflow"])
def test_show_json_labels_parallel(repos: dict[str, Path], kind_name: str) -> None:
    kind = KINDS[kind_name]
    for status in CANONICAL:
        data = _cli_json(repos[kind.name], ["show", _item_id(kind, status), "--json"])
        assert data["status"] == status
        want = kind.table.get(status, [])
        assert data.get("next_statuses") == want, (status, data)
        assert data.get("next_status_labels") == [_label(kind, s) for s in want], (status, data)


def _can_move_to(text: str) -> list[str] | None:
    for line in text.splitlines():
        match = re.search(r"Can move to:?\s*(.*?)\s*(?:│|\|)?\s*$", line)
        if match:
            return [n.strip() for n in match.group(1).split(",") if n.strip()]
    return None


def test_show_human_hdd_active(repos: dict[str, Path]) -> None:
    result, text = _cli(repos["hdd"], ["show", _item_id(KINDS["hdd"], "in_progress")])
    assert result.exit_code == 0, text
    names = _can_move_to(text)
    assert names is not None, f"no 'Can move to' line:\n{text}"
    assert [_strip_canonical(n) for n in names] == ["complete", "abandoned", "draft"], names
    assert "abandoned (blocked)" in names, names
    for name in names:  # native, optionally `native (canonical)`
        assert re.fullmatch(r"[a-z_-]+( \([a-z_]+\))?", name), names


def test_show_human_default(repos: dict[str, Path]) -> None:
    kind = KINDS["software"]
    result, text = _cli(repos["software"], ["show", _item_id(kind, "in_progress")])
    assert result.exit_code == 0, text
    names = _can_move_to(text)
    assert names == ["review", "done", "blocked", "ready"], text
