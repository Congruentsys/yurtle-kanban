# ruff: noqa: F811  (the `world` fixture is imported, then used as a parameter)
"""Issue #750 — one parse of the parent's turtle block per HDD child create.

A local child create (hypothesis / experiment / literature under a parent) parsed
the parent's turtle block twice: once in `_parent_link_edit` (to write the link)
and again in `hdd_commands._print_parent_missing` -> `service.parent_link_state`
(to choose the message when nothing was added).

Decided behaviour ([steer] on #750, bucket 1):

- `_parent_link_edit` also returns why nothing was added; a new public
  `service.link_parent(parent_id, child_type, child_id) -> str` returns 'added' or
  that reason ('missing', 'no-relation', 'no-block', 'unparseable', 'linked');
  `update_parent_turtle_block` keeps its bool on top of it.
- Every create path carries the reason out (hdd `_update_parent`, and
  `create_item_and_push`'s local-commit and outside-repo results as
  `parent_state`), so the CLI never calls `parent_link_state` on these paths.

Observable: count `KanbanService._modify_turtle_block` (the rdflib parse) and
`KanbanService.parent_link_state` calls during one CLI child create. Each create
makes exactly one parse when the parent has a block, none when it has no block or
is missing, and never calls `parent_link_state`. The printed lines are unchanged.

Reuses the #645/#674/#705/#737 real-git harness.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import (
    HYP,
    KINDS,
    PAPER,
    Kind,
    links,
    seed_on_origin,
    turtle_block,
)
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    drop_remote,
    flat,
    world,
)
from tests.issues.test_737_unparseable_parent_block import LINKED as PAPER_LINKED
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

LIT, EXP = KINDS[0], KINDS[2]
CHILD = "H130.1"
BLOCK_RE = re.compile(r"```turtle\n.*?```\n", re.S)

# rdflib raises BadSyntax on this whatever the parent's type (#737's repro, generic)
BROKEN = (
    "```turtle\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "\n"
    '<#PARENT> rdfs:label "A parent" ;;; broken\n'
    "```\n"
)

PARENT_ID = {
    "literature": "IDEA-R-001",
    "hypothesis": "PAPER-130",
    "experiment": "H130.1",
}

# the CLI line for each state, unchanged by #750
LINE = {
    "added": "with inverse reference",  # "Updated <PARENT> with inverse reference"
    "linked": "already links",
    "unparseable": "has a turtle block that can't be parsed",
    "no-block": "has no turtle block",
    "missing": "is not on any board",
}

# how the create is run: plain local (`_update_parent`), `--push` with no remote
# (create_item_and_push's local commit), `--push` with the board outside the repo
PATHS = ["local", "commit", "outside"]


# --- harness --------------------------------------------------------------------------


class Counts:
    """Count the rdflib parses of a parent block and the `parent_link_state` calls."""

    def __init__(self) -> None:
        self.modify = 0
        self.state = 0


def count(monkeypatch: pytest.MonkeyPatch) -> Counts:
    counts = Counts()
    orig_modify = KanbanService._modify_turtle_block
    orig_state = KanbanService.parent_link_state

    def modify(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        counts.modify += 1
        return orig_modify(self, *args, **kwargs)

    def state(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        counts.state += 1
        return orig_state(self, *args, **kwargs)

    monkeypatch.setattr(KanbanService, "_modify_turtle_block", modify)
    monkeypatch.setattr(KanbanService, "parent_link_state", state)
    return counts


def set_block(path, block: str) -> None:
    text = path.read_text()
    assert BLOCK_RE.search(text), text
    path.write_text(BLOCK_RE.sub(lambda _m: block, text, count=1))


def prepare(world: World, monkeypatch, kind: Kind, state: str) -> None:
    """Seed the kind's parent, drop the remote, and put the parent in `state`
    (committed, so the no-remote `--push` create is not refused, #674)."""
    seed_on_origin(world, monkeypatch, kind)
    drop_remote(world)
    parent = world.a / kind.parent_rel
    if state == "linked":
        assert kind is HYP, "linked is only set up for the hypothesis kind (--id)"
        set_block(parent, PAPER_LINKED)
    elif state == "unparseable":
        set_block(parent, BROKEN)
    elif state == "no-block":
        set_block(parent, "")
    elif state == "missing":
        (world.a / "notes").mkdir(exist_ok=True)
        git(world.a, "mv", kind.parent_rel, f"notes/{parent.name}")
    if state != "added":
        git(world.a, "add", "-A")
        git(world.a, "commit", "-q", "-m", f"parent: {state}")
    config_mod._theme_cache.clear()


def argv_for(kind: Kind, path: str, state: str) -> list[str]:
    argv = list(kind.argv) if path != "local" else [a for a in kind.argv if a != "--push"]
    if kind is HYP and state == "linked":
        argv += ["--id", CHILD]  # the child PAPER_LINKED already links
    return argv


def run_create(world: World, monkeypatch, kind: Kind, path: str, state: str):
    if path == "outside":
        # create_item_and_push's outside-repo branch (#174): the board is not in git
        monkeypatch.setattr(KanbanService, "_outside_repo", lambda self, *paths: True)
    counts = count(monkeypatch)
    result = invoke(world, monkeypatch, argv_for(kind, path, state))
    return result, counts


def expected_parses(state: str) -> int:
    return 0 if state in ("no-block", "missing") else 1


def service(world: World) -> KanbanService:
    config_mod._theme_cache.clear()
    return KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)


# --- CLI: one parse per create, no parent_link_state, same line -----------------------

HYP_CASES = [
    pytest.param(path, state, id=f"{path}-{state}")
    for path in PATHS
    for state in ("added", "linked", "unparseable", "no-block", "missing")
]
OTHER_CASES = [
    pytest.param(kind, path, state, id=f"{kind.name}-{path}-{state}")
    for kind in (LIT, EXP)
    for path in PATHS
    for state in ("added", "unparseable", "no-block", "missing")
]


@pytest.mark.parametrize(("path", "state"), HYP_CASES)
def test_hypothesis_create_parses_parent_once(world, monkeypatch, path, state) -> None:
    prepare(world, monkeypatch, HYP, state)
    result, counts = run_create(world, monkeypatch, HYP, path, state)
    out = flat(result)
    assert result.exit_code == 0, out
    assert counts.state == 0, f"parent_link_state called {counts.state}x: {out}"
    assert counts.modify == expected_parses(state), (
        f"{counts.modify} parses of the parent block, want {expected_parses(state)}: {out}"
    )


@pytest.mark.parametrize(("kind", "path", "state"), OTHER_CASES)
def test_literature_and_experiment_create_parse_parent_once(
    world, monkeypatch, kind, path, state
) -> None:
    prepare(world, monkeypatch, kind, state)
    result, counts = run_create(world, monkeypatch, kind, path, state)
    out = flat(result)
    assert result.exit_code == 0, out
    assert counts.state == 0, f"parent_link_state called {counts.state}x: {out}"
    assert counts.modify == expected_parses(state), (
        f"{counts.modify} parses of the parent block, want {expected_parses(state)}: {out}"
    )


# controls: the printed line per state is unchanged (green before and after)


@pytest.mark.parametrize(("path", "state"), HYP_CASES)
def test_control_hypothesis_create_line(world, monkeypatch, path, state) -> None:
    prepare(world, monkeypatch, HYP, state)
    result, _ = run_create(world, monkeypatch, HYP, path, state)
    out = flat(result)
    assert result.exit_code == 0, out
    assert f"PAPER-130 {LINE[state]}" in out or (
        state == "added" and "Updated PAPER-130 with inverse reference" in out
    ), out
    for other, line in LINE.items():
        if other != state:
            assert line not in out, out
    if state == "linked":
        assert f"PAPER-130 already links to {CHILD}" in out, out


@pytest.mark.parametrize(("kind", "path", "state"), OTHER_CASES)
def test_control_literature_and_experiment_create_line(
    world, monkeypatch, kind, path, state
) -> None:
    prepare(world, monkeypatch, kind, state)
    result, _ = run_create(world, monkeypatch, kind, path, state)
    out = flat(result)
    assert result.exit_code == 0, out
    parent = PARENT_ID[kind.name]
    if state == "added":
        assert f"Updated {parent} with inverse reference" in out, out
    else:
        assert f"{parent} {LINE[state]}" in out, out
    for other, line in LINE.items():
        if other != state:
            assert line not in out, out


# --- service: link_parent, update_parent_turtle_block, parent_state -------------------


@pytest.mark.parametrize(
    "state", ["added", "linked", "unparseable", "no-block", "missing"]
)
def test_link_parent_returns_added_or_the_reason(world, monkeypatch, state) -> None:
    prepare(world, monkeypatch, HYP, state)
    svc = service(world)
    counts = count(monkeypatch)
    assert svc.link_parent("PAPER-130", "hypothesis", CHILD) == state
    assert counts.modify == expected_parses(state), counts.modify
    assert counts.state == 0, counts.state


def test_link_parent_no_relation(world, monkeypatch) -> None:
    prepare(world, monkeypatch, HYP, "added")
    assert service(world).link_parent("PAPER-130", "no-such-type", CHILD) == "no-relation"


def test_link_parent_twice_is_linked(world, monkeypatch) -> None:
    prepare(world, monkeypatch, HYP, "added")
    svc = service(world)
    assert svc.link_parent("PAPER-130", "hypothesis", CHILD) == "added"
    text = (world.a / PAPER).read_text()
    assert links(turtle_block(text), HYP, CHILD), text
    assert svc.link_parent("PAPER-130", "hypothesis", CHILD) == "linked"
    assert (world.a / PAPER).read_text() == text  # the second call writes nothing


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        pytest.param("added", True, id="added"),
        pytest.param("linked", False, id="linked"),
        pytest.param("unparseable", False, id="unparseable"),
        pytest.param("no-block", False, id="no-block"),
        pytest.param("missing", False, id="missing"),
    ],
)
def test_control_update_parent_turtle_block_still_bool(
    world, monkeypatch, state, expected
) -> None:
    prepare(world, monkeypatch, HYP, state)
    got = service(world).update_parent_turtle_block("PAPER-130", "hypothesis", CHILD)
    assert got is expected, got


@pytest.mark.parametrize("path", ["commit", "outside"])
@pytest.mark.parametrize("state", ["linked", "unparseable", "no-block", "missing"])
def test_create_item_and_push_carries_parent_state(world, monkeypatch, path, state) -> None:
    """Both no-push branches of create_item_and_push report why nothing was linked,
    as `parent_state`, from the one parse they made."""
    prepare(world, monkeypatch, HYP, state)
    if path == "outside":
        monkeypatch.setattr(KanbanService, "_outside_repo", lambda self, *paths: True)
    svc = service(world)
    counts = count(monkeypatch)
    result = svc.create_item_and_push(
        item_type=WorkItemType.HYPOTHESIS,
        title="Hyp 750",
        item_id=CHILD,
        parent="PAPER-130",
    )
    assert result["success"], result
    assert not result["parent_linked"], result
    assert result.get("parent_state") == state, result
    assert counts.modify == expected_parses(state), counts.modify
    assert counts.state == 0, counts.state
