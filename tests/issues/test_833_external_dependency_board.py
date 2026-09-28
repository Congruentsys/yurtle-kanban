"""Issue #833: `update --push` sees an external board's items as dependencies.

Follow-up from the PR #820 round-3 review (#805). With a multi-board config where one
board lies outside the git repository, `update_item_push('EXP-001',
add_depends_on=['EXP-900'])`, EXP-900 being on the EXTERNAL board, is refused with
"EXP-900 is on no board", which is false. `_dependency_board_at(rev)` reads only
origin's tree, and origin can't hold an external board. Its duplicate-ID check has
the same blind spot.

The decided fix ([steer] bucket 1): the fetched dependency board is origin's tree
items PLUS every external board's working-tree items, and the duplicate-ID check
covers the union.

Pinned here, through the service and the CLI (`update --add-dep ... --push`):

1. An in-repo EXP-001 gains a dependency on external EXP-900: `won`, pushed as one
   commit touching only EXP-001, and origin's EXP-001 lists EXP-900.
2. EXP-900 on the external board AND in origin's tree (a duplicate): the dependency
   is refused as ambiguous ("is on more than one board"), and origin is unchanged.

Controls (green before the fix):

3. A dependency on an ID on no board at all is still refused "is on no board".
4. A dependency on an in-repo item only on origin (not pulled into A) is `won`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_574_claim import output_of
from tests.issues.test_574_sync_and_push import ITEM, Recorder, _seed_item, commit_files, service
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_805_external_claim import ACTOR, _ext_item
from yurtle_kanban.cli import main

ITEM_ID = "EXP-001"
HOW = ["service", "cli"]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """The #574 world (origin and both clones hold EXP-001), agent-A acting."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("YURTLE_AGENT", ACTOR)
    w = World(tmp_path)
    _seed_item(w)
    return w


def plain(item_id: str, title: str) -> str:
    return (
        f'---\nid: {item_id}\ntitle: "{title}"\ntype: expedition\nstatus: backlog\n'
        f"depends_on: []\n---\n\n# {title}\n\nA description long enough.\n"
    )


def add_dep(
    world: World, monkeypatch: pytest.MonkeyPatch, how: str, dep: str
) -> tuple[bool, str]:
    """Add `dep` to EXP-001's dependencies with `--push`: (succeeded, message)."""
    if how == "service":
        rec = Recorder()
        out = service(world).update_item_push(
            ITEM_ID, add_depends_on=[dep], sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
        )
        return out.kind == "won" and out.exit_code == 0, f"{out.kind}: {out.message}"
    monkeypatch.chdir(world.a)
    result: Any = CliRunner().invoke(main, ["update", ITEM_ID, "--add-dep", dep, "--push"])
    return result.exit_code == 0, f"exit {result.exit_code}: {output_of(result)}"


def remote_front(world: World) -> dict[str, Any]:
    """EXP-001's frontmatter as origin's default branch holds it."""
    text = git(world.remote, "show", f"{world.default}:{ITEM}")
    return yaml.safe_load(text.split("---", 2)[1])


def assert_one_item_commit(world: World, base: str) -> None:
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert commit_files(world.remote, tip) == [ITEM], "the update commit touched other files"


# --- 1. a dependency on an external-board item ------------------------------------------


@pytest.mark.parametrize("how", HOW)
def test_add_dep_on_external_item_is_pushed(world, monkeypatch, how) -> None:
    _ext_item(world)  # EXP-900 on the external board, in A's working tree only
    base = world.remote_sha()

    ok, said = add_dep(world, monkeypatch, how, "EXP-900")

    assert ok, said
    assert "no board" not in said.lower(), said
    assert_one_item_commit(world, base)
    assert [str(d).upper() for d in remote_front(world)["depends_on"]] == ["EXP-900"]


# --- 2. the duplicate check sees the union ----------------------------------------------


@pytest.mark.parametrize("how", HOW)
def test_add_dep_on_id_both_external_and_on_origin_is_ambiguous(world, monkeypatch, how) -> None:
    _ext_item(world)  # EXP-900 on the external board
    b_push(world, {f"{EXP_DIR}/EXP-900-dup.md": plain("EXP-900", "Dup")})  # and on origin
    assert not (world.a / EXP_DIR / "EXP-900-dup.md").exists()
    base = world.remote_sha()

    ok, said = add_dep(world, monkeypatch, how, "EXP-900")

    assert not ok, said
    assert "more than one board" in said.lower(), said
    assert world.remote_sha() == base, "origin changed"


# --- 3. control: an ID on no board is still refused -------------------------------------


@pytest.mark.parametrize("how", HOW)
def test_add_dep_on_unknown_id_is_still_on_no_board(world, monkeypatch, how) -> None:
    _ext_item(world)
    base = world.remote_sha()

    ok, said = add_dep(world, monkeypatch, how, "EXP-777")

    assert not ok, said
    assert "no board" in said.lower(), said
    assert world.remote_sha() == base, "origin changed"


# --- 4. control: an in-repo item only on origin is known ----------------------------------


@pytest.mark.parametrize("how", HOW)
def test_add_dep_on_in_repo_item_only_on_origin_is_pushed(world, monkeypatch, how) -> None:
    _ext_item(world)
    b_push(world, {f"{EXP_DIR}/EXP-004-four.md": plain("EXP-004", "Four")})
    assert not (world.a / EXP_DIR / "EXP-004-four.md").exists()
    base = world.remote_sha()

    ok, said = add_dep(world, monkeypatch, how, "EXP-004")

    assert ok, said
    assert_one_item_commit(world, base)
    assert [str(d).upper() for d in remote_front(world)["depends_on"]] == ["EXP-004"]
