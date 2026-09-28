"""Issue #879: the fetched dependency board's duplicate check compares resolved paths;
an outside board's parse warnings are reported once per call.

Found in the PR #872 (#833) review. `_dependency_board_at(rev)` adds the working
tree's outside-the-repo items to origin's, then counts an ID held by two different
paths as a duplicate. It compares the paths as written, while the scan
(`_index_item`) compares `path.resolve()`. So two boards that reach ONE outside folder
through two spellings (a symlinked directory, or `/tmp` versus `/private/tmp` on
macOS) make one file two, and `update --push --add-dep` refuses the dependency as
"on more than one board". And when origin holds no config (the local service judges),
every push attempt's `_items_outside_repo` appends a malformed outside file's parse
warning to `service.parse_warnings` again: a retry reports it twice.

The decided fix ([steer] bucket 1): compare `path.resolve()` in the duplicate check,
and parse-warn once per call.

Pinned here:

1. Two outside boards, one folder, two spellings (symlinked directory; on macOS also
   `/private/...` versus its `/...` alias): `_dependency_board_at(rev)` names no
   duplicate for EXP-900, and `update --push --add-dep EXP-900` (service and CLI) is
   `won`.
2. A malformed outside file, origin holding no config, one non-fast-forward rejection
   (a rival in attempt 0's seam) so the push retries: `service.parse_warnings` holds
   that file's warning ONCE.

Control (green before the fix):

3. Two genuinely different outside files holding one ID are still a duplicate: listed
   by `_dependency_board_at(rev)`, and the dependency refused "on more than one board".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.issues.test_574_sync_and_push import Recorder, _seed_item, rival, service
from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_805_external_claim import ACTOR, EXT_TEXT, _ext_item
from tests.issues.test_833_external_dependency_board import (
    HOW,
    ITEM_ID,
    add_dep,
    assert_one_item_commit,
    remote_front,
)
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import fold_id

DEP = "EXP-900"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """The #574 world (origin and both clones hold EXP-001), agent-A acting."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("YURTLE_AGENT", ACTOR)
    w = World(tmp_path)
    _seed_item(w)
    return w


BAD_TEXT = "---\nid: EXP-950\ntitle: [unclosed\ntype: expedition\n---\n\n# Bad\n"


def _two_spellings(world: World, how: str) -> tuple[Path, Path]:
    """The outside folder and a second spelling of it: a symlinked directory, or (on
    macOS) the `/private/...` path without its `/private` prefix."""
    ext = (world.a.parent / "ext-board").resolve()
    ext.mkdir()
    if how == "symlink":
        alias = world.a.parent / "ext-alias"
        alias.symlink_to(ext, target_is_directory=True)
        return ext, alias
    assert str(ext).startswith("/private/"), ext
    alias = Path(str(ext)[len("/private") :])
    assert alias != ext and alias.resolve() == ext
    return ext, alias


def _alias_board_world(world: World, how: str) -> Path:
    """A's config: the in-repo board plus TWO outside boards spelling one folder two
    ways; committed and pushed, so origin judges by it. EXP-900 in that folder."""
    ext, alias = _two_spellings(world, how)
    (world.a / ".kanban" / "config.yaml").write_text(
        'version: "2.0"\n'
        "boards:\n"
        "  - name: main\n"
        "    preset: nautical\n"
        '    path: "kanban-work/"\n'
        "  - name: outside\n"
        "    preset: nautical\n"
        f'    path: "{ext}/"\n'
        "  - name: alias\n"
        "    preset: nautical\n"
        f'    path: "{alias}/"\n'
        "default_board: main\n"
    )
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", "multi-board config, one outside folder twice")
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    cfg = KanbanConfig.load(world.a / ".kanban" / "config.yaml")
    assert cfg.is_multi_board and len(cfg.boards) == 3
    (ext / "expeditions").mkdir()
    (ext / "expeditions" / "EXP-900-out.md").write_text(EXT_TEXT)
    assert (alias / "expeditions" / "EXP-900-out.md").exists()
    return ext


SPELLINGS = [
    "symlink",
    pytest.param(
        "private",
        marks=pytest.mark.skipif(sys.platform != "darwin", reason="macOS /private alias"),
    ),
]


# --- 1. one outside file, two spellings: not a duplicate ---------------------------------


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_dependency_board_counts_one_outside_file_once(world, spelling) -> None:
    _alias_board_world(world, spelling)
    rev = git(world.a, "rev-parse", "HEAD").strip()

    duplicated = service(world)._dependency_board_at(rev)[1]

    assert fold_id(DEP) not in duplicated, (
        f"one outside file reached through two spellings counted twice: {duplicated}"
    )


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_add_dep_on_outside_item_reached_two_ways_is_pushed(
    world, monkeypatch, spelling, how
) -> None:
    _alias_board_world(world, spelling)
    base = world.remote_sha()

    ok, said = add_dep(world, monkeypatch, how, DEP)

    assert ok, said
    assert "more than one board" not in said.lower(), said
    assert_one_item_commit(world, base)
    assert [str(d).upper() for d in remote_front(world)["depends_on"]] == [DEP]


# --- 2. an outside parse warning, once per call ----------------------------------------


def _drop_origin_config(world: World) -> None:
    """B removes `.kanban/config.yaml` from origin: the local service judges (#831)."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    git(world.b, "rm", "-q", ".kanban/config.yaml")
    git(world.b, "commit", "-m", "no config on origin")
    git(world.b, "push", "origin", f"HEAD:refs/heads/{world.default}")


def test_outside_parse_warning_is_reported_once_across_retries(world) -> None:
    ext = _ext_item(world).parent.parent  # EXP-900 on the outside board
    bad = ext / "expeditions" / "EXP-950-bad.md"
    bad.write_text(BAD_TEXT)
    _drop_origin_config(world)
    tip = world.remote_sha()
    assert ".kanban/config.yaml" not in world.remote_files()
    svc = service(world)
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)

    out = svc.update_item_push(
        ITEM_ID, add_depends_on=[DEP], sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "won", f"{out.kind}: {out.message}"
    assert rec.seams == [0, 1], "the push did not retry once"
    assert world.remote_sha() != tip
    of_bad = [r for p, r in svc.parse_warnings if Path(p).resolve() == bad.resolve()]
    assert len(of_bad) == 1, (
        f"the malformed outside file's warning is held {len(of_bad)} times, not once: "
        f"{svc.parse_warnings}"
    )


# --- 3. control: two different outside files, one ID ----------------------------------


@pytest.mark.parametrize("how", HOW)
def test_two_different_outside_files_with_one_id_are_still_a_duplicate(
    world, monkeypatch, how
) -> None:
    first = _ext_item(world)
    second = first.parent / "EXP-900-copy.md"
    second.write_text(EXT_TEXT.replace('"Out"', '"Copy"').replace("# Out", "# Copy"))
    rev = git(world.a, "rev-parse", "HEAD").strip()
    base = world.remote_sha()

    duplicated = service(world)._dependency_board_at(rev)[1]
    assert sorted(p.resolve() for p in duplicated.get(fold_id(DEP), [])) == sorted(
        [first.resolve(), second.resolve()]
    ), duplicated

    ok, said = add_dep(world, monkeypatch, how, DEP)

    assert not ok, said
    assert "more than one board" in said.lower(), said
    assert world.remote_sha() == base, "origin changed"
