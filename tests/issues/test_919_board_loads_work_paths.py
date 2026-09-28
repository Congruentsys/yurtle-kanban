"""Issue #919 — ``_board_loads`` on a single board must agree with the scan outside the
work paths too, and a multi-board board at the git top itself is pinned.

``_board_loads(rel)`` says whether a scan would load the file at ``rel`` (relative to
the git work tree's top). On a single board it only checked ``paths.ignore``, so
``README.md`` at the top or ``proj/notes/EXP-009-n.md`` — never scanned — came back
True. [steer] on #919: on a single board it must also require ``rel`` under a work
path or a placement dir (``_placement_dirs``, which the scan always covers, #113), so
the docstring holds as written.

Layouts (builds on #891's pins, ``tests/issues/test_891_board_loads_pins.py``):

1. single board, ``.kanban/`` at the git top, scan path ``work/active/`` only, so the
   placement dirs (``work/expeditions/`` ...) lie outside it and are scanned on their
   own; files at the top, in ``notes/``, loose under ``work/`` and in a sibling that
   shares a placement dir's name as a prefix are not loaded;
2. the same with ``.kanban/`` in ``proj/`` (the paths the issue names);
3. multi-board with a board at the git top itself (``path: "."`` and ``path: ""``,
   ``.kanban/`` at the top) plus a nested board — the ``head in ('', '.')`` branch.

Every case compares ``_board_loads(rel)`` with whether a real scan of that layout
loads ``rel``: over the whole tracked tree, and one test per (layout, file).

Red on main: the single-board layouts' files outside every work path and placement
dir. Green-on-main pins: the multi-board board-at-the-top layouts (the mutation
``head not in ('', '.')`` -> ``head != ''`` turns them red) and every loaded file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_574_claim import item_text
from tests.issues.test_585_create_push_loop import _configure, git
from tests.issues.test_891_board_loads_pins import (  # noqa: F401  (_env: autouse)
    Layout,
    Repo,
    _env,
    _id_of,
    scanned,
    tracked_md,
)

# --- layouts -----------------------------------------------------------------------
#
# Paths are relative to the git top; each maps to whether a scan loads it. The pins
# compare `_board_loads` with the actual scan; `test_layout_exercises_what_it_claims`
# only guards that each layout covers what it says.

SINGLE_CONFIG = """\
version: "1.0"
theme: nautical
paths:
  root: work/
  scan_paths:
    - work/active/
  ignore:
    - "**/archive/**"
"""


def single_files(prefix: str) -> dict[str, bool]:
    p = f"{prefix}/" if prefix else ""
    return {
        # in the work path
        f"{p}work/active/EXP-001-active.md": True,
        # in a placement dir outside the work path: the scan covers it (#113)
        f"{p}work/expeditions/EXP-002-placed.md": True,
        # ignored, in the work path and in a placement dir
        f"{p}work/active/archive/EXP-003-old.md": False,
        f"{p}work/expeditions/archive/EXP-004-old.md": False,
        # under the board root, but neither a work path nor a placement dir
        f"{p}work/loose/EXP-005-loose.md": False,
        # a sibling whose name has a placement dir's as a prefix
        f"{p}work/expeditions-old/EXP-006-prefix.md": False,
        # outside every work path (the issue's examples)
        f"{p}notes/EXP-009-n.md": False,
        f"{p}EXP-007-repo-top.md": False,
        "README.md": False,
    }


def top_board_config(path: str) -> str:
    return f"""\
version: "2.0"
boards:
  - name: dev
    preset: nautical
    path: "{path}"
    ignore:
      - "**/archive/**"
      - "parked/**"
      - "lab/**"
  - name: lab
    preset: nautical
    path: lab/
    ignore:
      - "**/archive/**"
default_board: dev
"""


# `.kanban/` at the git top; board `dev` is the top itself.
TOP_BOARD_FILES = {
    "EXP-001-top.md": True,
    "expeditions/EXP-002-exp.md": True,
    "notes/EXP-003-notes.md": True,  # anywhere under the top board's root
    "README.md": True,  # likewise: an item file at the top is on the top board
    "parked/EXP-004-parked.md": False,
    "expeditions/archive/EXP-005-old.md": False,
    # `parked/**` is relative to the repo root: a nested `parked/` is loaded
    "expeditions/parked/EXP-006-deep.md": True,
    # ignored by `dev`, loaded by `lab`
    "lab/EXP-007-lab.md": True,
    # ignored by both
    "lab/archive/EXP-008-old.md": False,
}

LAYOUTS = [
    Layout(
        "single-board-kanban-at-top",
        "",
        SINGLE_CONFIG,
        single_files(""),
        ignored="work/loose/EXP-005-loose.md",
        loaded="work/expeditions/EXP-002-placed.md",
    ),
    Layout(
        "single-board-kanban-in-subdir",
        "proj",
        SINGLE_CONFIG,
        single_files("proj"),
        ignored="proj/notes/EXP-009-n.md",
        loaded="proj/work/expeditions/EXP-002-placed.md",
    ),
    Layout(
        "multi-board-top-board-dot",
        "",
        top_board_config("."),
        TOP_BOARD_FILES,
        ignored="parked/EXP-004-parked.md",
        loaded="expeditions/EXP-002-exp.md",
    ),
    Layout(
        "multi-board-top-board-empty",
        "",
        top_board_config(""),
        TOP_BOARD_FILES,
        ignored="parked/EXP-004-parked.md",
        loaded="expeditions/EXP-002-exp.md",
    ),
]
LAYOUT_IDS = [layout.name for layout in LAYOUTS]


def _item_id(rel: str) -> str:
    return "EXP-090" if Path(rel).name == "README.md" else _id_of(rel)


def build(tmp_path: Path, layout: Layout) -> Repo:
    """A bare remote and clone A holding `layout`'s config and files, pushed. Every
    file, `README.md` too, is a well-formed item, so only placement and ignore decide
    whether the scan loads it."""
    remote = tmp_path / "remote.git"
    top = tmp_path / "A"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", str(remote), str(top))
    _configure(top)
    git(top, "checkout", "-B", "main")
    repo = Repo(remote, top, layout)
    (repo.repo_root / ".kanban").mkdir(parents=True)
    (repo.repo_root / ".kanban" / "config.yaml").write_text(layout.config)
    for rel in layout.files:
        path = top / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item_text("ready", item_id=_item_id(rel), title=Path(rel).stem))
    git(top, "add", "-A")
    git(top, "commit", "-m", "seed")
    git(top, "push", "-u", "origin", "main")
    git(top, "fetch", "origin")
    return repo


@pytest.fixture(params=LAYOUTS, ids=LAYOUT_IDS)
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> Repo:
    return build(tmp_path, request.param)


# --- guards ------------------------------------------------------------------------


def test_layout_exercises_what_it_claims(repo: Repo) -> None:
    """The scan loads exactly the files the layout says, and the single-board layouts
    really have a placement dir outside their work path."""
    expected = {rel for rel, loads in repo.layout.files.items() if loads}
    assert scanned(repo) == expected
    svc = repo.service()
    assert svc.config.is_multi_board == repo.layout.multi
    if not repo.layout.multi:
        placed = svc.repo_root / "work" / "expeditions"
        assert placed in svc._placement_dirs()
        assert all(
            not placed.is_relative_to(svc.repo_root / p) for p in svc.config.get_work_paths()
        ), "precondition: the placement dir lies outside every work path"


# --- _board_loads agrees with the scan ------------------------------------------------


def test_board_loads_agrees_with_scan_over_whole_tree(repo: Repo) -> None:
    """Single board and multi-board alike: for every tracked `.md`, `_board_loads`
    is what the scan does, outside every work path too (#919)."""
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


CASES = [
    pytest.param(layout, rel, id=f"{layout.name}:{rel}")
    for layout in LAYOUTS
    for rel in layout.files
]


@pytest.mark.parametrize(("layout", "rel"), CASES)
def test_board_loads_file_agrees_with_scan(layout: Layout, rel: str, tmp_path: Path) -> None:
    """One (layout, file) at a time: `_board_loads(rel) == (the scan loads rel)`."""
    repo = build(tmp_path, layout)
    loads = rel in scanned(repo)
    assert loads == layout.files[rel], "precondition: the layout's expectation"

    got = repo.service()._board_loads(rel)

    assert got == loads, (
        f"_board_loads({rel!r}) is {got} but the scan "
        f"{'loads' if loads else 'does not load'} it ({layout.name})"
    )


def test_holders_at_loaded_follows_the_scan(repo: Repo) -> None:
    """Through `_holders_at(loaded=True)`, given each file as its id source (so the
    filter is `_board_loads` alone, not `_ids_at`'s work-path listing): the unloaded
    item has no holder, the loaded one has its file."""
    svc = repo.service()
    for rel, want in ((repo.layout.ignored, []), (repo.layout.loaded, [repo.layout.loaded])):
        ids = [(rel, _item_id(rel))]
        got = svc._holders_at("origin/main", _item_id(rel), ids, loaded=True)
        assert got == want, f"{rel} ({repo.layout.name})"
