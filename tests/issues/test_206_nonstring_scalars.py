"""Issue #206 — non-string frontmatter scalars; board folding on narrow terminals.

Follow-ups from the review of PR #201 (#179):

- `assignee: 5` crashes `list`, `roadmap` and `history` (`escape(int)`);
  `value_summary: 42` hits `len(int)` in `roadmap --ranked`; `query` puts a raw int
  priority into a table cell.
- `overflow="fold"` on the board columns splits tags and assignees mid-word at 80
  columns (carclaw: +26 lines at 80 columns, none at 160).

Decided behaviour:
1. `_parse_file` coerces scalar `assignee`, `priority` and `value_summary` to `str`,
   as #179 does for `id` and `title`. Every listing command prints such items.
2. At 80 columns only the card TITLE folds (the #179 behaviour); a tag or assignee
   is shown whole on one line or cut with an ellipsis, never split mid-word.
   At 160 columns nothing is cut.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban.board import render_board
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.service import KanbanService

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=path, capture_output=True, check=True)


def _clear_theme_cache() -> None:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with the software theme, cwd set to it."""
    _git_init(tmp_path)
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/features/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _write(repo: Path, item_id: str, fields: str, status: str = "backlog") -> None:
    path = repo / "kanban-work" / "features" / f"{item_id}-x.md"
    path.write_text(
        f"---\nid: {item_id}\ntitle: Item {item_id}\ntype: feature\n"
        f"status: {status}\n{fields}---\n\nBody\n"
    )


def _dense(text: str) -> str:
    """Drop whitespace and box-drawing chars: tables wrap inside narrow columns."""
    return "".join(c for c in text if not c.isspace() and not "─" <= c <= "╿")


def _assert_ok(result: Result) -> None:
    assert "Traceback" not in result.output, result.output
    assert "TypeError" not in result.output, result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    assert result.exit_code == 0, result.output


NUMERIC_FIELDS = "assignee: 5\npriority: 1\npriority_rank: 1\nvalue_summary: 42\n"
STRING_FIELDS = (
    "assignee: mini\npriority: high\npriority_rank: 2\nvalue_summary: Ships value\n"
)


@pytest.fixture
def numeric(repo: Path) -> Path:
    """One open and one done item with numeric scalars, plus a string control."""
    _write(repo, "FEAT-001", NUMERIC_FIELDS)
    _write(repo, "FEAT-002", NUMERIC_FIELDS, status="done")
    _write(repo, "FEAT-003", STRING_FIELDS)
    return repo


# ---------------------------------------------------------------------------
# 1a. Parse: scalars become strings
# ---------------------------------------------------------------------------


def test_numeric_scalars_scan_as_strings(numeric: Path) -> None:
    items = {i.id: i for i in _service(numeric).get_items()}
    item = items["FEAT-001"]
    assert item.assignee == "5"
    assert item.value_summary == "42"
    # priority: a string (or None if normalization drops it), never an int
    assert item.priority is None or isinstance(item.priority, str), repr(item.priority)
    assert not isinstance(item.priority, (int, float))


def test_string_scalars_unchanged_control(numeric: Path) -> None:
    items = {i.id: i for i in _service(numeric).get_items()}
    item = items["FEAT-003"]
    assert item.assignee == "mini"
    assert item.priority == "high"
    assert item.value_summary == "Ships value"
    assert item.priority_rank == 2


def test_list_assignee_unchanged_control(repo: Path) -> None:
    _write(repo, "FEAT-004", "assignee: [a, b]\n")
    items = {i.id: i for i in _service(repo).get_items()}
    assert items["FEAT-004"].assignee in (["a", "b"], "a, b"), items["FEAT-004"].assignee


# ---------------------------------------------------------------------------
# 1b. CLI: every listing command prints such items
# ---------------------------------------------------------------------------

COMMANDS = [
    ["list"],
    ["roadmap"],
    ["roadmap", "--ranked"],
    ["roadmap", "--by-type"],
    ["history"],
    ["history", "--by-assignee"],
    ["board"],
    ["show", "FEAT-001"],
    ["show", "FEAT-002"],
]


@pytest.mark.parametrize("args", COMMANDS, ids=lambda a: "-".join(a))
def test_numeric_scalars_print(numeric: Path, runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(main, args)
    _assert_ok(result)


@pytest.mark.parametrize("args", [["list"], ["roadmap"], ["history"]], ids=lambda a: a[0])
def test_numeric_assignee_shown(numeric: Path, runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(main, args)
    _assert_ok(result)
    item = "FEAT-002" if args[0] == "history" else "FEAT-001"
    line = next(ln for ln in result.output.splitlines() if item in ln)
    assert "5" in line.split(item, 1)[1], result.output


def test_ranked_roadmap_shows_numeric_summary(numeric: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["roadmap", "--ranked"])
    _assert_ok(result)
    line = next(ln for ln in result.output.splitlines() if "FEAT-001" in ln)
    assert "42" in line, result.output


@pytest.mark.parametrize("args", COMMANDS, ids=lambda a: "-".join(a))
def test_string_scalars_print_control(
    repo: Path, runner: CliRunner, args: list[str]
) -> None:
    _write(repo, "FEAT-001", STRING_FIELDS)
    _write(repo, "FEAT-002", STRING_FIELDS, status="done")
    result = runner.invoke(main, args)
    _assert_ok(result)


def test_query_table_numeric_priority(numeric: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "feature", "--no-semantic"])
    if isinstance(result.exception, ImportError):
        pytest.skip(f"query needs an optional dependency: {result.exception}")
    _assert_ok(result)


# ---------------------------------------------------------------------------
# 2. Board at narrow and wide terminals
# ---------------------------------------------------------------------------

TITLE = "[bold]x[/bold]"
WHOLE = ["#infrastructure", "#observability", "@observability"]


@pytest.fixture
def tagged(repo: Path) -> Path:
    path = repo / "kanban-work" / "features" / "FEAT-010-tagged.md"
    path.write_text(
        f"---\nid: FEAT-010\ntitle: '{TITLE}'\ntype: feature\nstatus: backlog\n"
        "assignee: observability\ntags: [infrastructure, observability]\n---\n\nBody\n"
    )
    return repo


def _render(repo: Path, width: int) -> str:
    buf = io.StringIO()
    render_board(_service(repo).get_board(), Console(width=width, file=buf))
    return buf.getvalue()


def _cells(text: str) -> list[str]:
    """Each table/panel cell's text on each output line."""
    out = []
    for line in text.splitlines():
        for part in line.replace("│", "|").split("|"):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _assert_not_split(text: str) -> None:
    cells = _cells(text)
    for token in WHOLE:
        # no cell starts with a tail of the token (`ructure`, `ility`, ...)
        for n in range(3, len(token) - 1):
            tail = token[-n:]
            bad = [c for c in cells if c.startswith(tail)]
            assert not bad, f"{token!r} split mid-word, tail {tail!r}:\n{text}"
        # a cell holding the token's head holds all of it, or cuts it with "…"
        head = token[:5]
        for cell in cells:
            if head in cell:
                assert token in cell or "…" in cell, f"{token!r} split:\n{text}"


def test_narrow_board_does_not_split_tags_or_assignee(tagged: Path) -> None:
    _assert_not_split(_render(tagged, 80))


def test_narrow_board_still_folds_title(tagged: Path) -> None:
    text = _render(tagged, 80)
    assert _dense(TITLE) in _dense(text), text
    assert "FEAT-010" in _dense(text), text


def test_wide_board_shows_everything_whole_control(tagged: Path) -> None:
    text = _render(tagged, 160)
    assert "…" not in text, text
    _assert_not_split(text)
    lines = text.splitlines()
    for token in [*WHOLE, TITLE, "FEAT-010"]:
        assert any(token in ln for ln in lines), f"{token!r} not whole:\n{text}"


def test_board_cli_numeric_scalars_narrow(numeric: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["board"], env={"COLUMNS": "80"})
    _assert_ok(result)


# ---------------------------------------------------------------------------
# 1c. One numeric field at a time: each crash on its own
# ---------------------------------------------------------------------------


def test_ranked_roadmap_numeric_summary_alone(repo: Path, runner: CliRunner) -> None:
    _write(repo, "FEAT-005", "assignee: mini\npriority_rank: 1\nvalue_summary: 42\n")
    result = runner.invoke(main, ["roadmap", "--ranked"])
    _assert_ok(result)
    line = next(ln for ln in result.output.splitlines() if "FEAT-005" in ln)
    assert "42" in line, result.output


def test_query_numeric_priority_alone(repo: Path, runner: CliRunner) -> None:
    _write(repo, "FEAT-006", "assignee: mini\npriority: 1\n")
    result = runner.invoke(main, ["query", "feature", "--no-semantic"])
    if isinstance(result.exception, ImportError):
        pytest.skip(f"query needs an optional dependency: {result.exception}")
    _assert_ok(result)
