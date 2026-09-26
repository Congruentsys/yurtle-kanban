"""Issue #463 — pin get_board's scan scope (follow-up A from the review of PR #460).

Decided behaviour: in a multi-board repo, the public per-board reads
`get_board("<board>")` and `get_items(board=...)` share one status-name lookup per
read. They parse outside `scan()`, so it is `_scan_board`'s own `_scan_scope()` that
memoises the board's theme status names: with 60+ items on a theme-status board
(hdd `active`), one read outside `scan()` builds the board's status names — and so
loads its theme through `_load_board_theme` — at most twice, not once per item.

Counted at the service's own seam, as test_459's bounded-load control does:
`config._theme_cache` hides per-item lookups from a count of file loads
(`_load_builtin_theme`). Expected GREEN today (a pin); RED without the scope.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItem, WorkItemStatus
from yurtle_kanban.service import KanbanService

N_ITEMS = 60
MAX_LOADS = 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(repo: Path, item_id: str, status: str) -> None:
    path = repo / "research" / "ideas" / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntype: idea\nstatus: {status}\n"
        f"title: Idea {item_id}\n---\n\n# Idea {item_id}\n"
    )


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """v2 config: `development` (nautical, default) and `research` (hdd), with
    N_ITEMS research ideas in hdd's own `active` status."""
    config = {
        "version": "2.0",
        "boards": [
            {"name": "development", "preset": "nautical", "path": "kanban-work/"},
            {"name": "research", "preset": "hdd", "path": "research/"},
        ],
        "default_board": "development",
    }
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(yaml.safe_dump(config))
    (tmp_path / "kanban-work").mkdir()
    for n in range(1, N_ITEMS + 1):
        _item(tmp_path, f"IDEA-R-{n:03d}", "active")
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _counted(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count `_load_board_theme` calls, and `_theme_status_names` calls that build
    (miss the memo) rather than hit it."""
    calls = {"board": 0, "builds": 0}
    real_board = KanbanService._load_board_theme

    def count_board(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        calls["board"] += 1
        return real_board(self, *args, **kwargs)

    real_names = KanbanService._theme_status_names

    def count_names(self: KanbanService, file_path: Path | None) -> Any:
        before = calls["board"]
        result = real_names(self, file_path)
        if calls["board"] > before:  # loaded a theme: a build, not a memo hit
            calls["builds"] += 1
        return result

    monkeypatch.setattr(KanbanService, "_load_board_theme", count_board)
    monkeypatch.setattr(KanbanService, "_theme_status_names", count_names)
    return calls


def _get_board(service: KanbanService) -> list[WorkItem]:
    return list(service.get_board("research").items)


def _get_items(service: KanbanService) -> list[WorkItem]:
    return service.get_items(board="research")


@pytest.mark.parametrize("read", [_get_board, _get_items], ids=["get_board", "get_items"])
def test_per_board_read_outside_scan_builds_status_names_boundedly(
    repo: Path, monkeypatch: pytest.MonkeyPatch, read: Callable[[KanbanService], list[WorkItem]]
) -> None:
    service = cli.get_service()
    assert service._scanning is False  # outside scan(): no memo from a scan
    calls = _counted(monkeypatch)
    config_mod._theme_cache.clear()

    items = read(service)

    assert len(items) == N_ITEMS, len(items)
    # hdd `active` read through the board's theme
    assert {i.status for i in items} == {WorkItemStatus.IN_PROGRESS}
    assert calls["builds"] <= MAX_LOADS, (
        f"{calls['builds']} status-name builds for {N_ITEMS} items in one read"
    )
    assert calls["board"] <= MAX_LOADS, (
        f"{calls['board']} board-theme loads for {N_ITEMS} items in one read"
    )
    assert service._scanning is False  # the read's scope ends with it


def test_each_read_starts_fresh(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two reads build twice in total (one each), not zero for the second: the memo
    is per read, so a theme change between reads is still seen (#459)."""
    service = cli.get_service()
    calls = _counted(monkeypatch)
    _get_board(service)
    _get_items(service)
    assert 2 <= calls["builds"] <= 2 * MAX_LOADS, calls
