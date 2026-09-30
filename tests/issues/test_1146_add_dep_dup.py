# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""#1146: `--add-dep` of an id already stored in another spelling is no change.

#641 ruled that `EXP-5` is `EXP-05` is `EXP-005`. [steer] on #1146 (bucket 2, as the
issue's Expected): an add-dep whose `_dup_key` is already in the item's `depends_on`
reports "no changes" and writes nothing - the stored list never gains a second
spelling of one node. With it, the low-priority notes from the #1143 review:

- an end-to-end `update --push --add-dep` refused as a cycle through a stored
  `EXP-05` spelling (#1139 pinned only `_dependency_board_at`'s graph);
- `--push`'s own "no changes" for a stored alternate spelling.

`--add-dep` alone is already no change end to end: #1136's `_edited_text` drops an add
whose `_dup_key` is held before `_check_new_dependencies` sees it; those tests pin it.
Red before the fix: `_check_new_dependencies` itself (its `had` compares folded
spellings), which `--depends-on EXP-05,EXP-5` reaches, writing both spellings.

Fixtures: #576's two-board repo (`repo`: EXP-1..EXP-5, H1.1; EXP-5 depends on EXP-2)
for the local path; #833's `world` (#574's origin with clones A and B, EXP-001 seeded)
with #590's `b_push` for the pushed path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_574_claim import output_of
from tests.issues.test_574_sync_and_push import ITEM, Recorder, service
from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _flat,
    _ok,
    repo,
)
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_833_external_dependency_board import world  # noqa: F401
from tests.issues.test_1125_id_key_lookup import _deps_of, _write
from yurtle_kanban.cli import main
from yurtle_kanban.service import _Edits

HOW = ["service", "cli"]


# --- local: a stored alternate spelling ----------------------------------------------------


def _stored(repo: Repo, spelling: str) -> Path:
    """EXP-006 depends on `spelling` (EXP-5's id, another spelling), written directly;
    committed."""
    path = _write(repo, "work/expeditions/EXP-006-padded.md", "EXP-006", [spelling])
    repo.commit(f"EXP-006 -> {spelling}")
    return path


@pytest.mark.parametrize("stored", ["EXP-05", "exp-005"])
@pytest.mark.parametrize("add", ["EXP-5", "exp-5", "EXP-005"])
def test_add_dep_of_stored_spelling_is_no_change(
    repo: Repo, stored: str, add: str
) -> None:
    path = _stored(repo, stored)
    before, head = path.read_bytes(), repo.head()
    result = _ok(["update", "EXP-006", "--add-dep", add])
    assert "no changes" in _flat(result.output).lower(), result.output
    assert path.read_bytes() == before, path.read_text(encoding="utf-8")
    assert repo.head() == head


def test_depends_on_naming_stored_id_twice_is_no_change(repo: Repo) -> None:
    """`--depends-on EXP-05,EXP-5` names the stored edge in two spellings: one edge,
    kept as stored, so no change."""
    path = _stored(repo, "EXP-05")
    before = path.read_bytes()
    result = _ok(["update", "EXP-006", "--depends-on", "EXP-05,EXP-5"])
    assert "no changes" in _flat(result.output).lower(), result.output
    assert path.read_bytes() == before, path.read_text(encoding="utf-8")


@pytest.mark.parametrize("given", ["EXP-005", "EXP-5"])
def test_depends_on_another_spelling_of_the_stored_edge_is_no_change(
    repo: Repo, given: str
) -> None:
    """r1 F1: `--depends-on EXP-005` on a stored `EXP-05` names that same edge: it
    keeps the stored spelling (no change), never writes a third one."""
    path = _stored(repo, "EXP-05")
    before = path.read_bytes()
    result = _ok(["update", "EXP-006", "--depends-on", given])
    assert "no changes" in _flat(result.output).lower(), result.output
    assert path.read_bytes() == before, path.read_text(encoding="utf-8")


def test_service_edit_of_stored_spelling_is_no_change(repo: Repo) -> None:
    """The service's own answer: no changes, nothing to write."""
    path = _stored(repo, "EXP-05")
    service = repo.service()
    item = service.get_item("EXP-006")
    assert item is not None
    content = path.read_text(encoding="utf-8")
    new, changes = service._edited_text(
        item, content, service._checked_edits(_Edits(add_depends_on=["EXP-5"]))
    )
    assert changes == [] and new == content, changes


def test_check_new_dependencies_reads_stored_spelling_as_had(repo: Repo) -> None:
    """`had` is compared by `_dup_key`: the stored `EXP-05` is the edge `EXP-5`
    names, so it is not a new edge to re-check or rename."""
    _stored(repo, "EXP-05")
    service = repo.service()
    item = service.get_item("EXP-006")
    assert item is not None
    assert service._check_new_dependencies(item, ["EXP-05", "EXP-5"], False) == ["EXP-05"]


def test_control_new_target_is_still_added(repo: Repo) -> None:
    """A target of another number is a new edge, written as the board spells it."""
    path = _stored(repo, "EXP-05")
    _ok(["update", "EXP-006", "--add-dep", "EXP-1"])
    assert _deps_of(path) == ["EXP-05", "EXP-1"]


# --- pushed: the cycle and the no-change through a stored `EXP-05` -------------------------


def plain(item_id: str, deps: list[str]) -> str:
    return (
        f'---\nid: {item_id}\ntitle: "Item {item_id}"\ntype: expedition\n'
        f"status: backlog\ndepends_on: [{', '.join(deps)}]\n---\n\n# Item {item_id}\n\n"
        "A description long enough.\n"
    )


def push_add_dep(
    world: World, monkeypatch: pytest.MonkeyPatch, how: str, item_id: str, dep: str
) -> tuple[str, str]:
    """`update item_id --add-dep dep --push`: (outcome kind or exit, message)."""
    if how == "service":
        rec = Recorder()
        out = service(world).update_item_push(
            item_id, add_depends_on=[dep], sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
        )
        return out.kind, out.message
    monkeypatch.chdir(world.a)
    result: Any = CliRunner().invoke(main, ["update", item_id, "--add-dep", dep, "--push"])
    return f"exit {result.exit_code}", output_of(result)


def remote_text(world: World, rel: str) -> str:
    return git(world.remote, "show", f"{world.default}:{rel}")


@pytest.mark.parametrize("how", HOW)
def test_push_add_dep_closing_cycle_through_stored_spelling_is_refused(
    world, monkeypatch, how
) -> None:
    """On origin, EXP-006's file says `EXP-05`: `EXP-5 --add-dep EXP-006 --push`
    closes EXP-5 → EXP-006 → EXP-5 and is refused; origin is unchanged."""
    b_push(world, {
        f"{EXP_DIR}/EXP-5-five.md": plain("EXP-5", []),
        f"{EXP_DIR}/EXP-006-six.md": plain("EXP-006", ["EXP-05"]),
    })
    base = world.remote_sha()

    kind, said = push_add_dep(world, monkeypatch, how, "EXP-5", "EXP-006")

    assert kind in ("refused", "exit 1"), f"{kind}: {said}"
    assert "cycle" in said.lower(), said
    assert "EXP-5 → EXP-006 → EXP-5" in said, said
    assert world.remote_sha() == base, "origin changed"


@pytest.mark.parametrize("how", HOW)
def test_push_add_dep_of_stored_spelling_is_no_change(world, monkeypatch, how) -> None:
    """On origin, EXP-001's file says `EXP-05`: `--add-dep EXP-5 --push` is no
    change, and origin's file keeps its one spelling."""
    b_push(world, {
        f"{EXP_DIR}/EXP-5-five.md": plain("EXP-5", []),
        ITEM: plain("EXP-001", ["EXP-05"]),
    })
    base = world.remote_sha()
    before = remote_text(world, ITEM)

    kind, said = push_add_dep(world, monkeypatch, how, "EXP-001", "EXP-5")

    assert kind in ("noop", "exit 0"), f"{kind}: {said}"
    assert "no changes" in said.lower(), said
    assert world.remote_sha() == base, "origin changed"
    assert remote_text(world, ITEM) == before
    front = yaml.safe_load(before.split("---", 2)[1])
    assert front["depends_on"] == ["EXP-05"]
