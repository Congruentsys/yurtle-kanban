"""Issue #225 — YAML booleans read as "true"/"false"; pin the board card ID fold.

Follow-ups from the review of PR #224 (#206):

- `assignee: true` becomes `"True"`, Python's spelling. Decided: booleans in
  `assignee`, `priority` and `value_summary` read as YAML spells them,
  `"true"`/`"false"`. Numbers are unchanged (`"5"`, `"1.5"`).
- Setting the card's ID line back to `overflow="ellipsis"` failed no test, because
  `FEAT-010` fits at 80 columns. Pin it with an ID too long for its card.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from rich.console import Console

from yurtle_kanban.board import render_board
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.service import KanbanService


def _git_init(path: Path) -> None:
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=path, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with the software theme, cwd set to it."""
    from yurtle_kanban import config as config_mod

    _git_init(tmp_path)
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/features/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    config_mod._theme_cache.clear()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _write(repo: Path, item_id: str, fields: str, title: str = "") -> None:
    path = repo / "kanban-work" / "features" / f"{item_id}-x.md"
    path.write_text(
        f"---\nid: {item_id}\ntitle: {title or 'Item ' + item_id}\ntype: feature\n"
        f"status: backlog\n{fields}---\n\nBody\n"
    )


def _items(repo: Path) -> dict:
    return {i.id: i for i in _service(repo).get_items()}


# ---------------------------------------------------------------------------
# 1. Booleans read as YAML spells them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "false"])
def test_boolean_assignee_reads_lowercase(repo: Path, value: str) -> None:
    _write(repo, "FEAT-001", f"assignee: {value}\n")
    assert _items(repo)["FEAT-001"].assignee == value


@pytest.mark.parametrize("value", ["true", "false"])
def test_boolean_value_summary_reads_lowercase(repo: Path, value: str) -> None:
    _write(repo, "FEAT-001", f"value_summary: {value}\n")
    assert _items(repo)["FEAT-001"].value_summary == value


@pytest.mark.parametrize("value", ["true", "false"])
def test_boolean_priority_reads_lowercase(repo: Path, value: str) -> None:
    _write(repo, "FEAT-001", f"priority: {value}\n")
    priority = _items(repo)["FEAT-001"].priority
    assert priority == value, repr(priority)


def test_numbers_unchanged_control(repo: Path) -> None:
    _write(repo, "FEAT-001", "assignee: 5\nvalue_summary: 1.5\n")
    item = _items(repo)["FEAT-001"]
    assert item.assignee == "5"
    assert item.value_summary == "1.5"


def test_quoted_capitalised_string_unchanged_control(repo: Path) -> None:
    _write(repo, "FEAT-001", "assignee: 'True'\nvalue_summary: 'False'\n")
    item = _items(repo)["FEAT-001"]
    assert item.assignee == "True"
    assert item.value_summary == "False"


# ---------------------------------------------------------------------------
# 2. Board card: a long ID folds, never cut with an ellipsis
# ---------------------------------------------------------------------------

LONG_ID = "EXPR-100016"
NARROW = 60  # software theme: the card is far narrower than LONG_ID here


def _render(repo: Path, width: int) -> str:
    buf = io.StringIO()
    render_board(_service(repo).get_board(), Console(width=width, file=buf))
    return buf.getvalue()


def _dense(text: str) -> str:
    """Drop whitespace and box-drawing chars: cards wrap inside narrow columns."""
    return "".join(c for c in text if not c.isspace() and not "─" <= c <= "╿")


@pytest.fixture
def long_id(repo: Path) -> Path:
    _write(repo, LONG_ID, "", title="x")
    return repo


def test_long_id_does_not_fit_one_line_precondition(long_id: Path) -> None:
    text = _render(long_id, NARROW)
    assert not any(LONG_ID in ln for ln in text.splitlines()), text


def test_long_id_folds_whole(long_id: Path) -> None:
    text = _render(long_id, NARROW)
    assert LONG_ID in _dense(text), text
    assert "…" not in text, text


def test_long_id_whole_on_wide_board_control(long_id: Path) -> None:
    text = _render(long_id, 200)
    assert any(LONG_ID in ln for ln in text.splitlines()), text
