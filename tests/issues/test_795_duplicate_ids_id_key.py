# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""#795: `duplicate_ids` keys on `_id_key`, as `_holders_at` does.

#641 ruled that `EXP-3` is `EXP-003`: ids are the same when the text before their
number (separator included, case folded) and the number are. `_holders_at` compares
that way, so with a parent held as `IDEA-R-001` and `IDEA-R-1`, `--push` refuses the
parent link. The board's `duplicate_ids` keyed on the case-folded ID only, so it saw
two items and no duplicate, and the LOCAL create linked one copy. [steer] on #795:
one rule — `duplicate_ids` keys on `_id_key`, so:

- a board holding `EXP-3` and `EXP-003` reports them in `duplicate_ids`, with both
  files, and `validate` prints a Duplicate ID for the pair;
- writers refuse either spelling as ambiguous (`update`), and a dependency on it;
- the push side's duplicate map (`_dependency_board_at`) agrees;
- a local HDD child create under such a parent is refused, as `--push` is.

Controls: `EXP-3` and `EXP-4` are no duplicate; `EXP3` is not `EXP-3` (#661); a case
variant (`exp-003` vs `EXP-003`) is still a duplicate, keyed as today.

Fixtures: #576's two-board repo; #645/#754's real-git World for the HDD parent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _flat,
    _item_text,
    _refused,
    invoke,
    repo,
)
from tests.issues.test_585_create_push_loop import World, git, porcelain
from tests.issues.test_603_push_failure_messages import invoke as invoke_world
from tests.issues.test_645_parent_in_cas import KINDS, seed_on_origin
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    flat,
    world,
)
from tests.issues.test_754_duplicate_parent_and_epic import (
    ALLOC,
    PARENT_NEEDLES,
    _has,
    _service,
    _tree,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.service import KanbanService

NEEDLES = ("is on more than one board", "ambiguous")


def _write(repo: Repo, rel: str, item_id: str) -> Path:
    """An expedition file at `rel` whose frontmatter `id:` is `item_id`."""
    path = repo.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_item_text(item_id, []), encoding="utf-8")
    return path


def _files_for(svc: KanbanService, item_id: str) -> list[Path]:
    """The files `duplicate_ids` records for `item_id`'s id (`_id_key`), whatever
    spelling of it the dict is keyed by."""
    want = KanbanService._id_key(item_id)
    hits = [f for k, f in svc.duplicate_ids.items() if KanbanService._id_key(k) == want]
    assert len(hits) <= 1, f"one id recorded under several keys: {svc.duplicate_ids}"
    return hits[0] if hits else []


@pytest.fixture
def padded(repo: Repo) -> Path:
    """EXP-3 (the fixture's) and a second file holding EXP-003, committed."""
    path = _write(repo, "work/expeditions/EXP-003-padded.md", "EXP-003")
    repo.commit("EXP-003 beside EXP-3")
    return path


# --- the board -------------------------------------------------------------------------


def test_padded_pair_is_a_duplicate(repo: Repo, padded: Path) -> None:
    svc = repo.service()
    svc.scan()
    files = {p.resolve() for p in _files_for(svc, "EXP-3")}
    assert files == {repo.path("EXP-3").resolve(), padded.resolve()}, svc.duplicate_ids


def test_validate_reports_padded_pair(repo: Repo, padded: Path) -> None:
    result = invoke(["validate"])
    out = _flat(result.output)
    assert "duplicate id" in out.lower(), out
    assert padded.name in out and repo.path("EXP-3").name in out, out


@pytest.mark.parametrize("item_id", ["EXP-3", "EXP-003"])
def test_refuse_duplicate_either_spelling(repo: Repo, padded: Path, item_id: str) -> None:
    svc = repo.service()
    item = svc.get_item(item_id)
    assert item is not None and item.id == item_id
    with pytest.raises(ValueError, match="is on more than one board"):
        svc.refuse_duplicate(item, "an update")


@pytest.mark.parametrize("item_id", ["EXP-3", "EXP-003"])
def test_update_refuses_either_spelling(repo: Repo, padded: Path, item_id: str) -> None:
    _refused(repo, ["update", item_id, "--priority", "high"], item_id, *NEEDLES)


@pytest.mark.parametrize("target", ["EXP-3", "EXP-003"])
def test_dependency_on_padded_pair_refused(repo: Repo, padded: Path, target: str) -> None:
    _refused(
        repo, ["update", "EXP-5", "--add-dep", target],
        "is on more than one board", "a dependency on it is ambiguous",
    )


def test_push_side_duplicate_map_agrees(repo: Repo, padded: Path) -> None:
    """`_dependency_board_at` (what a pushed dependency edit is checked against) sees
    the pair at HEAD as one duplicated id, as the scan does."""
    svc = repo.service()
    svc.scan()
    _, duplicated = svc._dependency_board_at("HEAD")
    want = KanbanService._id_key("EXP-3")
    hits = [f for k, f in duplicated.items() if KanbanService._id_key(k) == want]
    assert len(hits) == 1 and len(hits[0]) == 2, duplicated


# --- controls --------------------------------------------------------------------------


def test_control_distinct_numbers_no_duplicate(repo: Repo) -> None:
    svc = repo.service()
    svc.scan()
    assert svc.duplicate_ids == {}, svc.duplicate_ids


def test_control_no_separator_is_another_id(repo: Repo) -> None:
    """`EXP3` is not `EXP-3` (#661)."""
    _write(repo, "work/expeditions/EXP3-nosep.md", "EXP3")
    repo.commit("EXP3")
    svc = repo.service()
    svc.scan()
    assert svc.get_item("EXP3") is not None
    assert svc.duplicate_ids == {}, svc.duplicate_ids


def test_control_case_variant_keyed_as_today(repo: Repo) -> None:
    upper = _write(repo, "work/expeditions/EXP-007-a.md", "EXP-007")
    lower = _write(repo, "work/expeditions/exp-007-b.md", "exp-007")
    repo.commit("case variants")
    svc = repo.service()
    svc.scan()
    assert list(svc.duplicate_ids) == ["EXP-007"], svc.duplicate_ids
    assert {p.resolve() for p in svc.duplicate_ids["EXP-007"]} == {
        upper.resolve(), lower.resolve()
    }


# --- an HDD child create: local and --push agree ----------------------------------------

LIT = next(k for k in KINDS if k.name == "literature")  # parent IDEA-R-001


def _padded_parent(world: World, monkeypatch) -> str:
    """Seed IDEA-R-001 on origin, then a second file holding `IDEA-R-1`, committed and
    pushed so the local board and origin agree. Returns the copy's path."""
    seed_on_origin(world, monkeypatch, LIT)
    dest = world.a / "research" / "measures" / "IDEA-R-1-copy.md"
    shutil.copyfile(world.a / LIT.parent_rel, dest)
    text = dest.read_text()
    assert "id: IDEA-R-001" in text, text
    dest.write_text(text.replace("id: IDEA-R-001", "id: IDEA-R-1", 1))
    git(world.a, "add", "-A")
    git(world.a, "commit", "-q", "-m", "IDEA-R-1 beside IDEA-R-001")
    git(world.a, "push", "-q", "origin", "main")
    config_mod._theme_cache.clear()
    return dest.relative_to(world.a).as_posix()


@pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
def test_child_create_refuses_padded_parent(world, monkeypatch, push: bool) -> None:
    dup_rel = _padded_parent(world, monkeypatch)
    before, head, remote = _tree(world), git(world.a, "rev-parse", "HEAD"), world.remote_sha()
    argv = list(LIT.argv) if push else [a for a in LIT.argv if a != "--push"]

    result = invoke_world(world, monkeypatch, argv)
    out = flat(result)

    assert result.exit_code != 0, f"a create under IDEA-R-001/IDEA-R-1 went through:\n{out}"
    assert "Traceback" not in out, out
    for needle in (*PARENT_NEEDLES, LIT.parent_rel, dup_rel):
        assert _has(needle, out), f"{needle!r} not in output:\n{out}"
    after = _tree(world)
    assert after == before, f"a refused create wrote: {sorted(set(after) - set(before))}"
    assert after.get(ALLOC) == before.get(ALLOC)
    assert git(world.a, "rev-parse", "HEAD") == head
    assert world.remote_sha() == remote
    assert porcelain(world.a) == [], porcelain(world.a)


def test_service_sees_padded_parent(world, monkeypatch) -> None:
    _padded_parent(world, monkeypatch)
    assert len(_files_for(_service(world), "IDEA-R-001")) == 2
