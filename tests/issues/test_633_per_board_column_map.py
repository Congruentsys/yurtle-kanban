"""Issue #633 — multi-board: `_get_column_status_map` merges every board's
`status_mappings` (plus a hard-coded nautical/spec/hdd name table) into ONE
column -> status map, so board B's alias can map a column id on board A.

Decided ([steer] on #633, bucket 2): #587's rule ("the item's own theme only")
applies at scan time too. The column -> status map is built PER BOARD: the six
canonical identity names plus that board's own theme `status_mappings` (as
cleaned by #613/#615). An item's column resolves through the map of the board
that holds it. The hard-coded cross-theme table is deleted. In a single-board
repo the behaviour is unchanged for that board's theme.

Covered here:

(a) Two custom-theme boards whose aliases collide (`review_q` is review on
    `alpha`, blocked on `beta`; `parked` is backlog on `alpha`, done on `beta`).
    An item on each board with the colliding status resolves by its own board's
    theme in `list --json`, `show --json` and board placement (the column the
    `board` view draws it in). Both board orders are run: a merged map is wrong
    for whichever board is listed first.
(b) Cross-theme leakage. A software item whose status is another theme's
    native name (`arrived`, ...) is read exactly like any other unknown status
    on a software board (today: a name no theme knows, e.g. `zzz_bogus`, reads
    as backlog); a nautical item's `arrived` reads as done. Also a custom
    board's column named like another theme's native name, but not in its own
    `status_mappings`, is not given that other theme's meaning.
(c) Controls: a single-board repo per shipped theme; every native and
    canonical name reads back as today (show --json) and every column of the
    theme draws the items of its status.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"

CANONICAL = ("backlog", "ready", "in_progress", "review", "done", "blocked")

# a status name no theme (and no hard-coded table) knows
UNKNOWN = "zzz_bogus"

# --- custom themes for (a) ---------------------------------------------------------

ALPHA_THEME: dict[str, Any] = {
    "theme": {"name": "alpha"},
    "item_types": {"task": {"id_prefix": "ATASK", "path": "alpha/tasks/"}},
    "columns": {
        "parked": {"name": "Parked", "order": 1},
        "doing": {"name": "Doing", "order": 2},
        "review_q": {"name": "Review Queue", "order": 3},
        "shipped": {"name": "Shipped", "order": 4},
        "blocked": {"name": "Blocked", "order": 5},
    },
    "status_mappings": {
        "parked": "backlog",
        "doing": "in_progress",
        "review_q": "review",
        "shipped": "done",
    },
}

BETA_THEME: dict[str, Any] = {
    "theme": {"name": "beta"},
    "item_types": {"task": {"id_prefix": "BTASK", "path": "beta/tasks/"}},
    "columns": {
        "todo": {"name": "Todo", "order": 1},
        "doing": {"name": "Doing", "order": 2},
        "review": {"name": "Review", "order": 3},
        "parked": {"name": "Parked (done)", "order": 4},
        "review_q": {"name": "Stuck", "order": 5},
    },
    "status_mappings": {
        "todo": "backlog",
        "doing": "in_progress",
        "parked": "done",  # alpha's `parked` is backlog
        "review_q": "blocked",  # alpha's `review_q` is review
    },
}

# colliding name -> {board: canonical it means there}
COLLISIONS: dict[str, dict[str, str]] = {
    "review_q": {"alpha": "review", "beta": "blocked"},
    "parked": {"alpha": "backlog", "beta": "done"},
}

BOARD_ORDERS = {"alpha-first": ("alpha", "beta"), "beta-first": ("beta", "alpha")}


# --- fixtures / helpers ------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git_init(root: Path) -> None:
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t.com"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)


def _write_item(root: Path, rel_dir: str, item_id: str, status: str) -> None:
    folder = root / rel_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item_id}-item.md").write_text(
        f"---\nid: {item_id}\ntitle: item {item_id}\ntype: task\nstatus: {status}\n---\n\n"
        f"# item {item_id}\n"
    )


def _multi_repo(
    root: Path,
    boards: list[tuple[str, str, str]],
    custom_themes: dict[str, dict[str, Any]] | None = None,
) -> Path:
    """boards: (name, preset, path) in config order."""
    root.mkdir(parents=True, exist_ok=True)
    kanban = root / ".kanban"
    (kanban / "themes").mkdir(parents=True)
    for name, theme in (custom_themes or {}).items():
        (kanban / "themes" / f"{name}.yaml").write_text(yaml.safe_dump(theme, sort_keys=False))
    lines = ['version: "2.0"', "boards:"]
    for name, preset, path in boards:
        lines += [f"  - name: {name}", f"    preset: {preset}", f"    path: {path}"]
        (root / path).mkdir(parents=True, exist_ok=True)
    (kanban / "config.yaml").write_text("\n".join(lines) + "\n")
    _git_init(root)
    return root


def _single_repo(root: Path, theme: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    kanban = root / ".kanban"
    kanban.mkdir(parents=True)
    (kanban / "config.yaml").write_text(
        f"kanban:\n  theme: {theme}\n  paths:\n    root: work/\n"
    )
    (root / "work").mkdir()
    _git_init(root)
    return root


def _cli_json(repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]) -> Any:
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    result = CliRunner().invoke(main, args)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`{' '.join(args)}` crashed: {result.exception!r}")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _show_status(repo: Path, monkeypatch: pytest.MonkeyPatch, item_id: str) -> str:
    return str(_cli_json(repo, monkeypatch, ["show", item_id, "--json"])["status"])


def _list_statuses(repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    data = _cli_json(repo, monkeypatch, ["list", "--json"])
    return {str(d["id"]): str(d["status"]) for d in data}


def _service(repo: Path, monkeypatch: pytest.MonkeyPatch) -> KanbanService:
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _placed_in(service: KanbanService, board_name: str | None) -> dict[str, list[str]]:
    """column id -> ids of the items the board view draws there."""
    board = service.get_board(board_name=board_name)
    return {
        col.id: sorted(i.id for i in board.get_column_items(col.id)) for col in board.columns
    }


def _column_status(service: KanbanService, board_name: str | None) -> dict[str, str | None]:
    board = service.get_board(board_name=board_name)
    out: dict[str, str | None] = {}
    for col in board.columns:
        status = board.column_status(col.id)
        out[col.id] = status.value if status is not None else None
    return out


def _collision_repo(root: Path, order: tuple[str, str]) -> Path:
    """alpha + beta custom boards in `order`, one item per (board, colliding name),
    id `<BOARD>-<NAME>` upper-cased."""
    repo = _multi_repo(
        root,
        [(b, f"{b}theme", f"{b}/") for b in order],
        {"alphatheme": ALPHA_THEME, "betatheme": BETA_THEME},
    )
    for name in COLLISIONS:
        for b in ("alpha", "beta"):
            _write_item(repo, f"{b}/tasks", _cid(b, name), name)
    return repo


def _cid(board: str, name: str) -> str:
    return f"{board[0].upper()}-{name.upper().replace('_', '')}"


# ---------------------------------------------------------------------------
# 0. Non-vacuity: the fixtures really collide / the theme files are as assumed
# ---------------------------------------------------------------------------


def test_fixture_themes_collide() -> None:
    for name, meaning in COLLISIONS.items():
        assert ALPHA_THEME["status_mappings"][name] == meaning["alpha"]
        assert BETA_THEME["status_mappings"][name] == meaning["beta"]
        assert meaning["alpha"] != meaning["beta"]
        assert name in ALPHA_THEME["columns"] and name in BETA_THEME["columns"]


def test_software_theme_has_no_status_mappings() -> None:
    software = yaml.safe_load((THEMES_DIR / "software.yaml").read_text())
    assert not software.get("status_mappings")
    nautical = yaml.safe_load((THEMES_DIR / "nautical.yaml").read_text())
    assert nautical["status_mappings"]["arrived"] == "done"


# ---------------------------------------------------------------------------
# (a) colliding aliases resolve by the item's own board
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("order", list(BOARD_ORDERS), ids=list(BOARD_ORDERS))
def test_collision_show_json_per_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: str
) -> None:
    repo = _collision_repo(tmp_path / "repo", BOARD_ORDERS[order])
    got = {
        (b, n): _show_status(repo, monkeypatch, _cid(b, n))
        for n in COLLISIONS for b in ("alpha", "beta")
    }
    want = {(b, n): COLLISIONS[n][b] for n in COLLISIONS for b in ("alpha", "beta")}
    assert got == want


@pytest.mark.parametrize("order", list(BOARD_ORDERS), ids=list(BOARD_ORDERS))
def test_collision_list_json_per_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: str
) -> None:
    repo = _collision_repo(tmp_path / "repo", BOARD_ORDERS[order])
    got = _list_statuses(repo, monkeypatch)
    want = {_cid(b, n): COLLISIONS[n][b] for n in COLLISIONS for b in ("alpha", "beta")}
    assert got == want


@pytest.mark.parametrize("board", ["alpha", "beta"])
@pytest.mark.parametrize("order", list(BOARD_ORDERS), ids=list(BOARD_ORDERS))
def test_collision_column_status_per_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: str, board: str
) -> None:
    """Each colliding column id maps through its own board's theme only."""
    repo = _collision_repo(tmp_path / "repo", BOARD_ORDERS[order])
    cols = _column_status(_service(repo, monkeypatch), board)
    assert {n: cols.get(n) for n in COLLISIONS} == {n: COLLISIONS[n][board] for n in COLLISIONS}


@pytest.mark.parametrize("board", ["alpha", "beta"])
@pytest.mark.parametrize("order", list(BOARD_ORDERS), ids=list(BOARD_ORDERS))
def test_collision_board_placement_per_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: str, board: str
) -> None:
    """The `board` view draws each item in the column its own theme names for its
    status: on alpha, A-REVIEWQ under `review_q` and A-PARKED under `parked`; on
    beta, B-REVIEWQ under `review_q` (stuck) and B-PARKED under `parked` (done)."""
    repo = _collision_repo(tmp_path / "repo", BOARD_ORDERS[order])
    placed = _placed_in(_service(repo, monkeypatch), board)
    assert {n: placed.get(n) for n in COLLISIONS} == {
        n: [_cid(board, n)] for n in COLLISIONS
    }


# ---------------------------------------------------------------------------
# (b) cross-theme leakage
# ---------------------------------------------------------------------------

# other themes' native names that a software item must not borrow (the old
# hard-coded tables carry all of these)
FOREIGN_ON_SOFTWARE = [
    "arrived", "provisioning", "underway", "approaching", "accepted", "active", "complete",
    "abandoned",
]


def _software_unknown_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """What a single software board reads for a status name nobody knows."""
    repo = _single_repo(tmp_path / "baseline", "software")
    _write_item(repo, "work", "S-UNKNOWN", UNKNOWN)
    return _show_status(repo, monkeypatch, "S-UNKNOWN")


def test_unknown_status_baseline_is_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins today's unknown-status behaviour that (b) compares against."""
    assert _software_unknown_baseline(tmp_path, monkeypatch) == "backlog"


@pytest.mark.parametrize("name", FOREIGN_ON_SOFTWARE)
def test_single_software_foreign_status_reads_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    baseline = _software_unknown_baseline(tmp_path, monkeypatch)
    repo = _single_repo(tmp_path / "repo", "software")
    _write_item(repo, "work", "S-1", name)
    assert _show_status(repo, monkeypatch, "S-1") == baseline, (
        f"software item `status: {name}` borrowed another theme's meaning"
    )


@pytest.mark.parametrize("name", FOREIGN_ON_SOFTWARE)
def test_multi_software_foreign_status_reads_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """software + nautical boards: the software item's `arrived` is unknown on its
    own board (list, show, placement), the nautical item's `arrived` is done."""
    baseline = _software_unknown_baseline(tmp_path, monkeypatch)
    repo = _multi_repo(
        tmp_path / "repo",
        [("dev", "software", "dev/"), ("ship", "nautical", "ship/")],
    )
    _write_item(repo, "dev/tasks", "S-1", name)
    assert _show_status(repo, monkeypatch, "S-1") == baseline
    assert _list_statuses(repo, monkeypatch)["S-1"] == baseline
    placed = _placed_in(_service(repo, monkeypatch), "dev")
    assert "S-1" in placed.get(baseline, []), placed


def test_multi_nautical_arrived_reads_as_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _multi_repo(
        tmp_path / "repo",
        [("dev", "software", "dev/"), ("ship", "nautical", "ship/")],
    )
    _write_item(repo, "ship/tasks", "N-1", "arrived")
    assert _show_status(repo, monkeypatch, "N-1") == "done"
    assert _list_statuses(repo, monkeypatch)["N-1"] == "done"
    placed = _placed_in(_service(repo, monkeypatch), "ship")
    assert placed.get("done") == ["N-1"], placed


# a custom theme with columns named like other themes' native names, none of them in
# its own `status_mappings`
PLAIN_THEME: dict[str, Any] = {
    "theme": {"name": "plain"},
    "item_types": {"task": {"id_prefix": "PTASK", "path": "plain/tasks/"}},
    "columns": {
        "backlog": {"name": "Backlog", "order": 1},
        "arrived": {"name": "Arrived", "order": 2},
        "complete": {"name": "Complete", "order": 3},
        "draft": {"name": "Draft", "order": 4},
        "done": {"name": "Done", "order": 5},
    },
    "status_mappings": {"inbox": "backlog"},
}


@pytest.mark.parametrize("column", ["arrived", "complete", "draft"])
def test_custom_column_does_not_borrow_other_theme_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, column: str
) -> None:
    """A column id the board's own theme doesn't map is not given the meaning
    another theme (or the deleted hard-coded table) gives it: it draws nothing."""
    repo = _multi_repo(
        tmp_path / "repo",
        [("plain", "plaintheme", "plain/"), ("ship", "nautical", "ship/"),
         ("research", "hdd", "research/")],
        {"plaintheme": PLAIN_THEME},
    )
    _write_item(repo, "plain/tasks", "P-DONE", "done")
    _write_item(repo, "plain/tasks", "P-BACKLOG", "backlog")
    service = _service(repo, monkeypatch)
    assert _column_status(service, "plain").get(column) is None
    assert _placed_in(service, "plain").get(column) == []


# ---------------------------------------------------------------------------
# (c) controls: a single board per shipped theme reads back as today
# ---------------------------------------------------------------------------

SHIPPED = ("software", "nautical", "spec", "hdd")


def _theme_file(theme: str) -> dict[str, Any]:
    return yaml.safe_load((THEMES_DIR / f"{theme}.yaml").read_text())


def _native_names(theme: str) -> dict[str, str]:
    """Every name a `theme` item may carry -> canonical: the six canonical names plus
    the theme's `status_mappings`."""
    names = {c: c for c in CANONICAL}
    for native, canonical in (_theme_file(theme).get("status_mappings") or {}).items():
        names[str(native)] = str(canonical)
    return names


def _expected_columns(theme: str) -> dict[str, str | None]:
    names = _native_names(theme)
    return {str(col): names.get(str(col)) for col in _theme_file(theme)["columns"]}


CONTROL_NAMES = [(t, n, c) for t in SHIPPED for n, c in sorted(_native_names(t).items())]


@pytest.mark.parametrize(
    ("theme", "name", "canonical"), CONTROL_NAMES, ids=[f"{t}-{n}" for t, n, _ in CONTROL_NAMES]
)
def test_control_single_board_native_name_reads_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, theme: str, name: str, canonical: str
) -> None:
    repo = _single_repo(tmp_path / "repo", theme)
    _write_item(repo, "work", "C-1", name)
    assert _show_status(repo, monkeypatch, "C-1") == canonical
    assert _list_statuses(repo, monkeypatch) == {"C-1": canonical}


@pytest.mark.parametrize("theme", SHIPPED)
def test_control_single_board_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, theme: str
) -> None:
    """Every column of the theme maps to the status its own name means, and draws
    exactly the item written with that column's name."""
    expected = _expected_columns(theme)
    assert all(v is not None for v in expected.values()), expected
    repo = _single_repo(tmp_path / "repo", theme)
    for i, col in enumerate(expected):
        _write_item(repo, "work", f"C-{i}", col)
    service = _service(repo, monkeypatch)
    assert _column_status(service, None) == expected
    placed = _placed_in(service, None)
    for i, col in enumerate(expected):
        assert f"C-{i}" in placed[col], (col, placed)
