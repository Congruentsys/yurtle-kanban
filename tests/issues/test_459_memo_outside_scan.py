"""Issue #459 — follow-ups from the review of PR #456 (#454).

Decided behaviour:
1. The status-name memo is used only DURING a scan. Outside one, a theme status name
   is read from the current theme: in one long-lived `KanbanService` (MCP), a status
   name a theme gained after the last `scan()` is recognised by a single-file read with
   no rescan (single board: `_parse_file`; multi-board: the public per-board reads
   `get_board(name)` / `get_items(board=name)`, which parse outside `scan()`). A
   second rescan agrees.
2. A nested `scan()` doesn't end the outer scan's memo early: `_scanning` is saved and
   restored (white-box — no public path nests a scan today).
3. Controls: the scan still loads the theme a bounded number of times; the #454 and
   #448 tests stay green (run alongside).
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

THEME = "mytheme"

OLD_MAPPINGS = {"todo": "backlog"}
NEW_MAPPINGS = {"todo": "backlog", "doing": "in_progress"}

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = f'version: "2.0"\nboards:\n  - name: devboard\n    preset: {THEME}\n    path: work/\n'


def _theme(mappings: dict[str, str]) -> dict[str, Any]:
    return {
        "theme": {"name": "acme", "description": "custom"},
        "item_types": {
            "task": {"id_prefix": "TSK", "path": "work/tasks/"},
            "bug": {"id_prefix": "BUG", "path": "work/bugs/"},
        },
        "columns": {
            "backlog": {"name": "Backlog", "order": 1},
            "in_progress": {"name": "In Progress", "order": 2},
            "done": {"name": "Done", "order": 3},
        },
        "status_mappings": dict(mappings),
        "transitions": {"backlog": ["in_progress"], "in_progress": ["backlog", "done"]},
    }


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _write_theme(repo: Path, mappings: dict[str, str]) -> None:
    themes = repo / ".kanban" / "themes"
    themes.mkdir(parents=True, exist_ok=True)
    (themes / f"{THEME}.yaml").write_text(yaml.safe_dump(_theme(mappings), sort_keys=False))
    config_mod._theme_cache.clear()


def _item(repo: Path, item_id: str, status: str) -> Path:
    path = repo / "work" / "tasks" / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntype: task\nstatus: {status}\n"
        f"title: Item {item_id}\n---\n\n# Item {item_id}\n"
    )
    return path


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str) -> Path:
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(cfg)
    _write_theme(tmp_path, OLD_MAPPINGS)
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
    _item(tmp_path, "TSK-001", "todo")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def single(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _repo(tmp_path, monkeypatch, SINGLE_CFG)


@pytest.fixture
def multi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _repo(tmp_path, monkeypatch, MULTI_CFG)


def _scanned(repo: Path) -> KanbanService:
    """A long-lived service that has scanned the repo under the OLD theme, then the
    theme gains `doing` and a `status: doing` item appears — no rescan."""
    service = cli.get_service()
    items = {i.id: i for i in service.scan()}
    assert items["TSK-001"].status == WorkItemStatus.BACKLOG, items
    _write_theme(repo, NEW_MAPPINGS)
    _item(repo, "TSK-002", "doing")
    return service


# ---------------------------------------------------------------------------
# Controls: a fresh service reads `doing` as IN_PROGRESS (green today)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["single", "multi"])
def test_fresh_service_reads_new_name(fixture: str, request: pytest.FixtureRequest) -> None:
    repo: Path = request.getfixturevalue(fixture)
    _write_theme(repo, NEW_MAPPINGS)
    _item(repo, "TSK-002", "doing")
    items = {i.id: i for i in cli.get_service().scan()}
    assert items["TSK-002"].status == WorkItemStatus.IN_PROGRESS, items


@pytest.mark.parametrize("fixture", ["single", "multi"])
def test_rescan_reads_new_name(fixture: str, request: pytest.FixtureRequest) -> None:
    repo: Path = request.getfixturevalue(fixture)
    service = _scanned(repo)
    items = {i.id: i for i in service.scan()}
    assert items["TSK-002"].status == WorkItemStatus.IN_PROGRESS, items
    # and again: a second rescan agrees
    items = {i.id: i for i in service.scan()}
    assert items["TSK-002"].status == WorkItemStatus.IN_PROGRESS, items


# ---------------------------------------------------------------------------
# 1. Outside a scan, the current theme's status names (red today)
# ---------------------------------------------------------------------------


def test_single_board_parse_file_outside_scan_reads_current_theme(single: Path) -> None:
    service = _scanned(single)
    path = single / "work" / "tasks" / "TSK-002.md"
    item = service._parse_file(path)
    assert item is not None
    assert item.status == WorkItemStatus.IN_PROGRESS, item.status
    # a rescan afterwards still agrees
    items = {i.id: i for i in service.scan()}
    assert items["TSK-002"].status == WorkItemStatus.IN_PROGRESS, items


def test_multiboard_get_board_outside_scan_reads_current_theme(multi: Path) -> None:
    service = _scanned(multi)
    board = service.get_board("devboard")
    items = {i.id: i for i in board.items}
    assert "TSK-002" in items, list(items)
    assert items["TSK-002"].status == WorkItemStatus.IN_PROGRESS, items["TSK-002"].status


def test_multiboard_get_items_by_board_outside_scan_reads_current_theme(multi: Path) -> None:
    service = _scanned(multi)
    items = {i.id: i for i in service.get_items(board="devboard")}
    assert "TSK-002" in items, list(items)
    assert items["TSK-002"].status == WorkItemStatus.IN_PROGRESS, items["TSK-002"].status


# ---------------------------------------------------------------------------
# 2. A nested scan() leaves the outer scan's `_scanning` set (white-box)
# ---------------------------------------------------------------------------


def test_nested_scan_keeps_outer_scanning(single: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = cli.get_service()
    seen: list[bool] = []
    real = KanbanService._scan_directory

    def nesting(self: KanbanService, directory: Path) -> list[Any]:
        if not seen:
            seen.append(self._scanning)  # outer scan: True
            self.scan()  # nested
            seen.append(self._scanning)  # must still be True
        return real(self, directory)

    monkeypatch.setattr(KanbanService, "_scan_directory", nesting)
    service.scan()
    assert seen[:2] == [True, True], seen
    assert service._scanning is False  # the outer scan ends it


def test_scan_raising_still_clears_scanning(single: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: `_scanning` is back to False after a scan that raises (green today)."""
    service = cli.get_service()

    def boom(self: KanbanService, directory: Path) -> list[Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(KanbanService, "_scan_directory", boom)
    with pytest.raises(RuntimeError):
        service.scan()
    assert service._scanning is False


# ---------------------------------------------------------------------------
# 3. Control: the scan still memoises (bounded theme loads)
# ---------------------------------------------------------------------------

N_ITEMS = 60


@pytest.mark.parametrize("fixture", ["single", "multi"])
def test_scan_loads_theme_a_bounded_number_of_times(
    fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo: Path = request.getfixturevalue(fixture)
    for n in range(2, N_ITEMS + 1):
        _item(repo, f"TSK-{n:03d}", "todo")
    service = cli.get_service()

    # counted at the service's own seams: `config._theme_cache` would hide per-item
    # lookups from a count of file loads
    calls = {"theme": 0, "board": 0}
    real_theme = type(service.config).get_theme
    real_board = KanbanService._load_board_theme

    def count_theme(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls["theme"] += 1
        return real_theme(self, *args, **kwargs)

    def count_board(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        calls["board"] += 1
        return real_board(self, *args, **kwargs)

    monkeypatch.setattr(type(service.config), "get_theme", count_theme)
    monkeypatch.setattr(KanbanService, "_load_board_theme", count_board)
    config_mod._theme_cache.clear()
    items = service.scan()
    assert len(items) == N_ITEMS, len(items)
    assert {i.status for i in items} == {WorkItemStatus.BACKLOG}
    assert calls["theme"] < 15, f"{calls['theme']} get_theme calls for {N_ITEMS} items"
    assert calls["board"] < 15, f"{calls['board']} board-theme loads for {N_ITEMS} items"
