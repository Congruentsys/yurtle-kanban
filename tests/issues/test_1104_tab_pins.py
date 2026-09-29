"""#1104: pin the two halves of the #1093 tab/CRLF ruling that only the description pinned.

[steer] on #1093: in multi-line text (description, comments) a tab and a CRLF line end
stay whitespace; single-line fields still show a tab as `\\t`, as `safe()` does.

1. A comment body keeps a tab as whitespace and a CRLF line end as a line break, while
   ESC on the same line is still escaped (`_safe_lines`, not per-line `safe()`).
   #1109: the tab renders as whitespace (not dropped), and `_safe_lines`' own CRLF
   step is pinned directly — `_parse_text` already normalises CRLF, so no file does.
2. A tab in a title (`show`, `list`) or an assignee (`list`) shows as `\\t` — a
   single-line field is not routed through `_safe_lines`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.issues import test_1093_more_display_sites as base
from yurtle_kanban import config as config_mod
from yurtle_kanban._click import safe
from yurtle_kanban.board import _safe_lines


@pytest.fixture(autouse=True)
def _clean() -> None:
    config_mod._theme_cache.clear()


# --- 1. comment body: tab and CRLF stay whitespace -------------------------------------


def test_show_comment_keeps_tabs_and_crlf_as_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = base._repo(tmp_path)
    base._item(repo, "FEAT-001", {"title": '"t"', "status": "backlog"})
    path = repo / "work" / "FEAT-001.md"
    body = (
        "desc\r\n"
        "\r\n"
        "## Comments\r\n"
        "\r\n"
        "### alice (2026-09-25 10:00)\r\n"
        "\r\n"
        "cmt\tcol \x1b[2J\r\n"
        "cmtnext line\r\n"
    )
    path.write_bytes(path.read_bytes() + body.encode())
    out = base._run(repo, ["show", "FEAT-001"], monkeypatch)
    base._no_raw_control(out)
    col = base._line_with(out, "cmt")
    assert "cmt" in col and "\\t" not in col, repr(out)
    # #1109: the tab is whitespace between the words, not dropped (`cmtcol`)
    assert re.search(r"cmt\s+col", col), repr(out)
    assert "\\r" not in out, repr(out)
    assert "col \\x1b[2J" in col, repr(out)
    assert "cmtnext line" not in col, repr(out)
    assert base._line_with(out, "cmtnext line").startswith("    cmtnext line"), repr(out)


# --- 1b. `_safe_lines` directly: the CRLF step no file-based test reaches (#1109) ----


def test_safe_lines_keeps_tab_folds_crlf_and_escapes_esc() -> None:
    got = _safe_lines("a\tb\r\nc\x1b[2J")
    assert got == "a\tb\nc\\x1b[2J", repr(got)
    # the same as `safe()` on each tab/line-separated part
    assert got == "\t".join([safe("a"), safe("b")]) + "\n" + safe("c\x1b[2J"), repr(got)
    assert "\r" not in got and "\\r" not in got, repr(got)


def test_safe_lines_escapes_a_lone_cr() -> None:
    got = _safe_lines("a\rb")
    assert got == "a\\rb", repr(got)
    assert "\r" not in got, repr(got)


# --- 2. single-line fields: a tab shows as `\t` ----------------------------------------

TITLE_YAML = '"Tab\\there"'  # YAML escape: a real tab in the parsed title
TITLE_SHOWN = "Tab\\there"
ASSIGNEE_YAML = '"bo\\tb"'
ASSIGNEE_SHOWN = "bo\\tb"


@pytest.fixture
def tab_repo(tmp_path: Path) -> Path:
    repo = base._repo(tmp_path)
    base._item(
        repo,
        "FEAT-001",
        {"title": TITLE_YAML, "status": "backlog", "assignee": ASSIGNEE_YAML},
    )
    return repo


def test_show_title_tab_is_escaped(tab_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = base._run(tab_repo, ["show", "FEAT-001"], monkeypatch)
    assert TITLE_SHOWN in out, repr(out)
    assert "\t" not in out, repr(out)


def test_list_title_and_assignee_tab_are_escaped(
    tab_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = base._run(tab_repo, ["list"], monkeypatch)
    assert TITLE_SHOWN in out, repr(out)
    assert ASSIGNEE_SHOWN in out, repr(out)
    assert "\t" not in out, repr(out)
