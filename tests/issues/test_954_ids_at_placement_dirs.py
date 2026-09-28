"""Issue #954 — ``_ids_at`` must list the placement dirs too, not only the work paths.

On a single board the scan also walks every folder ``create`` writes into
(``_placement_dirs()``, #113), even when a type's folder lies outside the configured
scan paths. ``_ids_at(rev)`` — the id source for ``_holders_at``, ``_holder_at`` and
``_next_id_at`` (allocation and duplicate checks against origin) — listed only
``config.get_work_paths()``. So with the only scan path ``work/active/`` and
expeditions placed in ``work/expeditions/``, an item there is loaded locally but
invisible on origin: ``_holders_at("origin/main", "EXP-002", loaded=True)`` is ``[]``
and ``next-id EXP`` can hand out ``EXP-002`` again.

Layout (as ``tests/issues/test_919_board_loads_work_paths.py``): single board,
``.kanban/`` at the git top, ``paths.root: work/``, scan path ``work/active/`` only.
Clone A seeds ``work/active/EXP-001-active.md`` and pushes; clone B then pushes the
origin-only files, so A's own scan never sees them and only the rev-based readers
can:

- ``work/expeditions/EXP-002-placed.md`` — in a placement dir outside the scan path;
- ``work/expeditions/archive/EXP-004-old.md`` — the same, but ``paths.ignore``d;
- ``notes/EXP-009-n.md`` — outside every work path and placement dir.

Red on main: the placed item is not found on origin, and allocation against origin
returns ``EXP-002``. Controls (green on main and after the fix): ``notes/`` is never
counted or found; the ignored copy in the placement dir is no loaded holder.

Note: allocation deliberately counts ignored files ("the id space sees every file",
``_holders_at``'s docstring, #856), so the ignored control is pinned on
``_holders_at(loaded=True)`` only, not on ``next-id``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import item_text
from tests.issues.test_585_create_push_loop import _configure, git
from tests.issues.test_891_board_loads_pins import _env  # noqa: F401  (autouse)
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

CONFIG = """\
version: "1.0"
theme: nautical
paths:
  root: work/
  scan_paths:
    - work/active/
  ignore:
    - "**/archive/**"
"""

ACTIVE = "work/active/EXP-001-active.md"
PLACED = "work/expeditions/EXP-002-placed.md"
IGNORED = "work/expeditions/archive/EXP-004-old.md"
NOTES = "notes/EXP-009-n.md"


def _id_of(rel: str) -> str:
    return "-".join(Path(rel).stem.split("-")[:2])


def _write(clone: Path, rels: list[str]) -> None:
    for rel in rels:
        path = clone / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item_text("ready", item_id=_id_of(rel), title=Path(rel).stem))


class Board:
    """A bare remote, clone A (under test) holding the config and EXP-001, and
    clone B that pushes files A only ever sees on origin/main."""

    def __init__(self, tmp_path: Path) -> None:
        self.remote = tmp_path / "remote.git"
        self.a = tmp_path / "A"
        self.b = tmp_path / "B"
        git(tmp_path, "init", "--bare", "-b", "main", str(self.remote))
        git(tmp_path, "clone", str(self.remote), str(self.a))
        _configure(self.a)
        git(self.a, "checkout", "-B", "main")
        (self.a / ".kanban").mkdir()
        (self.a / ".kanban" / "config.yaml").write_text(CONFIG)
        _write(self.a, [ACTIVE])
        git(self.a, "add", "-A")
        git(self.a, "commit", "-m", "seed")
        git(self.a, "push", "-u", "origin", "main")
        git(tmp_path, "clone", str(self.remote), str(self.b))
        _configure(self.b)

    def push_from_b(self, rels: list[str]) -> None:
        """B commits `rels` and pushes; A fetches but does not merge them."""
        git(self.b, "fetch", "origin")
        git(self.b, "reset", "--hard", "origin/main")
        _write(self.b, rels)
        git(self.b, "add", "-A")
        git(self.b, "commit", "-m", "origin-only items")
        git(self.b, "push", "origin", "HEAD:refs/heads/main")
        git(self.a, "fetch", "origin")

    def service(self) -> KanbanService:
        config_mod._theme_cache.clear()
        return KanbanService(KanbanConfig.load(self.a / ".kanban" / "config.yaml"), self.a)


@pytest.fixture
def board(tmp_path: Path) -> Board:
    return Board(tmp_path)


def _preconditions(board: Board, on_origin: list[str]) -> KanbanService:
    """The layout is what the issue describes, and A's checkout holds none of the
    origin-only files (so only the rev-based readers can see them)."""
    svc = board.service()
    placed_dir = svc.repo_root / "work" / "expeditions"
    assert placed_dir in svc._placement_dirs()
    assert all(
        not placed_dir.is_relative_to(svc.repo_root / p) for p in svc.config.get_work_paths()
    ), "precondition: the placement dir lies outside every work path"
    tree = set(git(board.a, "ls-tree", "-r", "--name-only", "origin/main").split())
    assert set(on_origin) <= tree, "precondition: the files are on origin/main"
    for rel in on_origin:
        assert not (board.a / rel).exists(), f"precondition: {rel} is origin-only"
    assert [i.id for i in svc.scan()] == ["EXP-001"]
    return svc


def _loaded_by_scan(board: Board, rels: list[str]) -> set[str]:
    """Which of `rels` a scan of a checkout holding them all loads (A merged)."""
    git(board.a, "merge", "--ff-only", "origin/main")
    svc = board.service()
    got = {Path(i.file_path).resolve() for i in svc.scan()}
    out = {rel for rel in rels if (board.a / rel).resolve() in got}
    git(board.a, "reset", "--hard", "HEAD@{1}")
    return out


# --- 1. the placed item is found on origin --------------------------------------------


def test_placed_item_is_loaded_by_the_scan(board: Board) -> None:
    """Guard: once checked out, the scan loads the placed item (#113) and neither the
    ignored copy nor `notes/` — the loads the rev-based readers must match."""
    board.push_from_b([PLACED, IGNORED, NOTES])
    assert _loaded_by_scan(board, [ACTIVE, PLACED, IGNORED, NOTES]) == {ACTIVE, PLACED}


def test_holders_at_finds_item_in_placement_dir_on_origin(board: Board) -> None:
    """`_holders_at(origin/main, EXP-002, loaded=True)`, default ids, finds the item
    in the placement dir outside the scan path (#954)."""
    board.push_from_b([PLACED])
    svc = _preconditions(board, [PLACED])

    assert svc._holders_at("origin/main", "EXP-002", loaded=True) == [PLACED]
    assert svc._holders_at("origin/main", "EXP-002") == [PLACED]


def test_holder_at_finds_item_in_placement_dir_on_origin(board: Board) -> None:
    """The duplicate check `_holder_at` sees it too (#954)."""
    board.push_from_b([PLACED])
    svc = _preconditions(board, [PLACED])

    assert svc._holder_at("origin/main", "EXP-002") == PLACED


def test_ids_at_lists_placement_dir(board: Board) -> None:
    """`_ids_at(origin/main)` covers the placement dirs: the placed file's name and
    its frontmatter id (#954)."""
    board.push_from_b([PLACED])
    svc = _preconditions(board, [PLACED])

    names, ids = svc._ids_at("origin/main")

    assert ACTIVE in names
    assert PLACED in names
    assert (PLACED, "EXP-002") in ids


# --- 2. allocation against origin skips its number ------------------------------------


def test_next_id_at_origin_skips_placed_number(board: Board) -> None:
    """EXP-001 in the work path, EXP-002 only in the placement dir on origin: the
    next EXP id against origin is EXP-003 (#954)."""
    board.push_from_b([PLACED])
    svc = _preconditions(board, [PLACED])

    assert svc._next_id_at("origin/main", "EXP") == "EXP-003"


def test_next_id_cli_skips_placed_number(
    board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: `next-id EXP --json` in clone A allocates EXP-003, not the EXP-002
    that origin's placement dir already holds (#954)."""
    board.push_from_b([PLACED])
    _preconditions(board, [PLACED])
    monkeypatch.setenv("YURTLE_AGENT", "tester")
    monkeypatch.chdir(board.a)

    result = CliRunner().invoke(main, ["next-id", "EXP", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == "EXP-003", result.output


# --- 3. negative controls -------------------------------------------------------------


def test_control_file_outside_work_paths_and_placement_dirs_not_counted(
    board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`notes/EXP-009-n.md` lies outside every work path and placement dir: not
    listed, no holder, and allocation stays at EXP-002 (not EXP-010)."""
    board.push_from_b([NOTES])
    svc = _preconditions(board, [NOTES])

    names, ids = svc._ids_at("origin/main")
    assert NOTES not in names
    assert all(path != NOTES for path, _ in ids)
    assert svc._holders_at("origin/main", "EXP-009") == []
    assert svc._holder_at("origin/main", "EXP-009") is None
    assert svc._next_id_at("origin/main", "EXP") == "EXP-002"

    monkeypatch.setenv("YURTLE_AGENT", "tester")
    monkeypatch.chdir(board.a)
    result = CliRunner().invoke(main, ["next-id", "EXP", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == "EXP-002", result.output


def test_control_ignored_file_in_placement_dir_is_no_loaded_holder(board: Board) -> None:
    """`work/expeditions/archive/EXP-004-old.md` is `paths.ignore`d: the scan skips it,
    so `_holders_at(loaded=True)` does not return it once the placement dir is listed."""
    board.push_from_b([PLACED, IGNORED])
    svc = _preconditions(board, [PLACED, IGNORED])

    assert svc._holders_at("origin/main", "EXP-004", loaded=True) == []
