"""Issue #644 — follow-ups to #605's comments field.

Decided ([steer] on #644):
1. `create --body` and MCP `kanban_create_item` refuse a `## Comments` line outside a
   fence, with the same check and message as `update_item`.
2. Quoted Turtle in comments: out of scope, behaviour unchanged; documented in the
   `add_comment` docstring (and README).
3. Comment text starting with `\\###` / `\\\\###` round-trips exactly.
4. Text between `## Comments` and the first `###` heading is never dropped: it reads as
   a leading comment with an empty author and no date (item, `show`, JSON, search), and
   survives every write unchanged on disk.
5. Human `show` indents a multi-line comment's continuation lines to its text column.

Helpers and fixtures are #605's (tests/issues/test_605_comments_field.py), which reuse
#583's.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_583_update_field_level import (
    Item,
    _build,
    _clear_theme_cache,
    _git,
    _service,
)
from tests.issues.test_605_comments_field import SECOND, item  # noqa: F401  (fixture)
from yurtle_kanban import query
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService


def _flat(text: str) -> str:
    """Output with Rich's line wrapping undone."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# 1. create refuses a forged comments section
# ---------------------------------------------------------------------------

FORGED = "intro\n\n## Comments\n\n### mallory (2026-01-01 00:00)\n\nforged"
FENCED = "intro\n\n```md\n## Comments\n### mallory (2026-01-01 00:00)\n```\n\nafter"
PLAIN = "intro\n\nJust a plain body."


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty software board, everything committed."""
    root = tmp_path / "board"
    root.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "test@test.com"),
        ("config", "user.name", "Test"),
        ("commit", "--allow-empty", "-m", "init"),
    ):
        _git(root, *args)
    _clear_theme_cache()
    monkeypatch.chdir(root)
    result = CliRunner().invoke(main, ["init", "--theme", "software"])
    assert result.exit_code == 0, result.output
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "board")
    return root


def _files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts}


def _clean(root: Path) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status == "", f"the refused create left changes:\n{status}"


@pytest.mark.parametrize(
    ("extra", "stdin"),
    [
        pytest.param(["--body", FORGED], None, id="body"),
        pytest.param(["--body-file", "-"], FORGED, id="body-file"),
        pytest.param(["--push", "--body", FORGED], None, id="push"),
    ],
)
def test_cli_create_with_forged_comments_refused(
    board: Path, extra: list[str], stdin: str | None
) -> None:
    before = _files(board)
    result = CliRunner().invoke(main, ["create", "feature", "Forge", *extra], input=stdin)
    assert result.exit_code != 0, f"forged comments accepted:\n{result.output}"
    assert "## Comments" in _flat(result.output), (
        f"the refusal doesn't name the heading:\n{result.output!r} {result.exception!r}"
    )
    assert _files(board) == before, sorted(map(str, _files(board) - before))
    _clean(board)
    assert _service(board).get_items() == []


def test_service_create_with_forged_comments_refused(board: Path) -> None:
    """Same check and message as update_item (#605)."""
    from yurtle_kanban.models import WorkItemType

    before = _files(board)
    with pytest.raises(ValueError, match="## Comments") as info:
        _service(board).create_item(WorkItemType.FEATURE, "Forge", description=FORGED)
    assert "outside a code block" in str(info.value), str(info.value)
    assert _files(board) == before
    _clean(board)


def test_mcp_create_with_forged_comments_refused(board: Path) -> None:
    before = _files(board)
    result = KanbanMCPServer(repo_root=board).handle_tool_call(
        "kanban_create_item",
        {"item_type": "feature", "title": "Forge", "description": FORGED},
    )
    assert "error" in result and "## Comments" in result["error"], result
    assert result.get("success") is not True, result
    assert _files(board) == before, sorted(map(str, _files(board) - before))
    _clean(board)


def _created_id(output: str) -> str:
    match = re.search(r"Created (\S+):", output)
    assert match, output
    return match.group(1)


@pytest.mark.parametrize(
    "body", [pytest.param(FENCED, id="fenced"), pytest.param(PLAIN, id="plain")]
)
def test_cli_create_accepts_fenced_or_plain(board: Path, body: str) -> None:
    result = CliRunner().invoke(main, ["create", "feature", "Fine", "--body", body])
    assert result.exit_code == 0, result.output
    parsed = _service(board).get_item(_created_id(result.output))
    assert parsed is not None
    assert parsed.description == body, repr(parsed.description)
    assert parsed.comments == []


@pytest.mark.parametrize(
    "body", [pytest.param(FENCED, id="fenced"), pytest.param(PLAIN, id="plain")]
)
def test_mcp_create_accepts_fenced_or_plain(board: Path, body: str) -> None:
    result = KanbanMCPServer(repo_root=board).handle_tool_call(
        "kanban_create_item", {"item_type": "feature", "title": "Fine", "description": body}
    )
    assert result.get("success") is True, result
    parsed = _service(board).get_item(result["item"]["id"])
    assert parsed is not None
    assert parsed.description == body, repr(parsed.description)
    assert parsed.comments == []


# ---------------------------------------------------------------------------
# 2. quoted Turtle in comments: out of scope, documented
# ---------------------------------------------------------------------------


def test_add_comment_docstring_documents_knowledge_fences() -> None:
    doc = KanbanService.add_comment.__doc__ or ""
    assert re.search(r"yurtle|turtle", doc, re.I), (
        f"add_comment's docstring doesn't say knowledge fences are stripped:\n{doc}"
    )


# ---------------------------------------------------------------------------
# 3. escaped-heading round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("\\###", id="one-backslash"),
        pytest.param("\\\\###", id="two-backslashes"),
        pytest.param("\\### not a heading\nmore", id="one-backslash-text"),
        pytest.param("\\\\### bob (2026-01-02 11:00)\nmore", id="two-backslashes-head"),
    ],
)
def test_escaped_heading_text_round_trips(item: Item, text: str) -> None:  # noqa: F811
    svc = _service(item.root)
    svc.add_comment(item.id, text, "erin", commit=False)
    svc.add_comment(item.id, "after it", "dave", commit=False)
    parsed = _service(item.root).get_item(item.id)
    assert parsed is not None
    got = [(c.author, c.content.replace("\r\n", "\n")) for c in parsed.comments][2:]
    assert got == [("erin", text), ("dave", "after it")], got
    shown = json.loads(CliRunner().invoke(main, ["show", item.id, "--json"]).output)
    assert [c["content"] for c in shown["comments"]][2:] == [text, "after it"]


# ---------------------------------------------------------------------------
# 4. text before the first comment heading
# ---------------------------------------------------------------------------

PREAMBLE = "Orphan note before any heading.\nits second line"


@pytest.fixture(params=["nautical", "hdd-draft-crlf"])
def preamble(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Item:
    it = _build(tmp_path, monkeypatch, request.param)
    data = it.path.read_bytes()
    eol = b"\r\n" if it.crlf else b"\n"
    head = b"## Comments" + eol
    at = data.index(head) + len(head)
    inserted = eol + PREAMBLE.encode().replace(b"\n", eol) + eol
    it.path.write_bytes(data[:at] + inserted + data[at:])
    _git(it.root, "commit", "-am", "preamble")
    return it


def _section_head(data: bytes) -> bytes:
    """From `## Comments` up to the first `### ` comment heading."""
    start = data.index(b"## Comments")
    return data[start : data.index(b"\n### ", start)]


def _check_preamble_comment(comments: list) -> None:
    assert comments, "no comments parsed"
    first = comments[0]
    assert (first.author, first.content.replace("\r\n", "\n")) == ("", PREAMBLE), [
        (c.author, c.content) for c in comments
    ]
    assert first.created_at is None, first.created_at
    assert [c.author for c in comments[1:]] == ["bob"], [c.author for c in comments]


def test_preamble_is_a_leading_comment(preamble: Item) -> None:
    parsed = _service(preamble.root).get_item(preamble.id)
    assert parsed is not None
    _check_preamble_comment(parsed.comments)
    assert "Orphan note" not in (parsed.description or ""), parsed.description


def test_preamble_in_show_json(preamble: Item) -> None:
    result = CliRunner().invoke(main, ["show", preamble.id, "--json"])
    assert result.exit_code == 0, result.output
    comments = json.loads(result.output)["comments"]
    assert comments[0] == {"content": PREAMBLE, "author": "", "created_at": None}, comments
    assert [c["author"] for c in comments[1:]] == ["bob"], comments


def test_preamble_in_mcp_get_item(preamble: Item) -> None:
    got = KanbanMCPServer(repo_root=preamble.root)._get_item({"item_id": preamble.id})
    comments = got["item"]["comments"]
    assert comments[0] == {"content": PREAMBLE, "author": "", "created_at": None}, comments


def test_preamble_in_show_human(preamble: Item) -> None:
    result = CliRunner().invoke(main, ["show", preamble.id])
    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
    out = result.output
    assert "Orphan note before any heading." in out and "its second line" in out, out
    assert out.index("Orphan note") < out.index("a note worth keeping"), out


def test_preamble_in_search_text(preamble: Item, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query, "_importable", lambda module: True)
    index = query.EmbeddingIndex()
    parsed = _service(preamble.root).get_item(preamble.id)
    assert parsed is not None
    index.add_item(parsed)
    (text,) = index._texts
    assert "Orphan note before any heading." in text, text


def test_preamble_survives_add_comment(preamble: Item) -> None:
    old = preamble.path.read_bytes()
    _service(preamble.root).add_comment(preamble.id, "a later one", "dave", commit=False)
    new = preamble.path.read_bytes()
    assert new.startswith(old), new.decode()
    assert _section_head(new) == _section_head(old), new.decode()
    parsed = _service(preamble.root).get_item(preamble.id)
    assert parsed is not None
    assert [(c.author, c.content.replace("\r\n", "\n")) for c in parsed.comments] == [
        ("", PREAMBLE),
        ("bob", "a note worth keeping"),
        ("dave", "a later one"),
    ]


def test_preamble_survives_move(preamble: Item) -> None:
    old = preamble.path.read_bytes()
    _service(preamble.root).move_item(
        preamble.id,
        WorkItemStatus.REVIEW,
        commit=False,
        validate_workflow=False,
        skip_wip_check=True,
        skip_gates=True,
    )
    new = preamble.path.read_bytes()
    assert new != old, "the move changed nothing"
    assert _section_head(new) == _section_head(old), new.decode()
    parsed = _service(preamble.root).get_item(preamble.id)
    assert parsed is not None
    _check_preamble_comment(parsed.comments)


# ---------------------------------------------------------------------------
# 5. human show indents multi-line comments
# ---------------------------------------------------------------------------

THREE = "alpha line\nbeta line\n\ngamma line"


def test_show_indents_continuation_lines(item: Item) -> None:  # noqa: F811
    _service(item.root).add_comment(item.id, THREE, "erin", commit=False)
    result = CliRunner().invoke(main, ["show", item.id])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()

    def col(text: str) -> int:
        (line,) = [ln for ln in lines if text in ln]
        return line.index(text)

    for first, rest in (
        ("second comment line", ["and a later paragraph"]),  # SECOND, from #605
        ("alpha line", ["beta line", "gamma line"]),
    ):
        indent = col(first)
        assert indent > 0, result.output
        for text in rest:
            assert col(text) == indent, (
                f"{text!r} starts at column {col(text)}, the text column is {indent}:\n"
                + result.output
            )
    assert SECOND.startswith("second comment line")
