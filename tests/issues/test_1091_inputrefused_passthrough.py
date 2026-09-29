"""Issue #1091 — an `InputRefused` passed through to `click.ClickException` is escaped
once, at the pass-through; the hdd escape sites are pinned behaviourally.

Found in the review of PR #1088 (#1085). Three sites hand an `InputRefused` (or a
`ValueError` that is one) to click as `ClickException(str(e))`, so the `Error:` line
is only as safe as each producer's own escaping:

- `_click.Group.invoke`: any `InputRefused` a command lets escape;
- `epic_commands._do_add`: `service.refuse_duplicate(item, "a link")`;
- `hdd_commands._render` (an `InvalidTurtleName`) and
  `hdd_commands._refuse_duplicate_parent` (`refuse_duplicate(parent, "a parent link")`).

`service.refuse_duplicate` interpolates `item.id` and the repo file paths raw, so a
duplicate ID whose second copy lives under a directory named with ESC + newline puts
a raw ESC sequence and a forged line on stderr through `epic add` and
`literature create --idea`.

Decided ([steer] on #1091, bucket 2): escape once at each pass-through,
`ClickException(escape_nonprintable(str(e)))`, plain text. Pinned here, per site:

1. The `Error:` line holds `\\x1b` / `\\n` (a literal backslash), no raw ESC, and is
   ONE line (nothing forged after it).
2. Text a producer already escaped (a literal `\\x1b`, 4 characters) comes through
   unchanged: not double-escaped to `\\\\x1b`.
3. Brackets (`[bold]`) show verbatim, no backslash: click prints plain text, not markup.

Real producers where one is reachable offline (epic, hdd parent link); a
monkeypatched producer for the `Group` handler (`stats` lets `get_board`'s
`InputRefused` through) and for `_render`'s `InvalidTurtleName`, whose real producer
already escapes itself with `repr()`.

Item 2 (the hdd `already exists: {title}` / `Failed: {message}` sites, pinned only by
#1085's static sweep until now): `measure create --id M-001` against an M-001 whose
title holds ESC + newline, and `measure create --push` whose push fails with such a
message. Both already escape (#1085), so these are green today: behavioural pins.

The runner passes `color=True`, so click doesn't strip ANSI sequences itself, as it
would not on a real terminal.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused
from yurtle_kanban.service import KanbanService
from yurtle_kanban.template_engine import TemplateEngine
from yurtle_kanban.turtle_builder import InvalidTurtleName

# a directory name: ESC sequence, newline, markup-like brackets, an already-escaped \x1b
EVIL_DIR = "a\x1b[2J\nFORGED [bold] \\x1b b"
EVIL_DIR_SHOWN = "a\\x1b[2J\\nFORGED [bold] \\x1b b"  # escaped once
EVIL_MSG = "bad \x1b[2J\nFORGED [bold] \\x1b end"
EVIL_MSG_SHOWN = "bad \\x1b[2J\\nFORGED [bold] \\x1b end"

SOFTWARE = "kanban:\n  theme: software\n  paths:\n    root: work/\n"
TWO_BOARDS = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: "work/"
  - name: research
    preset: hdd
    path: "research/"
default_board: development
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _item(item_id: str, item_type: str, title: str = "t") -> str:
    return (
        f'---\nid: "{item_id}"\ntitle: "{title}"\ntype: {item_type}\nstatus: backlog\n'
        f"priority: medium\ncreated: 2026-09-29\n---\n\n# {item_id}\n"
    )


def _init(repo: Path, config: str) -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(config)


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> None:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def software(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Software board: EPIC-001, and FEAT-001 twice, the copy under EVIL_DIR."""
    _init(tmp_path, SOFTWARE)
    _write(tmp_path, "work/EPIC-001.md", _item("EPIC-001", "epic"))
    _write(tmp_path, "work/FEAT-001.md", _item("FEAT-001", "feature"))
    _write(tmp_path, f"work/{EVIL_DIR}/FEAT-001.md", _item("FEAT-001", "feature"))
    _commit(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def hdd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two boards: IDEA-R-001 twice (the copy under EVIL_DIR), and M-001 whose
    title holds ESC + newline (YAML double-quoted escapes)."""
    _init(tmp_path, TWO_BOARDS)
    _write(tmp_path, "work/expeditions/EXP-1.md", _item("EXP-1", "expedition"))
    idea = _item("IDEA-R-001", "idea", "An idea")
    _write(tmp_path, "research/ideas/IDEA-R-001-An-idea.md", idea)
    _write(tmp_path, f"research/ideas/{EVIL_DIR}/IDEA-R-001.md", idea)
    _write(
        tmp_path,
        "research/measures/M-001-x.md",
        _item("M-001", "measure", "T\\e[2J\\nFORGED [bold] \\\\x1b end"),
    )
    _commit(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _stderr(args: list[str]) -> str:
    result = CliRunner().invoke(main, args, color=True)
    assert result.exit_code == 1, (result.output, result.stderr, result.exception)
    return re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)  # colour codes only


def _assert_one_escaped_line(err: str, shown: str) -> None:
    """One `Error:` line, control characters escaped once, brackets verbatim."""
    assert "\x1b" not in err, f"raw ESC reached stderr: {err!r}"
    assert err.count("\n") == 1 and err.endswith("\n"), f"not one line: {err!r}"
    assert err.startswith("Error: "), repr(err)
    assert not any(line.startswith("FORGED") for line in err.splitlines()), repr(err)
    # escaped once: the literal `\x1b` stays 4 characters, not `\\x1b`
    assert shown in err, f"expected {shown!r} in {err!r}"
    assert "\\\\x1b" not in err, f"double-escaped: {err!r}"
    # plain text, not markup: `[bold]` printed as is, no `\[bold]`
    assert "[bold]" in err and "\\[bold]" not in err, repr(err)


# --- item 1: the pass-throughs --------------------------------------------------------


def test_group_handler_escapes_inputrefused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_click.Group.invoke`: `stats` lets `get_board`'s InputRefused through."""
    _init(tmp_path, SOFTWARE)
    monkeypatch.chdir(tmp_path)

    def refused(*_a: object, **_k: object) -> None:
        raise InputRefused(EVIL_MSG)

    monkeypatch.setattr(KanbanService, "get_board", refused)
    err = _stderr(["stats"])
    _assert_one_escaped_line(err, EVIL_MSG_SHOWN)
    assert err == f"Error: {EVIL_MSG_SHOWN}\n", repr(err)


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_epic_add_duplicate_refusal_escapes_path(software: Path, group: str) -> None:
    """`epic_commands._do_add`: the real `refuse_duplicate` names the EVIL_DIR copy."""
    err = _stderr([group, "add", "EPIC-001", "FEAT-001"])
    assert "is on more than one board" in err and "a link" in err, repr(err)
    _assert_one_escaped_line(err, f"work/{EVIL_DIR_SHOWN}/FEAT-001.md")


def test_hdd_parent_duplicate_refusal_escapes_path(hdd: Path) -> None:
    """`hdd_commands._refuse_duplicate_parent`: the real `refuse_duplicate` names the
    EVIL_DIR copy of the idea."""
    err = _stderr(["literature", "create", "lit", "--idea", "IDEA-R-001"])
    assert "is on more than one board" in err and "a parent link" in err, repr(err)
    _assert_one_escaped_line(err, f"research/ideas/{EVIL_DIR_SHOWN}/IDEA-R-001.md")


def test_hdd_render_refusal_escapes(hdd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`hdd_commands._render`: an InvalidTurtleName from the template engine."""

    def refused(*_a: object, **_k: object) -> str:
        raise InvalidTurtleName(EVIL_MSG)

    monkeypatch.setattr(TemplateEngine, "render", refused)
    err = _stderr(["measure", "create", "x", "--unit", "count", "--category", "coverage"])
    _assert_one_escaped_line(err, EVIL_MSG_SHOWN)
    assert err == f"Error: {EVIL_MSG_SHOWN}\n", repr(err)


def test_printable_refusal_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: printable text, backslashes and brackets included, is as the producer
    wrote it."""
    _init(tmp_path, SOFTWARE)
    monkeypatch.chdir(tmp_path)
    text = "plain C:\\dir [bold] 'q' \\n"

    def refused(*_a: object, **_k: object) -> None:
        raise InputRefused(text)

    monkeypatch.setattr(KanbanService, "get_board", refused)
    assert _stderr(["stats"]) == f"Error: {text}\n"


# --- item 2: the hdd escape sites, behaviourally --------------------------------------


def test_hdd_already_exists_escapes_title(hdd: Path) -> None:
    """`measure create --id M-001` against M-001 whose title holds ESC + newline."""
    err = _stderr(
        ["measure", "create", "x", "--unit", "count", "--category", "coverage", "--id", "M-001"]
    )
    assert "M-001 already exists: " in err, repr(err)
    _assert_one_escaped_line(err, "already exists: T\\x1b[2J\\nFORGED [bold] \\x1b end")


def test_hdd_failed_message_is_escaped(hdd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`measure create --push` whose push reports failure with ESC + newline."""

    def failed(*_a: object, **_k: object) -> dict[str, object]:
        return {"success": False, "message": EVIL_MSG}

    monkeypatch.setattr(KanbanService, "create_item_and_push", failed)
    err = _stderr(["measure", "create", "x", "--unit", "count", "--category", "coverage", "--push"])
    _assert_one_escaped_line(err, f"Failed: {EVIL_MSG_SHOWN}")
