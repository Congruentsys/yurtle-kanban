"""Issue #605 — comments get their own field; `description` stops carrying them.

Decided ([steer] on #605): the parser reads the `## Comments` section into
`item.comments` (author, time, text, in file order) and keeps it out of
`description`; `show` (human and `--json`), MCP `kanban_get_item` and search read the
new field; `update_item(description=...)` never touches the comments section. Also in
scope (round-3 review of PR #591): the parsed description has no trailing blank-line
run where the history block / comments used to follow it.

Fixtures are #583's (tests/issues/test_583_update_field_level.py): nautical LF/CRLF,
software, hdd native `active` and native `draft` + CRLF, each with a real `move`
history block, a body paragraph, hand-added frontmatter keys and one real comment;
here each gets a second, multi-line comment too.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_583_update_field_level import (
    BODY,
    FENCED_COMMENTS,
    FIXTURES,
    NO_H1,
    Item,
    _build,
    _custom,
    _git,
    _service,
)
from yurtle_kanban import query
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer

SECOND = "second comment line\n\nand a later paragraph"
HEADING_RE = re.compile(rb"^### (.+?) \((\d{4}-\d\d-\d\d \d\d:\d\d)\)\r?$", re.M)


@pytest.fixture(params=list(FIXTURES))
def item(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Item:
    it = _build(tmp_path, monkeypatch, request.param)
    _service(it.root).add_comment(it.id, SECOND, "carol", commit=False)
    _git(it.root, "commit", "-am", "second comment")
    return it


def _expected_comments(data: bytes) -> list[tuple[str, str, str]]:
    """(author, "YYYY-MM-DD HH:MM", text) per comment heading in the file."""
    heads = [(m.group(1).decode(), m.group(2).decode()) for m in HEADING_RE.finditer(data)]
    assert len(heads) == 2, data
    return [(heads[0][0], heads[0][1], "a note worth keeping"), (*heads[1], SECOND)]


def _got(comments: list) -> list[tuple[str, str, str]]:
    return [
        (c.author, c.created_at.strftime("%Y-%m-%d %H:%M"), c.content.replace("\r\n", "\n"))
        for c in comments
    ]


def _got_dicts(comments: list[dict]) -> list[tuple[str, str, str]]:
    return [
        (
            c["author"],
            datetime.fromisoformat(c["created_at"]).strftime("%Y-%m-%d %H:%M"),
            c["content"],
        )
        for c in comments
    ]


# ---------------------------------------------------------------------------
# 1. the parsed item
# ---------------------------------------------------------------------------


def test_description_excludes_comments(item: Item) -> None:
    parsed = _service(item.root).get_item(item.id)
    assert parsed is not None
    assert parsed.description == BODY, repr(parsed.description)


def test_comments_parsed_in_order(item: Item) -> None:
    parsed = _service(item.root).get_item(item.id)
    assert parsed is not None
    assert _got(parsed.comments) == _expected_comments(item.path.read_bytes())


# ---------------------------------------------------------------------------
# 2. the surfaces: show, show --json, MCP get_item, search
# ---------------------------------------------------------------------------


def test_show_json_has_comments_field(item: Item) -> None:
    result = CliRunner().invoke(main, ["show", item.id, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["description"] == BODY, repr(data["description"])
    assert _got_dicts(data["comments"]) == _expected_comments(item.path.read_bytes())


def test_show_human_lists_comments(item: Item) -> None:
    """test_179 pins that `show` prints comment text; it now comes from the Comments
    section (author + text), not a raw `## Comments` heading inside the description."""
    result = CliRunner().invoke(main, ["show", item.id])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "## Comments" not in out, out
    assert "### bob" not in out, out
    for text in ("a note worth keeping", "second comment line", "and a later paragraph"):
        assert out.count(text) == 1, out
    assert "bob" in out and "carol" in out, out
    assert out.index("a note worth keeping") < out.index("second comment line"), out


def test_mcp_get_item_has_comments_field(item: Item) -> None:
    got = KanbanMCPServer(repo_root=item.root)._get_item({"item_id": item.id})
    assert "item" in got, got
    assert got["item"]["description"] == BODY, repr(got["item"]["description"])
    assert _got_dicts(got["item"]["comments"]) == _expected_comments(item.path.read_bytes())


def test_search_text_includes_comments(item: Item, monkeypatch: pytest.MonkeyPatch) -> None:
    """Semantic search still sees the comments (read from the new field)."""
    monkeypatch.setattr(query, "_importable", lambda module: True)
    index = query.EmbeddingIndex()  # the model loads lazily; nothing is embedded here
    parsed = _service(item.root).get_item(item.id)
    assert parsed is not None
    index.add_item(parsed)
    (text,) = index._texts
    for part in (BODY, "a note worth keeping", "second comment line"):
        assert part in text, text


# ---------------------------------------------------------------------------
# 3. update_item(description=...) never touches the comments
# ---------------------------------------------------------------------------


def _comments_section(data: bytes) -> bytes:
    return data[data.index(b"## Comments") :]


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda d: "Edited body.", id="replaced"),
        pytest.param(lambda d: d + "\n\nOne more paragraph.", id="appended"),
        pytest.param(lambda d: "New first paragraph.\n\n" + d, id="prepended"),
    ],
)
def test_edited_description_keeps_comments_byte_identical(item: Item, edit) -> None:
    old = item.path.read_bytes()
    svc = _service(item.root)
    parsed = svc.get_item(item.id)
    assert parsed is not None and parsed.description is not None
    sent = edit(parsed.description)
    svc.update_item(item.id, description=sent, commit=False)
    new = item.path.read_bytes()
    assert new.count(b"## Comments") == 1, new.decode()
    assert new.endswith(_comments_section(old)), f"comments changed:\n{new.decode()}"
    reparsed = _service(item.root).get_item(item.id)
    assert reparsed is not None
    assert reparsed.description == sent, repr(reparsed.description)
    assert _got(reparsed.comments) == _expected_comments(old)


def test_mcp_read_modify_write_edited_description(item: Item) -> None:
    """The #591 reviewer's path: get_item, edit the description, send it back."""
    old = item.path.read_bytes()
    srv = KanbanMCPServer(repo_root=item.root)
    description = srv._get_item({"item_id": item.id})["item"]["description"]
    result = srv._update_item(
        {"item_id": item.id, "description": description + "\n\nAdded by an agent."}
    )
    assert result.get("success") is True, result
    new = item.path.read_bytes()
    assert new.count(b"## Comments") == 1, new.decode()
    assert new.endswith(_comments_section(old)), f"comments changed:\n{new.decode()}"


def test_parsed_description_sent_back_is_a_noop(item: Item) -> None:
    old = item.path.read_bytes()
    svc = _service(item.root)
    parsed = svc.get_item(item.id)
    assert parsed is not None
    svc.update_item(item.id, description=parsed.description, commit=False)
    assert item.path.read_bytes() == old


# ---------------------------------------------------------------------------
# 4. add_comment
# ---------------------------------------------------------------------------


def test_add_comment_appends_to_the_section(item: Item) -> None:
    old = item.path.read_bytes()
    _service(item.root).add_comment(item.id, "third one", "dave", commit=False)
    new = item.path.read_bytes()
    assert new.startswith(old), new.decode()
    assert new.count(b"## Comments") == 1, new.decode()
    parsed = _service(item.root).get_item(item.id)
    assert parsed is not None
    assert [(c.author, c.content) for c in parsed.comments] == [
        ("bob", "a note worth keeping"),
        ("carol", SECOND),
        ("dave", "third one"),
    ]
    assert parsed.description == BODY, repr(parsed.description)


def test_add_comment_with_fenced_comments_heading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `## Comments` line inside a code fence is body text, not the section."""
    it = _custom(tmp_path, monkeypatch, FENCED_COMMENTS)
    before = _service(it.root).get_item(it.id)
    assert before is not None
    assert before.comments == []
    body = before.description
    assert body is not None and "not real" in body
    _service(it.root).add_comment(it.id, "the real one", "dave", commit=False)
    data = it.path.read_bytes()
    assert data.count(b"## Comments") == 2, data.decode()  # the fenced one + the real one
    parsed = _service(it.root).get_item(it.id)
    assert parsed is not None
    assert [(c.author, c.content) for c in parsed.comments] == [("dave", "the real one")]
    assert parsed.description == body, repr(parsed.description)


# ---------------------------------------------------------------------------
# 5. no comments
# ---------------------------------------------------------------------------


def test_no_comments_is_empty_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    it = _custom(tmp_path, monkeypatch, NO_H1)
    parsed = _service(it.root).get_item(it.id)
    assert parsed is not None
    assert parsed.comments == []
    assert parsed.to_dict().get("comments") == []
    got = KanbanMCPServer(repo_root=it.root)._get_item({"item_id": it.id})
    assert got["item"]["comments"] == []
