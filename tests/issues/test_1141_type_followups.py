"""Issue #1141 — #1131 follow-ups.

1. [steer] keep: a type declared by any item on the board, done ones included, is a
   valid `--type`. `roadmap` shows only open items, so `roadmap --type X` where only
   done items declare X prints an empty roadmap (exit 0), not a refusal; its
   `--help` says so.
2. `valid_types` reused nothing: `type_filter` called `get_items` again, which on a
   multi-board repo with `--board` re-ran `_scan_board`. It now takes the items the
   caller already loaded: `list --board B --type spec` scans the board once.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer
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


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


@pytest.fixture
def done_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Single board: `spec` is declared only by a done item; `task` by an open one."""
    _git_init(tmp_path)
    kanban = tmp_path / ".kanban"
    kanban.mkdir()
    (kanban / "config.yaml").write_text(
        yaml.safe_dump({"kanban": {"theme": "spec", "paths": {"root": "work/"}}})
    )
    _write(tmp_path / "work", "DOC-001", "spec", "done")
    _write(tmp_path / "work", "DOC-002", "task", "draft")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def multi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two boards; board `b` holds two spec items and a task."""
    _git_init(tmp_path)
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
    _write(tmp_path / "b", "DOC-003", "spec", "draft")
    _write(tmp_path / "b", "DOC-004", "task", "draft")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _invoke(*args: str) -> Result:
    config_mod._theme_cache.clear()
    result = CliRunner().invoke(main, list(args))
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`{' '.join(args)}` crashed: {result.exception!r}")
    return result


# --- item 1: roadmap --type on a type only done items declare ----------------------


def test_roadmap_type_done_only_json_is_empty_list(done_only: Path) -> None:
    result = _invoke("roadmap", "--type", "spec", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [], result.stdout


def test_roadmap_type_done_only_text_is_empty_roadmap(done_only: Path) -> None:
    result = _invoke("roadmap", "--type", "spec")
    assert result.exit_code == 0, result.output
    assert "Unknown type" not in result.output, result.output
    assert "Roadmap" in result.stdout, result.stdout
    assert "No items to show." in result.stdout, result.stdout
    assert "DOC-001" not in result.stdout, result.stdout


def test_roadmap_type_done_only_export_md_is_heading_only(done_only: Path) -> None:
    result = _invoke("roadmap", "--type", "spec", "--export", "md")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "# Roadmap", result.stdout


def test_roadmap_type_undeclared_still_refused(done_only: Path) -> None:
    result = _invoke("roadmap", "--type", "rfc")
    assert result.exit_code == 1, result.output
    assert "Unknown type: rfc" in result.stderr, result.stderr


def test_list_type_done_only_lists_the_done_item(done_only: Path) -> None:
    """`list` shows done items, so the same type lists the done item."""
    result = _invoke("list", "--type", "spec", "--json")
    assert result.exit_code == 0, result.output
    assert {i["id"] for i in json.loads(result.stdout)} == {"DOC-001"}


def test_roadmap_help_states_done_only_case(done_only: Path) -> None:
    result = _invoke("roadmap", "--help")
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split()).lower()
    assert "done" in text and "empty roadmap" in text, result.output


# --- item 2: valid_types reuses the loaded items ------------------------------------


def _count_scans(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
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


def test_list_board_type_declared_scans_board_once(
    multi: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_scans(monkeypatch)
    result = _invoke("list", "--board", "b", "--type", "spec", "--json")
    assert result.exit_code == 0, result.output
    assert {i["id"] for i in json.loads(result.stdout)} == {"DOC-002", "DOC-003"}
    assert calls == {"scan_board": 1, "get_items": 1}, calls


def test_list_board_type_unknown_scans_board_once(
    multi: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal's `Valid types:` line reuses the same items too."""
    calls = _count_scans(monkeypatch)
    result = _invoke("list", "--board", "b", "--type", "bogus")
    assert result.exit_code == 1, result.output
    assert "Unknown type: bogus" in result.stderr, result.stderr
    assert "spec" in result.stderr.split("Valid types:", 1)[1], result.stderr
    assert calls == {"scan_board": 1, "get_items": 1}, calls


def test_list_board_type_scoped_to_board(multi: Path) -> None:
    """`spec` is declared only on board b: board a refuses it."""
    result = _invoke("list", "--board", "a", "--type", "spec")
    assert result.exit_code == 1, result.output
    assert "Unknown type: spec" in result.stderr, result.stderr


def test_roadmap_type_declared_one_get_items(
    done_only: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_scans(monkeypatch)
    result = _invoke("roadmap", "--type", "spec", "--json")
    assert result.exit_code == 0, result.output
    assert calls["get_items"] == 1, calls


def test_mcp_list_type_declared_one_get_items(
    done_only: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = KanbanMCPServer(repo_root=done_only)
    calls = _count_scans(monkeypatch)
    got = server._list_items({"item_type": "spec"})
    assert {i["id"] for i in got["items"]} == {"DOC-001"}, got
    assert calls["get_items"] == 1, calls
    got = server._list_items({"item_type": "spec", "status": "in_progress"})
    assert got["items"] == [], got
