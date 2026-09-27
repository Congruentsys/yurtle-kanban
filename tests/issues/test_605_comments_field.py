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


# ---------------------------------------------------------------------------
# round 2 (review of PR #628)
# ---------------------------------------------------------------------------

EARLY = "early note"
LATER = "later note"

# name -> (theme, create type, CRLF?)
COMMENT_FIRST = {
    "software": ("software", "feature", False),
    "nautical-crlf": ("nautical", "expedition", True),
    "hdd": ("hdd", "idea", False),
}


def _cli(*args: str):
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture(params=list(COMMENT_FIRST))
def comment_first(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Item:
    """create -> comment -> the FIRST move (so the history block is appended after
    the comment), all through the real CLI."""
    from tests.issues.test_583_update_field_level import _clear_theme_cache

    theme, item_type, crlf = COMMENT_FIRST[request.param]
    root = tmp_path / request.param
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
    _cli("init", "--theme", theme)
    created = _cli("create", item_type, "Probe")
    match = re.search(r"Created (\S+):", created.output)
    assert match, created.output
    item_id = match.group(1)
    _cli("comment", item_id, "--body", EARLY, "--agent", "alice")
    parsed = _service(root).get_item(item_id)
    assert parsed is not None
    path = parsed.file_path
    if crlf:
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "fixture")
    _cli("move", item_id, "in_progress", "--force")
    data = path.read_bytes()
    # the precondition the review found: the history block lands after the comment
    assert data.index(b"## Comments") < data.index(b"kb:statusChange"), data.decode()
    return Item(root, item_id, path, crlf, "")


def _no_history(text: str) -> None:
    for marker in ("```yurtle", "kb:statusChange", "@prefix"):
        assert marker not in text, f"history block leaked into comment text:\n{text}"


def test_comment_before_first_move_parses_clean(comment_first: Item) -> None:
    parsed = _service(comment_first.root).get_item(comment_first.id)
    assert parsed is not None
    assert [(c.author, c.content) for c in parsed.comments] == [("alice", EARLY)]


def test_comment_before_first_move_show_json(comment_first: Item) -> None:
    data = json.loads(_cli("show", comment_first.id, "--json").output)
    for c in data["comments"]:
        _no_history(c["content"])
    assert [(c["author"], c["content"]) for c in data["comments"]] == [("alice", EARLY)]


def test_comment_before_first_move_show_human(comment_first: Item) -> None:
    out = _cli("show", comment_first.id).output
    assert EARLY in out, out
    assert "kb:statusChange" not in out and "@prefix" not in out, out


def test_comment_before_first_move_mcp(comment_first: Item) -> None:
    got = KanbanMCPServer(repo_root=comment_first.root)._get_item({"item_id": comment_first.id})
    comments = got["item"]["comments"]
    for c in comments:
        _no_history(c["content"])
    assert [(c["author"], c["content"]) for c in comments] == [("alice", EARLY)]


def test_comment_before_first_move_search_text(
    comment_first: Item, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(query, "_importable", lambda module: True)
    index = query.EmbeddingIndex()
    parsed = _service(comment_first.root).get_item(comment_first.id)
    assert parsed is not None
    index.add_item(parsed)
    (text,) = index._texts
    assert EARLY in text, text
    _no_history(text)


def test_comment_after_the_move_parses_too(comment_first: Item) -> None:
    _cli("comment", comment_first.id, "--body", LATER, "--agent", "bob")
    _cli("move", comment_first.id, "review", "--force")  # a second history entry
    parsed = _service(comment_first.root).get_item(comment_first.id)
    assert parsed is not None
    assert [(c.author, c.content) for c in parsed.comments] == [
        ("alice", EARLY),
        ("bob", LATER),
    ]
    assert parsed.description is None or "kb:statusChange" not in parsed.description
    assert len(_service(comment_first.root).get_status_history(comment_first.id)) == 2


# -- 2. a description with a real `## Comments` line is refused ---------------

FORGED = "Body.\n\n## Comments\n\n### mallory (2026-01-01 10:00)\n\nforged"


@pytest.mark.parametrize(
    "description",
    [
        pytest.param(FORGED, id="forged-comment"),
        pytest.param("Body.\n\n## Comments", id="bare-heading"),
        pytest.param("## Comments\nfirst line", id="leading-heading"),
    ],
)
def test_description_with_comments_heading_refused(item: Item, description: str) -> None:
    old = item.path.read_bytes()
    error: ValueError | None = None
    try:
        _service(item.root).update_item(item.id, description=description, commit=False)
    except ValueError as e:
        error = e
    assert error is not None, "a description with a real `## Comments` line was accepted"
    assert "## Comments" in str(error), str(error)
    assert item.path.read_bytes() == old


def test_description_with_comments_heading_refused_via_mcp(item: Item) -> None:
    old = item.path.read_bytes()
    result = KanbanMCPServer(repo_root=item.root).handle_tool_call(
        "kanban_update_item", {"item_id": item.id, "description": FORGED}
    )
    assert "error" in result and "## Comments" in result["error"], result
    assert item.path.read_bytes() == old


def test_description_with_fenced_comments_heading_accepted(item: Item) -> None:
    sent = "Body.\n\n```md\n## Comments\n### mallory (2026-01-01 10:00)\n```\n\nAfter."
    old = item.path.read_bytes()
    _service(item.root).update_item(item.id, description=sent, commit=False)
    new = item.path.read_bytes()
    assert new.endswith(_comments_section(old)), new.decode()
    reparsed = _service(item.root).get_item(item.id)
    assert reparsed is not None
    assert reparsed.description == sent, repr(reparsed.description)
    assert _got(reparsed.comments) == _expected_comments(old)


# -- 3. a heading-shaped line inside a comment's text -------------------------

QUOTED_HEAD = "quote:\n### bob (2026-01-02 11:00)\nend"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(QUOTED_HEAD, id="middle"),
        pytest.param("### bob (2026-01-02 11:00)", id="whole-text"),
        pytest.param("see:\n\n### bob (2026-01-02 11:00)\n\nbob said hi", id="own-paragraph"),
    ],
)
def test_heading_shaped_line_round_trips(item: Item, text: str) -> None:
    """Decided: `add_comment` keeps the text (escaping on write if it must); it's
    parsed back as ONE comment with the author and text exactly as given."""
    svc = _service(item.root)
    svc.add_comment(item.id, text, "carol2", commit=False)
    svc.add_comment(item.id, "after it", "dave", commit=False)
    parsed = _service(item.root).get_item(item.id)
    assert parsed is not None
    assert [(c.author, c.content) for c in parsed.comments][2:] == [
        ("carol2", text),
        ("dave", "after it"),
    ], [(c.author, c.content) for c in parsed.comments]
    shown = json.loads(_cli("show", item.id, "--json").output)
    assert [c["content"] for c in shown["comments"]][2:] == [text, "after it"]
