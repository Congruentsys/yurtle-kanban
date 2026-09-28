"""Issue #963 (2) — on a single board, ``_board_loads`` must filter the placement dirs
the way ``_scan`` does: a placement dir outside ``repo_root`` is not scanned
(``type_dir.is_relative_to(self.repo_root)``, #113), so a file there is not loaded.

Layout: ``.kanban/`` in ``proj/``, an absolute ``paths.root`` of ``<top>/shared/`` and
one absolute scan path ``<top>/shared/active/`` under it, so the board root is
``shared/`` and the placement dirs (``shared/expeditions/`` ...) lie outside
``repo_root`` (``proj/``) but inside the git top. The scan loads ``shared/active/`` (a
work path) and skips ``shared/expeditions/``; ``_board_loads`` accepted the latter.

Control: the same absolute spelling with the root inside ``repo_root``
(``<top>/proj/work/``, scanning ``<top>/proj/work/active/``): its placement dirs are
scanned, and ``_board_loads`` must keep saying True for them.

Red on main: ``shared/expeditions/EXP-002-placed.md`` in the outside layout. The
control is green on main.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_891_board_loads_pins import (  # noqa: F401  (_env: autouse)
    Layout,
    Repo,
    _env,
    _id_of,
    build,
    scanned,
    tracked_md,
)


def single_config(root: Path, scan: Path) -> str:
    return f"""\
version: "1.0"
theme: nautical
paths:
  root: "{root.as_posix()}/"
  scan_paths:
    - "{scan.as_posix()}/"
  ignore:
    - "**/archive/**"
"""


# Paths relative to the git top; each maps to whether a scan loads it.
OUTSIDE_FILES = {
    # the work path, outside repo_root: scanned, it is configured
    "shared/active/EXP-001-active.md": True,
    # a placement dir outside repo_root: never scanned (#113's filter)
    "shared/expeditions/EXP-002-placed.md": False,
    "shared/active/archive/EXP-003-old.md": False,
    # under repo_root but no work path or placement dir
    "proj/EXP-004-proj.md": False,
}

INSIDE_FILES = {
    "proj/work/active/EXP-001-active.md": True,
    # a placement dir inside repo_root: the scan covers it (#113)
    "proj/work/expeditions/EXP-002-placed.md": True,
    "proj/work/active/archive/EXP-003-old.md": False,
    "proj/work/loose/EXP-004-loose.md": False,
}

PLACED = {
    "outside": "shared/expeditions/EXP-002-placed.md",
    "inside": "proj/work/expeditions/EXP-002-placed.md",
}


def layout_for(kind: str, top: Path) -> Layout:
    if kind == "outside":
        root = top / "shared"
        files = OUTSIDE_FILES
        ignored, loaded = PLACED["outside"], "shared/active/EXP-001-active.md"
    else:
        root = top / "proj" / "work"
        files = INSIDE_FILES
        ignored, loaded = "proj/work/loose/EXP-004-loose.md", PLACED["inside"]
    return Layout(
        f"single-board-placement-{kind}-repo-root",
        "proj",
        single_config(root, root / "active"),
        files,
        ignored=ignored,
        loaded=loaded,
    )


@pytest.fixture(params=["outside", "inside"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> Repo:
    # test_891's `build` puts clone A's work tree (the git top) at tmp_path / "A"
    return build(tmp_path, layout_for(request.param, tmp_path / "A"))


def _placement_dir(repo: Repo) -> Path:
    return (repo.top / PLACED["outside" if "outside" in repo.layout.name else "inside"]).parent


# --- guard -------------------------------------------------------------------------


def test_layout_exercises_what_it_claims(repo: Repo) -> None:
    """The scan loads exactly the layout's files, and the placement dir lies outside
    (or, for the control, inside) repo_root, inside the git top, outside every work
    path."""
    expected = {rel for rel, loads in repo.layout.files.items() if loads}
    assert scanned(repo) == expected
    svc = repo.service()
    assert not svc.config.is_multi_board
    placed = _placement_dir(repo)
    assert placed in svc._placement_dirs()
    assert placed.is_relative_to(svc._git_toplevel())
    outside = "outside" in repo.layout.name
    assert placed.is_relative_to(svc.repo_root) is not outside
    assert all(not placed.is_relative_to(svc.repo_root / p) for p in svc.config.get_work_paths()), (
        "precondition: the placement dir lies outside every work path"
    )


# --- _board_loads agrees with the scan ----------------------------------------------


def test_board_loads_of_the_placed_file_agrees_with_scan(repo: Repo) -> None:
    """The issue's case: a file in a placement dir outside repo_root is not loaded by
    the scan, so `_board_loads` is False for it; inside repo_root it is True."""
    rel = PLACED["outside" if "outside" in repo.layout.name else "inside"]
    loads = rel in scanned(repo)
    assert loads == repo.layout.files[rel], "precondition: the layout's expectation"

    got = repo.service()._board_loads(rel)

    assert got == loads, (
        f"_board_loads({rel!r}) is {got} but the scan "
        f"{'loads' if loads else 'does not load'} it ({repo.layout.name})"
    )


def test_board_loads_agrees_with_scan_over_whole_tree(repo: Repo) -> None:
    svc = repo.service()
    loads = scanned(repo)
    tree = tracked_md(repo)
    assert tree == set(repo.layout.files)

    got = {rel: svc._board_loads(rel) for rel in sorted(tree)}

    wrong = {rel: v for rel, v in got.items() if v != (rel in loads)}
    assert not wrong, (
        f"_board_loads disagrees with the scan ({repo.layout.name}): {wrong}; "
        f"scan loads {sorted(loads)}"
    )


def test_holders_at_loaded_follows_the_scan(repo: Repo) -> None:
    """Through `_holders_at(loaded=True)`, each file given as its own id source (so
    the filter is `_board_loads` alone): the unloaded item has no holder, the loaded
    one has its file."""
    svc = repo.service()
    for rel, want in ((repo.layout.ignored, []), (repo.layout.loaded, [repo.layout.loaded])):
        got = svc._holders_at("origin/main", _id_of(rel), [(rel, _id_of(rel))], loaded=True)
        assert got == want, f"{rel} ({repo.layout.name})"
