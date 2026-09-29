"""#1093: `show`, `list` and `board` render an item's text with control characters escaped.

The #251/#215 ruling (repo text never reaches the terminal with raw control characters)
covers display as well as error lines ([steer] on #1093). An item file is repo text: its
front matter can carry ESC (YAML `\\e`) and newlines. `render_item_detail`, `render_list`
and `render_card` escaped Rich markup only, so `show` / `list` / `board` printed a title's
`\\x1b[2J` raw (clears the reader's screen) and its newline as a real line break (forges an
output line).

Decided behaviour: the item text these commands render goes through `safe()` (markup
escaped + `escape_nonprintable`): ESC shows as `\\x1b`, a newline as `\\n`, brackets
verbatim. `--json` output stays raw (JSON encodes it).

Each rendering is captured through a module console swapped for a wide, NON-terminal
`Console`: Rich then writes no colour codes of its own, so ANY `\\x1b` in the output
came from the item. One `show` run is also checked on a forced terminal, stripping only
Rich's own SGR colour codes.

Controls: `--json` round-trips the raw text; a printable title with `\\` and `[x]`
renders exactly as written.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

# the texts as they are once parsed; the file carries them as YAML double-quoted escapes
TITLE = "T\x1b[2J\nFORGED [bold]x[/bold]"
TITLE_SHOWN = "T\\x1b[2J\\nFORGED [bold]x[/bold]"
ASSIGNEE = "bob\x1b[31m"
ASSIGNEE_SHOWN = "bob\\x1b[31m"
TAG = "tg\x1b[5m"
TAG_SHOWN = "tg\\x1b[5m"
DEP = "FEAT-9\x1b[8m"
DEP_SHOWN = "FEAT-9\\x1b[8m"
SUPER = "FEAT-7\x1b[7m"
SUPER_SHOWN = "FEAT-7\\x1b[7m"

# a card cuts a title past 20 characters: keep this one at 19
CARD_TITLE = "\x1b[2J\nFORGED[b]x[/b]"
CARD_TITLE_SHOWN = "\\x1b[2J\\nFORGED[b]x[/b]"

PRINTABLE = r"back\slash [x] and [bold]b[/bold]"

CONFIG = "kanban:\n  theme: software\n  paths:\n    root: work/\n"


def _yaml(text: str) -> str:
    """`text` as a YAML double-quoted scalar (`\\e`, `\\n`, `\\\\`, `\\"` escapes)."""
    body = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\x1b", "\\e").replace("\n", "\\n")
    )
    return f'"{body}"'


# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(repo: Path, item_id: str, front: dict[str, str], body: str = "") -> None:
    lines = [f"id: {item_id}", "type: feature", "priority: medium", "created: 2026-09-25"]
    lines += [f"{k}: {v}" for k, v in front.items()]
    path = repo / "work" / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + "\n".join(lines) + f"\n---\n\n# {item_id}\n\n{body}")


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(CONFIG)
    return tmp_path


@pytest.fixture(autouse=True)
def _clean() -> None:
    config_mod._theme_cache.clear()


@pytest.fixture
def evil_repo(tmp_path: Path) -> Path:
    """FEAT-001 (backlog) carries ESC/newline/brackets in its free-text fields;
    FEAT-002 (done, superseded) carries ESC in `superseded_by`."""
    repo = _repo(tmp_path)
    _item(
        repo,
        "FEAT-001",
        {
            "title": _yaml(TITLE),
            "status": "backlog",
            "assignee": _yaml(ASSIGNEE),
            "tags": f"[{_yaml(TAG)}]",
            "depends_on": f"[{_yaml(DEP)}]",
        },
    )
    _item(
        repo,
        "FEAT-002",
        {
            "title": '"old"',
            "status": "done",
            "resolution": "superseded",
            "superseded_by": f"[{_yaml(SUPER)}]",
        },
    )
    return repo


def _run(
    repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch, *, terminal: bool = False
) -> str:
    """Run with the module console swapped for a wide one: no colour (so any ESC is the
    item's) unless `terminal`, where Rich's own SGR codes are stripped before returning."""
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, width=300, force_terminal=terminal))
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, (result.output, result.stderr, result.exception)
    out = buf.getvalue() + result.output
    if terminal:
        out = re.sub(r"\x1b\[[0-9;]*m", "", out)  # Rich's own colour codes only
    return out


def _run_json(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, (result.output, result.stderr, result.exception)
    return json.loads(result.output)


def _no_raw_control(out: str) -> None:
    assert "\x1b" not in out, f"raw ESC reached the terminal: {out!r}"
    assert not any(line.lstrip(" │|").startswith("FORGED") for line in out.splitlines()), (
        f"a title's newline forged an output line: {out!r}"
    )


# --- show -----------------------------------------------------------------------------


def test_show_has_no_raw_control_characters(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_raw_control(_run(evil_repo, ["show", "FEAT-001"], monkeypatch))


def test_show_has_no_raw_control_characters_on_a_terminal(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_raw_control(_run(evil_repo, ["show", "FEAT-001"], monkeypatch, terminal=True))


@pytest.mark.parametrize(
    "shown",
    [TITLE_SHOWN, ASSIGNEE_SHOWN, TAG_SHOWN, DEP_SHOWN],
    ids=["title", "assignee", "tags", "depends_on"],
)
def test_show_renders_each_field_escaped(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch, shown: str
) -> None:
    out = _run(evil_repo, ["show", "FEAT-001"], monkeypatch)
    assert shown in out, f"{shown!r} not shown literally: {out!r}"


def test_show_renders_superseded_by_escaped(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(evil_repo, ["show", "FEAT-002"], monkeypatch)
    assert "\x1b" not in out, repr(out)
    assert SUPER_SHOWN in out, repr(out)


def test_show_json_keeps_the_raw_text(evil_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _run_json(evil_repo, ["show", "FEAT-001", "--json"], monkeypatch)
    assert isinstance(data, dict)
    assert data["title"] == TITLE
    assert data["assignee"] == ASSIGNEE
    assert data["tags"] == [TAG]
    assert data["depends_on"] == [DEP]


# --- list -----------------------------------------------------------------------------


def test_list_has_no_raw_control_characters(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_raw_control(_run(evil_repo, ["list"], monkeypatch))


@pytest.mark.parametrize("shown", [TITLE_SHOWN, ASSIGNEE_SHOWN], ids=["title", "assignee"])
def test_list_renders_each_field_escaped(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch, shown: str
) -> None:
    out = _run(evil_repo, ["list"], monkeypatch)
    assert shown in out, f"{shown!r} not shown literally: {out!r}"


def test_list_json_keeps_the_raw_text(evil_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _run_json(evil_repo, ["list", "--json"], monkeypatch)
    rows = data if isinstance(data, list) else data["items"]  # type: ignore[index]
    row = next(r for r in rows if r["id"] == "FEAT-001")
    assert row["title"] == TITLE
    assert row["assignee"] == ASSIGNEE


# --- board ----------------------------------------------------------------------------


@pytest.fixture
def card_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    _item(
        repo,
        "FEAT-001",
        {"title": _yaml(CARD_TITLE), "status": "backlog", "assignee": _yaml(ASSIGNEE)},
    )
    return repo


def test_board_has_no_raw_control_characters(
    card_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_raw_control(_run(card_repo, ["board"], monkeypatch))


@pytest.mark.parametrize(
    "shown", [CARD_TITLE_SHOWN, "@" + ASSIGNEE_SHOWN], ids=["title", "assignee"]
)
def test_board_renders_each_field_escaped(
    card_repo: Path, monkeypatch: pytest.MonkeyPatch, shown: str
) -> None:
    out = _run(card_repo, ["board"], monkeypatch)
    assert shown in out, f"{shown!r} not shown literally: {out!r}"


# --- control: printable text is unchanged ---------------------------------------------


@pytest.fixture
def printable_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    _item(repo, "FEAT-001", {"title": _yaml(PRINTABLE), "status": "backlog"})
    return repo


@pytest.mark.parametrize("args", [["show", "FEAT-001"], ["list"]], ids=["show", "list"])
def test_printable_title_renders_unchanged(
    printable_repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    out = _run(printable_repo, args, monkeypatch)
    assert PRINTABLE in out, repr(out)
    assert "\\\\" not in out, f"a printable backslash was escaped: {out!r}"
