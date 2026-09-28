# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #834 — a ``--push`` create is refused when origin spells a FOLDER on the new
item's path in another case.

Since #788 the path guard in ``create_item_and_push``'s ``build`` lists the new file's
folder on origin (``ls-tree --full-tree``) and refuses a file whose NAME equals the new
one ignoring case. It never asks whether origin has the folder itself under another
case. With ``Research/experiments/…`` on origin, a create into
``research/experiments/…`` commits a second top folder; on a case-insensitive
filesystem (macOS, the fleet's platform) every clone that pulls merges the two, and a
later ``commit -a`` from such a clone moves or duplicates items on origin.

Decided fix ([steer], bucket 1): the guard compares the new item's WHOLE path, folder
components included, against origin's tree, ignoring case. It refuses, naming the
existing folder, when origin has a different-case spelling of any folder on the path.
Nothing is created or pushed.

APFS cannot hold ``Research/`` and ``research/`` in one checkout, so origin's tree is
rewritten with git plumbing in the bare remote (a temporary index), never through a
working tree.

RED today:
- (1) top folder: origin has only ``Research/…``; the board (and its config) says
  ``research/``. Service API with an explicit id, with an auto-allocated id, and the
  CLI (``experiment create … --push``).
- (2) leaf folder: origin has ``research/Experiments/``.
- (3) a folder deeper in the path: a board under ``sub/`` with origin holding
  ``sub/Research/experiments/`` (a middle component), and ``Sub/research/…`` (a
  component above the board's own folder).

Controls (green before and after): the same folders spelled identically land; a
folder that is not a case twin (``research/Experiments2/``) lands.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import _ORIG_RUN, World, git, output_of
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_788_no_overwrite import EXPR_099, _board_with
from tests.issues.test_788_subdir_board import SUB, _sub_board
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

NEW_ID = "EXPR-042"  # nothing on origin holds it; "b" builds EXPR-042-b.md
NEW_NAME = f"{NEW_ID}-b.md"


# --- origin rewritten with plumbing (no working tree can hold both spellings) -------


def _plumb(world: World, *args: str, env: dict[str, str], stdin: str | None = None) -> str:
    result = _ORIG_RUN(
        ["git", *args], cwd=world.remote, capture_output=True, text=True,
        input=stdin, env=env, check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"
    return result.stdout


def _respell_on_origin(
    world: World, old: str, new: str, files: dict[str, str] | None = None
) -> None:
    """Commit on origin/main a tree where every path under folder `old` sits under `new`
    instead (same blobs), plus `files`. Origin then holds ONLY the `new` spelling."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(Path(tmp) / "index"),
            "GIT_WORK_TREE": tmp,  # never checked out; update-index wants one
            "GIT_AUTHOR_NAME": "Rival", "GIT_AUTHOR_EMAIL": "rival@example.com",
            "GIT_COMMITTER_NAME": "Rival", "GIT_COMMITTER_EMAIL": "rival@example.com",
        }
        # a fresh index from main's tree, each path respelled (a bare repo has no
        # work tree to remove from, so the index is built, not edited)
        listed = _plumb(world, "ls-tree", "-r", "-z", "--full-tree", "main", env=env)
        lines = []
        for entry in filter(None, listed.split("\0")):
            meta, path = entry.split("\t", 1)
            if path.startswith(old):
                path = new + path[len(old):]
            lines.append(f"{meta}\t{path}")
        _plumb(world, "update-index", "--index-info", env=env, stdin="\n".join(lines) + "\n")
        for rel, text in (files or {}).items():
            sha = _plumb(world, "hash-object", "-w", "--stdin", env=env, stdin=text).strip()
            _plumb(world, "update-index", "--add", "--cacheinfo", f"100644,{sha},{rel}", env=env)
        tree = _plumb(world, "write-tree", env=env).strip()
        commit = _plumb(
            world, "commit-tree", tree, "-p", "main", "-m", f"rival: {old} -> {new}", env=env
        ).strip()
        _plumb(world, "update-ref", "refs/heads/main", commit, env=env)


def _remote_dirs(world: World) -> set[str]:
    dirs: set[str] = set()
    for f in world.remote_files():
        parts = f.split("/")[:-1]
        dirs.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    return dirs


def _twin_dirs(dirs: set[str]) -> dict[str, list[str]]:
    folded: dict[str, list[str]] = {}
    for d in dirs:
        folded.setdefault(d.casefold(), []).append(d)
    return {k: sorted(v) for k, v in folded.items() if len(v) > 1}


def _top_board(world: World, old: str, new: str, extra: dict[str, str]) -> KanbanService:
    """The lower-case HDD board (config says research/…), then origin respelled."""
    svc = _board_with(world, {"NOTES.md": "rival\n"})
    _respell_on_origin(world, old, new, extra)
    git(world.a, "fetch", "-q", "origin")
    assert not _twin_dirs(_remote_dirs(world)), "the fixture itself holds twin folders"
    return svc


def _assert_refused_untouched(
    world: World, result: dict, folder: str, base: str, before: list[str]
) -> None:
    assert world.remote_sha() == base, (
        f"something was pushed; origin holds twin folders {_twin_dirs(_remote_dirs(world))}"
    )
    assert world.remote_files() == before
    assert result["success"] is False, result
    assert folder in result.get("message", ""), result


# --- (1) the top folder differs only in case ------------------------------------------

TOP_EXTRA = {"Research/experiments/EXPR-001-a.md": EXPR_099}


def test_top_folder_case_twin_refused_explicit_id(world) -> None:
    svc = _top_board(world, "research/", "Research/", TOP_EXTRA)
    assert svc._holder_at("origin/main", NEW_ID) is None  # not the id guard
    before, base = world.remote_files(), world.remote_sha()
    assert not any(f.startswith("research/") for f in before), before
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    _assert_refused_untouched(world, result, "Research/", base, before)


def test_top_folder_case_twin_refused_auto_allocated(world) -> None:
    svc = _top_board(world, "research/", "Research/", TOP_EXTRA)
    before, base = world.remote_files(), world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b")
    _assert_refused_untouched(world, result, "Research/", base, before)


def test_cli_top_folder_case_twin_refused(world, monkeypatch) -> None:
    _top_board(world, "research/", "Research/", TOP_EXTRA)
    before, base = world.remote_files(), world.remote_sha()
    result = invoke(world, monkeypatch, ["experiment", "create", "--title", "b", "--push"])
    out = " ".join(output_of(result).split())
    assert world.remote_sha() == base, (
        f"pushed; twin folders on origin {_twin_dirs(_remote_dirs(world))}\n{out}"
    )
    assert world.remote_files() == before, out
    assert result.exit_code != 0, out
    assert "Research/" in out, out


# --- (2) only the leaf folder differs --------------------------------------------------


def test_leaf_folder_case_twin_refused(world) -> None:
    svc = _top_board(
        world, "research/experiments/", "research/Experiments/",
        {"research/Experiments/EXPR-001-a.md": EXPR_099},
    )
    before, base = world.remote_files(), world.remote_sha()
    assert "research/Experiments" in _remote_dirs(world)
    assert "research/experiments" not in _remote_dirs(world)
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    _assert_refused_untouched(world, result, "Experiments/", base, before)


# --- (3) a folder deeper in the path (board under sub/) --------------------------------


@pytest.mark.parametrize(
    ("old", "new", "folder"),
    [
        (f"{SUB}/research/", f"{SUB}/Research/", "Research/"),  # a middle component
        (f"{SUB}/", "Sub/", "Sub/"),  # above the board's own folder
    ],
    ids=["middle", "above-board"],
)
def test_nested_folder_case_twin_refused(world, old, new, folder) -> None:
    svc = _sub_board(world, {"NOTES.md": "rival\n"})
    rival = new + f"{SUB}/research/experiments/EXPR-001-a.md"[len(old):]
    _respell_on_origin(world, old, new, {rival: EXPR_099})
    git(world.a, "fetch", "-q", "origin")
    assert not _twin_dirs(_remote_dirs(world)), "the fixture itself holds twin folders"
    before, base = world.remote_files(), world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    _assert_refused_untouched(world, result, folder, base, before)


# --- controls ------------------------------------------------------------------------


def test_control_same_spelling_lands(world) -> None:
    svc = _board_with(world, {"research/experiments/EXPR-001-a.md": EXPR_099})
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    assert result["success"] is True, result
    assert f"research/experiments/{NEW_NAME}" in world.remote_files()
    assert not _twin_dirs(_remote_dirs(world))


def test_control_nested_same_spelling_lands(world) -> None:
    svc = _sub_board(world, {f"{SUB}/research/experiments/EXPR-001-a.md": EXPR_099})
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    assert result["success"] is True, result
    assert f"{SUB}/research/experiments/{NEW_NAME}" in world.remote_files()


def test_control_other_folder_not_a_twin_lands(world) -> None:
    rel = "research/Experiments2/EXPR-001-a.md"  # other case, but not a twin of experiments
    svc = _board_with(world, {rel: EXPR_099})
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    assert result["success"] is True, result
    assert f"research/experiments/{NEW_NAME}" in world.remote_files()
    assert world.remote_show(rel) == EXPR_099
    assert not _twin_dirs(_remote_dirs(world))
