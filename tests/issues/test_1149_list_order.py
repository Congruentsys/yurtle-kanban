"""Issue #1149 — #1141 follow-ups from the PR #1148 review.

1. `list` loaded the board before validating `--priority` and `--resolution`:
   `list --board b --priority bogus` on a multi-board repo scanned, then refused.
   [steer] validate first, then load; the refusal order stays as before #1141 —
   `--type`, then `--priority`, then `--resolution` — and is pinned here.
2. `roadmap --ranked` without `--type` called `get_items()` it did not need
   (`get_ranked_items` loads on its own): it loads only for `--type`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _write(folder: Path, item_id: str, item_type: str, status: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item_id}-x.md").write_text(
        f"---\nid: {item_id}\ntitle: Item {item_id}\ntype: {item_type}\n"
        f"status: {status}\n---\n\n# Item {item_id}\n"
    )


@pytest.fixture
def multi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two boards; board `b` holds a spec item and a task."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    kanban = tmp_path / ".kanban"
    kanban.mkdir()
    (kanban / "config.yaml").write_text(
        yaml.safe_dump({
            "version": "2.0",
            "boards": [
                {"name": "a", "preset": "spec", "path": "a/"},
                {"name": "b", "preset": "spec", "path": "b/"},
            ],
            "default_board": "a",
        })
    )
    _write(tmp_path / "a", "DOC-001", "task", "draft")
    _write(tmp_path / "b", "DOC-002", "spec", "draft")
    _write(tmp_path / "b", "DOC-003", "task", "draft")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _invoke(*args: str) -> Result:
    config_mod._theme_cache.clear()
    result = CliRunner().invoke(main, list(args))
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`{' '.join(args)}` crashed: {result.exception!r}")
    return result


def _count(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"scan_board": 0, "get_items": 0}
    real_scan = KanbanService._scan_board
    real_get = KanbanService.get_items

    def scan_board(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        calls["scan_board"] += 1
        return real_scan(self, *args, **kwargs)

    def get_items(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        calls["get_items"] += 1
        return real_get(self, *args, **kwargs)

    monkeypatch.setattr(KanbanService, "_scan_board", scan_board)
    monkeypatch.setattr(KanbanService, "get_items", get_items)
    return calls


# --- item 1: list validates before loading ------------------------------------------


def test_list_board_bogus_priority_refuses_without_scanning(
    multi: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count(monkeypatch)
    result = _invoke("list", "--board", "b", "--priority", "bogus")
    assert result.exit_code == 1, result.output
    assert "bogus" in result.stderr, result.stderr
    assert calls == {"scan_board": 0, "get_items": 0}, calls


def test_list_board_bogus_resolution_refuses_without_scanning(
    multi: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count(monkeypatch)
    result = _invoke("list", "--board", "b", "--resolution", "bogus")
    assert result.exit_code == 1, result.output
    assert "Unknown resolution: bogus" in result.stderr, result.stderr
    assert calls == {"scan_board": 0, "get_items": 0}, calls


def test_list_board_canonical_type_bogus_priority_refuses_without_scanning(
    multi: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canonical `--type` needs no load to judge: `--priority` still refuses first."""
    calls = _count(monkeypatch)
    result = _invoke("list", "--board", "b", "--type", "task", "--priority", "bogus")
    assert result.exit_code == 1, result.output
    assert "bogus" in result.stderr, result.stderr
    assert calls == {"scan_board": 0, "get_items": 0}, calls


def test_list_valid_filters_still_load_once(
    multi: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count(monkeypatch)
    result = _invoke("list", "--board", "b", "--type", "spec", "--priority", "medium")
    assert result.exit_code == 0, result.output
    assert calls == {"scan_board": 1, "get_items": 1}, calls


# refusal order, as before #1141: --type, then --priority, then --resolution


def test_refusal_order_type_before_priority(multi: Path) -> None:
    result = _invoke("list", "--board", "b", "--type", "bogus", "--priority", "bogus")
    assert result.exit_code == 1, result.output
    assert "Unknown type: bogus" in result.stderr, result.stderr
    assert "priority" not in result.stderr.lower(), result.stderr


def test_refusal_order_type_before_resolution(multi: Path) -> None:
    result = _invoke("list", "--board", "b", "--type", "bogus", "--resolution", "bogus")
    assert result.exit_code == 1, result.output
    assert "Unknown type: bogus" in result.stderr, result.stderr
    assert "resolution" not in result.stderr.lower(), result.stderr


def test_refusal_order_priority_before_resolution(multi: Path) -> None:
    result = _invoke("list", "--board", "b", "--priority", "bogus", "--resolution", "bogus")
    assert result.exit_code == 1, result.output
    assert "bogus" in result.stderr, result.stderr
    assert "resolution" not in result.stderr.lower(), result.stderr


# --- item 2: roadmap --ranked loads only for --type ---------------------------------


def test_roadmap_ranked_without_type_makes_no_get_items_call(
    multi: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count(monkeypatch)
    # get_ranked_items loads on its own; stub it so only roadmap's own load counts
    monkeypatch.setattr(KanbanService, "get_ranked_items", lambda self, *a, **k: [])
    result = _invoke("roadmap", "--ranked", "--json")
    assert result.exit_code == 0, result.output
    assert calls["get_items"] == 0, calls


def test_roadmap_ranked_with_type_still_filters(multi: Path) -> None:
    result = _invoke("roadmap", "--ranked", "--type", "bogus")
    assert result.exit_code == 1, result.output
    assert "Unknown type: bogus" in result.stderr, result.stderr
