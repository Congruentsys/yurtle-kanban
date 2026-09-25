"""Issue #87 (and its duplicate #98) — the board counts an item it does not draw.

`init --theme hdd`, `create idea "A real item"`, `board` shows `Draft (1)` over an
empty column. The header count comes from `Board.get_column_counts()`, which
resolves a themed column id (`draft`) through `Board.column_status_map`
(`draft` -> BACKLOG); the cards come from `render_board`, which resolves the same
column id with `WorkItemStatus.from_string(col.id)` only, fails on `draft` and
draws nothing. Every column whose id is not a canonical status (hdd: all four;
spec: draft/proposed/implementing/accepted) is counted but never drawn.

Decided behaviour: on every theme and board layout, an item counted in a
column's header is drawn in that same column, so each header's count equals the
number of cards drawn under it.

`list` is not pinned here beyond what the issue requires (see the report on #87).
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban.cli import main

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"

# Types `create` accepts on each built-in theme. The spec theme also lists
# rfc/spec/spike/adr, which `create` refuses today ("Unknown type") — a separate
# problem, not #87's, so they are left out rather than pinned either way.
CREATABLE_TYPES: dict[str, list[str]] = {
    "software": ["feature", "bug", "epic", "issue", "task", "idea"],
    "nautical": ["expedition", "voyage", "chore", "hazard", "signal"],
    "spec": ["issue", "task"],
    "hdd": ["idea", "literature", "paper", "hypothesis", "experiment", "measure"],
}

ID_RE = re.compile(r"\b[A-Z]+(?:-[A-Z])?-\d{3,}\b")
HEADER_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<count>\d+)(?:/\d+)?\)$")

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(args, cwd=path, capture_output=True, check=True)


def _clear_theme_cache() -> None:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty git repo, cwd set to it, no kanban config yet."""
    _git_init(tmp_path)
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def wide(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Route cli's console to a wide, colourless buffer so the table never wraps."""
    buf = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=buf, width=300, color_system=None, force_terminal=False)
    )
    return buf


def _ok(result: Result) -> Result:
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    assert result.exit_code == 0, result.output
    return result


def _invoke(runner: CliRunner, args: list[str]) -> Result:
    return _ok(runner.invoke(main, args))


def _created_id(runner: CliRunner, buf: io.StringIO, args: list[str]) -> str:
    """Run a create command and return the new ID (printed via cli.console or not)."""
    buf.seek(0)
    buf.truncate()
    result = _invoke(runner, args)
    out = buf.getvalue() + result.output
    match = re.search(r"Created (\S+):", out)
    assert match, out
    return match.group(1)


def _board_text(runner: CliRunner, buf: io.StringIO, args: list[str]) -> str:
    buf.seek(0)
    buf.truncate()
    result = _invoke(runner, ["board", *args])
    return buf.getvalue() + result.output


def _parse_board(text: str) -> list[tuple[str, int, list[str]]]:
    """Parse the rendered board into [(column name, header count, drawn card IDs)].

    Column boundaries come from the header separator (`├──┼──┤`); a card is a
    Panel, counted by its `╭` corner inside a column's slice of each body line.
    """
    lines = text.splitlines()
    sep_idx = next(
        i for i, line in enumerate(lines) if line.lstrip().startswith("├") and "┤" in line
    )
    sep = lines[sep_idx]
    bounds = [i for i, ch in enumerate(sep) if ch in "├┼┤"]
    header = lines[sep_idx - 1]

    cols: list[tuple[str, int, list[str]]] = []
    for left, right in zip(bounds, bounds[1:]):
        cell = header[left + 1 : right].strip()
        match = HEADER_RE.match(cell)
        assert match, f"unparseable header cell {cell!r} in:\n{text}"
        cards = 0
        ids: list[str] = []
        for line in lines[sep_idx + 1 :]:
            if line.lstrip().startswith("╰") and len(line) > left and line[left] in "╰┴":
                break
            chunk = line[left + 1 : right]
            cards += chunk.count("╭")
            ids.extend(ID_RE.findall(chunk))
        assert cards == len(ids), f"card/ID mismatch in {cell!r}:\n{text}"
        cols.append((match.group("name"), int(match.group("count")), ids))
    assert cols, text
    return cols


def _assert_counts_match_cards(text: str, expected_ids: set[str]) -> None:
    cols = _parse_board(text)
    mismatched = [(name, count, ids) for name, count, ids in cols if count != len(ids)]
    assert not mismatched, (
        "header count != cards drawn under it (name, count, drawn): "
        f"{mismatched}\n{text}"
    )
    drawn = [i for _, _, ids in cols for i in ids]
    assert sorted(drawn) == sorted(expected_ids), (
        f"every item drawn exactly once; drawn={drawn}\n{text}"
    )


def _theme_columns(theme: str) -> list[str]:
    data = yaml.safe_load((THEMES_DIR / f"{theme}.yaml").read_text())
    return list(data["columns"])


# ---------------------------------------------------------------------------
# 1. The issue's repro: hdd, one idea
# ---------------------------------------------------------------------------


def test_hdd_idea_is_drawn_in_the_draft_column_it_is_counted_in(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "hdd"])
    item_id = _created_id(runner, wide, ["create", "idea", "A real item"])

    text = _board_text(runner, wide, [])
    cols = {name: (count, ids) for name, count, ids in _parse_board(text)}

    assert cols["Draft"] == (1, [item_id]), text
    for name in ("Active", "Complete", "Abandoned"):
        assert cols[name] == (0, []), text


# ---------------------------------------------------------------------------
# 2. #98's multi-board repro: nautical default + hdd research
# ---------------------------------------------------------------------------


def test_multiboard_hdd_hypothesis_drawn_in_draft(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "nautical"])
    _invoke(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])
    _clear_theme_cache()
    item_id = _created_id(
        runner, wide, ["hypothesis", "create", "probe hypothesis", "--target", ">=1"]
    )
    files = list((repo / "research").rglob(f"{item_id}-*.md"))
    assert files, "hypothesis file not written under research/"

    text = _board_text(runner, wide, ["research"])
    cols = {name: (count, ids) for name, count, ids in _parse_board(text)}
    assert cols["Draft"] == (1, [item_id]), text

    # `list --board research` still shows the item (status wording not pinned, see #87)
    listed = _invoke(runner, ["list", "--board", "research"])
    assert item_id in listed.output + wide.getvalue()



def test_multiboard_board_all_draws_what_it_counts(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    """`board --all` renders each board with the same renderer: same contract."""
    _invoke(runner, ["init", "--theme", "nautical"])
    _invoke(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])
    _clear_theme_cache()
    dev_id = _created_id(runner, wide, ["create", "expedition", "dev item"])
    hyp_id = _created_id(
        runner, wide, ["hypothesis", "create", "probe hypothesis", "--target", ">=1"]
    )

    text = _board_text(runner, wide, ["--all"])
    sections = {
        chunk.split("\n", 1)[0].strip(): chunk
        for chunk in text.split("Board: ")[1:]
    }
    assert set(sections) == {"default", "research"}, text
    _assert_counts_match_cards(sections["default"], {dev_id})
    _assert_counts_match_cards(sections["research"], {hyp_id})

# ---------------------------------------------------------------------------
# 3. Every built-in theme: every type, and every column holding an item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", sorted(CREATABLE_TYPES))
def test_every_theme_count_equals_cards_drawn(
    repo: Path, runner: CliRunner, wide: io.StringIO, theme: str
) -> None:
    _invoke(runner, ["init", "--theme", theme])
    columns = _theme_columns(theme)
    types = CREATABLE_TYPES[theme]

    # at least one item of each type, and at least one item per column: item i
    # is moved to column i (by the theme's own column id; column 0 is where
    # `create` puts it)
    ids: list[str] = []
    for i in range(max(len(types), len(columns))):
        item_type = types[i % len(types)]
        ids.append(_created_id(runner, wide, ["create", item_type, f"item {i} {item_type}"]))
    for i, item_id in enumerate(ids):
        column = columns[i % len(columns)]
        if column != columns[0]:
            _invoke(runner, ["move", item_id, column, "--force", "--skip-gates", "--no-commit"])

    text = _board_text(runner, wide, [])
    _assert_counts_match_cards(text, set(ids))


# ---------------------------------------------------------------------------
# 4. Controls: themes whose column ids are canonical statuses already agree
# ---------------------------------------------------------------------------


def test_software_single_item_control(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "software"])
    item_id = _created_id(runner, wide, ["create", "feature", "A real item"])
    text = _board_text(runner, wide, [])
    cols = {name: (count, ids) for name, count, ids in _parse_board(text)}
    assert cols["Backlog"] == (1, [item_id]), text
