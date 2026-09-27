"""#635: each comment on an item is a `kb:comment` node in the unified graph
(author, time, text), so SPARQL reaches comments again (#605 took them out of
`kb:description`); a preamble comment has no `kb:at` (#644)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from yurtle_kanban.models import Comment, WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.query import UnifiedGraph


def _item() -> WorkItem:
    item = WorkItem(
        id="FEAT-001", title="T", item_type=WorkItemType.FEATURE,
        status=WorkItemStatus.BACKLOG, file_path=Path("FEAT-001.md"),
    )
    item.comments = [
        Comment(content="typed by hand", author="", created_at=None),
        Comment(content="looks good", author="alice", created_at=datetime(2026, 9, 1, 10, 0)),
    ]
    return item


def test_comments_are_kb_comment_nodes():
    ug = UnifiedGraph()
    ug.add_item(_item())
    rows = ug.sparql(
        "SELECT ?author ?text WHERE { ?i kb:id 'FEAT-001' ; kb:comment ?c . "
        "?c kb:author ?author ; kb:text ?text } ORDER BY ?text"
    )
    assert rows == [
        {"author": "alice", "text": "looks good"},
        {"author": "", "text": "typed by hand"},
    ]


def test_comment_time_is_a_datetime_and_absent_for_the_preamble():
    ug = UnifiedGraph()
    ug.add_item(_item())
    rows = ug.sparql(
        "SELECT ?text ?at WHERE { ?i kb:comment ?c . ?c kb:text ?text . "
        "OPTIONAL { ?c kb:at ?at } } ORDER BY ?text"
    )
    assert rows[0]["text"] == "looks good" and rows[0]["at"].startswith("2026-09-01T10:00")
    assert rows[1] == {"text": "typed by hand", "at": ""}


def test_comment_text_is_searchable_by_sparql():
    ug = UnifiedGraph()
    ug.add_item(_item())
    rows = ug.sparql(
        "SELECT ?id WHERE { ?i kb:id ?id ; kb:comment ?c . ?c kb:text ?t . "
        "FILTER(CONTAINS(?t, 'good')) }"
    )
    assert rows == [{"id": "FEAT-001"}]


def test_a_block_cannot_forge_a_comment_on_another_item():
    """#635 review: `kb:comment` is owned by the markdown file's comments section,
    like `kb:description`; a fenced block in item B can't add one to item A."""
    from rdflib import Graph

    a = _item()
    b = WorkItem(
        id="FEAT-002", title="B", item_type=WorkItemType.FEATURE,
        status=WorkItemStatus.BACKLOG, file_path=Path("FEAT-002.md"),
    )
    b.graph = Graph().parse(
        data=(
            "@prefix kb: <https://yurtle.dev/kanban/> .\n"
            "@prefix item: <https://yurtle.dev/kanban/item/> .\n"
            'item:FEAT-001 kb:comment [ kb:author "mallory" ; kb:text "forged" ] .\n'
        ),
        format="turtle",
    )
    ug = UnifiedGraph()
    ug.add_items([a, b])
    rows = ug.sparql("SELECT ?author WHERE { ?i kb:id 'FEAT-001' ; kb:comment ?c . ?c kb:author ?author }")
    assert {r["author"] for r in rows} == {"alice", ""}, rows


def test_a_forging_blocks_blank_node_does_not_merge_as_an_orphan():
    """#726: the dropped kb:comment's blank node (author/text) doesn't merge at all,
    so a query not anchored on `?item kb:comment` can't find the forged text."""
    from rdflib import Graph

    b = WorkItem(
        id="FEAT-002", title="B", item_type=WorkItemType.FEATURE,
        status=WorkItemStatus.BACKLOG, file_path=Path("FEAT-002.md"),
    )
    b.graph = Graph().parse(
        data=(
            "@prefix kb: <https://yurtle.dev/kanban/> .\n"
            "@prefix item: <https://yurtle.dev/kanban/item/> .\n"
            'item:FEAT-001 kb:comment [ kb:author "mallory" ; kb:text "forged" ] .\n'
        ),
        format="turtle",
    )
    ug = UnifiedGraph()
    ug.add_items([_item(), b])
    rows = ug.sparql("SELECT ?t WHERE { ?c kb:text ?t }")
    assert "forged" not in {r["t"] for r in rows}, rows


def test_end_to_end_comment_is_queryable(tmp_path, monkeypatch):
    """#726: `comment --body` then a SPARQL query over the scanned board."""
    import subprocess

    from click.testing import CliRunner

    from yurtle_kanban.cli import main

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, v in (("user.name", "T"), ("user.email", "t@t")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init", "--theme", "software"]).exit_code == 0
    made = runner.invoke(main, ["create", "feature", "Graph me"])
    assert made.exit_code == 0, made.output
    said = runner.invoke(main, ["comment", "FEAT-001", "--body", "lookup-token", "--agent", "alice"])
    assert said.exit_code == 0, said.output
    out = runner.invoke(
        main,
        ["query", "--no-semantic", "--sparql",
         "SELECT ?a WHERE { ?i kb:id 'FEAT-001' ; kb:comment ?c . ?c kb:author ?a ; "
         "kb:text ?t . FILTER(CONTAINS(?t, 'lookup-token')) }"],
    )
    assert out.exit_code == 0, out.output
    assert "alice" in out.output, out.output
