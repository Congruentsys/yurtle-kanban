# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #788/#792, round 4 — a board whose ``.kanban/`` sits in a SUBDIRECTORY.

Round-3 review of PR #810: ``_git_toplevel`` allows ``.kanban/`` below the git top
level, and ``_git_run`` runs git with ``cwd=self.repo_root`` (the board's folder). Two
calls on the push path don't match that layout:

1. ``_ids_at``'s ``git grep`` has no ``--full-name``, so its (path, id) pairs are
   relative to the board's folder, while ``names`` (``ls-tree --full-tree``) are
   repo-rooted. ``_holder_at`` now returns the ``_holders_at`` answer first, which is
   the wrong path, so a parent link to an ordinary paper is refused ("Could not read
   research/papers/PAPER-130-A-paper.md ... does not exist"); origin/main links it.
   The ``name in with_id`` skip never matches either, so the #788 rule is dead there.
2. The path guard's ``ls-tree`` has no ``--full-tree``: its repo-rooted pathspec is
   resolved against the board's folder, it lists nothing, and neither the exact-path
   nor the case-only guard fires. With the id check stubbed out (as the reviewer did),
   a create replaces EXP-043's file on origin.

Decided fix: ``git grep --full-name`` in ``_ids_at`` and a full-tree listing in the
guard. Then the subdirectory layout behaves exactly like the top-level one.

The board here lives under ``sub/`` (HDD, work dirs under ``sub/research/``); the
repo's top level keeps its own unrelated board, as the reviewer's probe had it.
"""

from __future__ import annotations

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_788_no_overwrite import EXP_043
from tests.issues.test_788_no_overwrite import _board_with as _top_board_with
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

SUB = "sub"
DIRS = ["research/experiments/", "research/papers/", "research/hypotheses/", "research/measures/"]

PAPER = f"{SUB}/research/papers/PAPER-130-A-paper.md"
PAPER_TEXT = (
    '---\nid: PAPER-130\ntitle: "A paper"\ntype: paper\nstatus: backlog\n---\n\n'
    "# PAPER-130\n\n```turtle\n@prefix paper: <https://nusy.dev/paper/> .\n"
    "<#PAPER-130> a paper:Paper .\n```\n"
)
# a lookalike named for PAPER-130 whose own id: is another item; it sorts before PAPER
LOOKALIKE = f"{SUB}/research/hypotheses/paper-130-foo.md"
LOOKALIKE_TEXT = (
    '---\nid: H130.9\ntitle: "Another hypothesis"\ntype: hypothesis\nstatus: backlog\n'
    "paper: PAPER-130\n---\n\n# Another hypothesis\n"
)

EXACT_REL = f"{SUB}/research/experiments/EXP-042-x.md"  # EXP-042 "x" builds this path
CASE_REL = f"{SUB}/research/experiments/exp-042-X.md"  # its case-only twin


def _sub_board(world: World, files: dict[str, str]) -> KanbanService:
    """An HDD board under `sub/` on origin holding `files`, fetched by A."""
    sub = world.a / SUB
    for d in DIRS:
        (sub / d).mkdir(parents=True, exist_ok=True)
        (sub / d / ".gitkeep").write_text("")
    KanbanConfig(
        theme="hdd", paths=PathConfig(root="research/", scan_paths=DIRS)
    ).save(sub / ".kanban" / "config.yaml")
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", "sub board")
    git(world.a, "push", "origin", "main")
    b_push(world, files)
    git(world.a, "fetch", "-q", "origin")
    config_mod._theme_cache.clear()
    svc = KanbanService(KanbanConfig.load(sub / ".kanban" / "config.yaml"), sub)
    svc.scan()
    # the layout under test: the board's folder is below the git top level
    assert svc._git_toplevel().resolve() == world.a.resolve()
    assert svc.repo_root.resolve() == sub.resolve()
    return svc


def _commits_since(world: World, base: str) -> list[str]:
    return git(world.remote, "rev-list", f"{base}..main").split()


def _changed_in(world: World, sha: str) -> set[str]:
    return set(git(world.remote, "diff-tree", "--no-commit-id", "-r", "--name-only", sha).split())


def _assert_refused_untouched(
    world: World, result: dict, rel: str, text: str, base: str, before: list[str]
) -> None:
    assert world.remote_show(rel) == text, f"origin's {rel} was replaced"
    assert world.remote_sha() == base, f"something was pushed; origin holds {world.remote_files()}"
    assert world.remote_files() == before
    assert result["success"] is False, result
    assert rel.rsplit("/", 1)[-1] in result.get("message", ""), result


# --- (1) a parent link with an ordinary paper, no lookalike --------------------------


def test_sub_holder_of_ordinary_paper_is_repo_rooted(world) -> None:
    svc = _sub_board(world, {PAPER: PAPER_TEXT})
    assert svc._holder_at("origin/main", "PAPER-130") == PAPER


def test_sub_parent_link_lands_in_paper_in_one_commit(world) -> None:
    svc = _sub_board(world, {PAPER: PAPER_TEXT})
    base = world.remote_sha()
    result = svc.create_item_and_push(
        WorkItemType.HYPOTHESIS, "h", item_id="H130.1", parent="PAPER-130"
    )
    assert result["success"] is True, result.get("message")
    assert result.get("parent_linked") is True, result
    commits = _commits_since(world, base)
    assert len(commits) == 1, commits
    changed = _changed_in(world, commits[0])
    assert PAPER in changed, changed
    assert any(p.startswith(f"{SUB}/research/hypotheses/H130.1") for p in changed), changed
    assert f"{SUB}/.kanban/_ID_ALLOCATIONS.json" in changed, changed
    assert "H130.1" in world.remote_show(PAPER)


# --- (2) the #788 rule: the id-bearing file beats a lookalike ------------------------


def test_sub_id_bearing_file_beats_lookalike(world) -> None:
    svc = _sub_board(world, {PAPER: PAPER_TEXT, LOOKALIKE: LOOKALIKE_TEXT})
    listed = world.remote_files()
    assert listed.index(LOOKALIKE) < listed.index(PAPER), listed  # not vacuous
    assert svc._holder_at("origin/main", "PAPER-130") == PAPER
    assert svc._holder_at("origin/main", "paper-130") == PAPER


def test_sub_lookalike_with_other_id_does_not_hold(world) -> None:
    svc = _sub_board(world, {LOOKALIKE: LOOKALIKE_TEXT})
    assert svc._holder_at("origin/main", "PAPER-130") is None
    assert svc._holder_at("origin/main", "H130.9") == LOOKALIKE


def test_sub_push_links_real_paper_not_lookalike(world) -> None:
    svc = _sub_board(world, {PAPER: PAPER_TEXT, LOOKALIKE: LOOKALIKE_TEXT})
    base = world.remote_sha()
    result = svc.create_item_and_push(
        WorkItemType.HYPOTHESIS, "h", item_id="H130.1", parent="PAPER-130"
    )
    assert result["success"] is True, result.get("message")
    assert len(_commits_since(world, base)) == 1
    assert "H130.1" in world.remote_show(PAPER)
    assert world.remote_show(LOOKALIKE) == LOOKALIKE_TEXT


# --- (3) exact-path collision --------------------------------------------------------


def test_sub_exact_collision_refused(world) -> None:
    svc = _sub_board(world, {EXACT_REL: EXP_043})
    # the id guard alone cannot save it: nothing on origin has id EXP-042 (#788)
    assert svc._holder_at("origin/main", "EXP-042") is None
    before, base = world.remote_files(), world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    _assert_refused_untouched(world, result, EXACT_REL, EXP_043, base, before)


def test_sub_exact_collision_refused_by_path_guard_alone(world, monkeypatch) -> None:
    svc = _sub_board(world, {EXACT_REL: EXP_043})
    monkeypatch.setattr(svc, "_holder_at", lambda rev, item_id: None)  # as the reviewer did
    before, base = world.remote_files(), world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    _assert_refused_untouched(world, result, EXACT_REL, EXP_043, base, before)


# --- (4) case-only collision ---------------------------------------------------------


def test_sub_case_collision_refused(world) -> None:
    svc = _sub_board(world, {CASE_REL: EXP_043})
    assert svc._holder_at("origin/main", "EXP-042") is None
    before, base = world.remote_files(), world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    _assert_refused_untouched(world, result, CASE_REL, EXP_043, base, before)


def test_sub_case_collision_refused_by_path_guard_alone(world, monkeypatch) -> None:
    svc = _sub_board(world, {CASE_REL: EXP_043})
    monkeypatch.setattr(svc, "_holder_at", lambda rev, item_id: None)  # as the reviewer did
    before, base = world.remote_files(), world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    _assert_refused_untouched(world, result, CASE_REL, EXP_043, base, before)


def test_sub_other_id_under_similar_name_lands(world) -> None:
    rel = f"{SUB}/research/experiments/exp-042-Y.md"
    svc = _sub_board(world, {rel: EXP_043})
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    assert result["success"] is True, result.get("message")
    assert EXACT_REL in world.remote_files()
    assert world.remote_show(rel) == EXP_043


# --- (5) _ids_at's paths are repo-rooted in both layouts -----------------------------


@pytest.mark.parametrize("layout", ["top", "sub"])
def test_ids_at_paths_are_repo_rooted(world, layout: str) -> None:
    prefix = "" if layout == "top" else f"{SUB}/"
    files = {
        f"{prefix}research/papers/PAPER-130-A-paper.md": PAPER_TEXT,
        f"{prefix}research/experiments/EXP-042-x.md": EXP_043,
    }
    svc = _top_board_with(world, files) if layout == "top" else _sub_board(world, files)
    names, ids = svc._ids_at("origin/main")
    remote = set(world.remote_files())
    assert dict(ids) == {
        f"{prefix}research/papers/PAPER-130-A-paper.md": "PAPER-130",
        f"{prefix}research/experiments/EXP-042-x.md": "EXP-043",
    }, ids
    for path, _ in ids:
        assert path in remote, f"{path} is not a repo-rooted path on origin"
        assert path in names, f"{path} (grep) does not match ls-tree's names {names}"
