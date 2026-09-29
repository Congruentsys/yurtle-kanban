"""#1093: `roadmap`, `history`, and `show`'s description and comments render repo text
with control characters escaped.

The sites `test_1093_display_control_chars.py` does not pin: the roadmap table, the
history table (and its `--by-assignee` headers and contributors line), and the
description panel and comments `show` prints. Single-line fields go through `safe()`
(ESC as `\\x1b`, a newline as `\\n`); the description and comment text are escaped
line by line, so ESC never reaches the terminal but multi-line text stays multi-line.

Each rendering is captured through a module console swapped for a wide, NON-terminal
`Console`: Rich writes no colour codes of its own, so ANY `\\x1b` came from the item.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

TITLE = "T\x1b[2J\nFORGED"
TITLE_SHOWN = "T\\x1b[2J\\nFORGED"
ASSIGNEE = "bob\x1b[31m"
ASSIGNEE_SHOWN = "bob\\x1b[31m"

CONFIG = "kanban:\n  theme: software\n  paths:\n    root: work/\n"


def _yaml(text: str) -> str:
    body = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\x1b", "\\e").replace("\n", "\\n")
    )
    return f'"{body}"'


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


def _run(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> str:
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, width=300, force_terminal=False))
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, (result.output, result.stderr, result.exception)
    return buf.getvalue() + result.output


def _no_raw_control(out: str) -> None:
    assert "\x1b" not in out, f"raw ESC reached the terminal: {out!r}"
    assert not any(line.lstrip(" │|").startswith("FORGED") for line in out.splitlines()), (
        f"a newline forged an output line: {out!r}"
    )


# --- roadmap --------------------------------------------------------------------------


@pytest.fixture
def roadmap_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    _item(
        repo,
        "FEAT-001",
        {"title": _yaml(TITLE), "status": "backlog", "assignee": _yaml(ASSIGNEE)},
    )
    return repo


@pytest.mark.parametrize("args", [["roadmap"], ["roadmap", "--by-type"]], ids=["flat", "by-type"])
def test_roadmap_renders_escaped(
    roadmap_repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    out = _run(roadmap_repo, args, monkeypatch)
    _no_raw_control(out)
    assert TITLE_SHOWN in out, repr(out)
    assert ASSIGNEE_SHOWN in out, repr(out)


# --- history --------------------------------------------------------------------------


@pytest.fixture
def history_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    _item(
        repo,
        "FEAT-001",
        {"title": _yaml(TITLE), "status": "done", "assignee": _yaml(ASSIGNEE)},
    )
    return repo


@pytest.mark.parametrize(
    "args",
    [["history"], ["history", "--by-assignee"], ["history", "--by-type"]],
    ids=["flat", "by-assignee", "by-type"],
)
def test_history_renders_escaped(
    history_repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    out = _run(history_repo, args, monkeypatch)
    _no_raw_control(out)
    assert TITLE_SHOWN in out, repr(out)
    assert ASSIGNEE_SHOWN in out, repr(out)


def test_history_by_assignee_header_is_escaped(
    history_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(history_repo, ["history", "--by-assignee"], monkeypatch)
    assert "@" + ASSIGNEE_SHOWN in out, repr(out)


# --- show: description and comments ----------------------------------------------------

BODY = (
    "first\x1b[2J line [b]x[/b]\n"
    "second line\n"
    "\n"
    "## Comments\n"
    "\n"
    "### ev\x1b[31mil (2026-09-25 10:00)\n"
    "\n"
    "hello\x1b[5m there\n"
    "again\n"
)


@pytest.fixture
def body_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    _item(repo, "FEAT-001", {"title": '"t"', "status": "backlog"}, body=BODY)
    return repo


def _line_with(out: str, text: str) -> str:
    return next((ln for ln in out.splitlines() if text in ln), "")


def test_show_description_and_comments_have_no_raw_esc(
    body_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(body_repo, ["show", "FEAT-001"], monkeypatch)
    assert "\x1b" not in out, f"raw ESC reached the terminal: {out!r}"


def test_show_description_is_escaped_line_by_line(
    body_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(body_repo, ["show", "FEAT-001"], monkeypatch)
    first = _line_with(out, "first")
    assert "first\\x1b[2J line [b]x[/b]" in first, repr(out)
    # multi-line text stays multi-line: the second line is its own output line
    assert "second line" not in first, repr(out)
    assert _line_with(out, "second line"), repr(out)
    assert "\\n" not in _line_with(out, "second line"), repr(out)


def test_show_comments_are_escaped_line_by_line(
    body_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(body_repo, ["show", "FEAT-001"], monkeypatch)
    assert "ev\\x1b[31mil" in out, repr(out)
    hello = _line_with(out, "hello")
    assert "hello\\x1b[5m there" in hello, repr(out)
    assert "again" not in hello, repr(out)
    assert _line_with(out, "again").startswith("    again"), repr(out)


# --- whitespace in multi-line text stays whitespace ------------------------------------


def test_show_description_keeps_tabs_and_crlf_as_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tab or a CRLF line end can't move the cursor or forge a line, so the
    description shows them as whitespace, not `\\t` / `\\r` ([steer] on #1093);
    ESC on the same line is still escaped."""
    repo = _repo(tmp_path)
    _item(repo, "FEAT-001", {"title": '"t"', "status": "backlog"})
    path = repo / "work" / "FEAT-001.md"
    body = "col\tumn \x1b[2J\r\nnext line\r\n"
    path.write_bytes(path.read_bytes() + body.encode())
    out = _run(repo, ["show", "FEAT-001"], monkeypatch)
    _no_raw_control(out)
    col = _line_with(out, "col")
    assert "\\t" not in col and "\\r" not in out, repr(out)
    assert "umn \\x1b[2J" in col, repr(out)
    assert "next line" not in col and _line_with(out, "next line"), repr(out)
