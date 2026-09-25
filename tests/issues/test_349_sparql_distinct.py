"""#349: structured_query must return each matching item at most once.

``QueryEngine.structured_query`` emits ``SELECT ?id`` without DISTINCT, and
``UnifiedGraph.add_item`` merges each item file's own RDF graph (fenced
```yurtle/```turtle blocks) into the unified graph. So an item that matches a
multi-valued pattern more than one way yields one solution row per match and
comes back twice:

(a) its yurtle block adds a second ``rdf:type`` that is also in the type filter;
(b) it has two tags that both contain the tag needle;
(c) it has two ``kb:assignee`` values that both contain the assignee needle —
    reachable: frontmatter ``assignee`` plus a ``kb:assignee`` triple in the block.

Note: a ``<>`` subject in the block resolves to the repo directory's file URI,
not the item's URI, so the extra triples name ``item:H-004`` explicitly.

Decided behaviour: each item at most once, still in ``numericId``-descending order.

Red before the fix: (a), (b), (c) through QueryEngine and through CLI
``query --no-semantic --json``. Controls (green before and after): single-match
queries, and ORDER BY DESC(?numId) across several items.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import get_service, main
from yurtle_kanban.query import ParsedQuery, QueryEngine

DUAL = "H-004"

_YURTLE_BLOCK = """
```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix item: <https://yurtle.dev/kanban/item/> .
item:H-004 a kb:Paper ;
    kb:assignee "Mini-2" .
```
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """hdd repo: PAPER-001..003 and one hypothesis H-004 that matches twice.

    PAPER-001 is assigned to Mini (one assignee match); PAPER-002 is tagged
    ``brainstorm`` (one tag match). H-004 is tagged ``brain-a,brain-b``, assigned
    to Mini in frontmatter and ``Mini-2`` in its yurtle block, and typed kb:Paper
    in the block on top of kb:Hypothesis.
    """
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=tmp_path, capture_output=True, check=True)
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    for args in (
        ["init", "--theme", "hdd"],
        ["create", "paper", "First paper", "--assignee", "Mini"],
        ["create", "paper", "Second paper", "--tags", "brainstorm"],
        ["create", "paper", "Third paper"],
        ["create", "hypothesis", "Dual item", "--tags", "brain-a,brain-b", "--assignee", "Mini"],
    ):
        result = runner.invoke(main, args)
        assert result.exit_code == 0, f"fixture step {args} failed: {result.output}"

    # Give the hypothesis a numericId above every paper so ordering is unambiguous.
    hyp_dir = tmp_path / "research" / "hypotheses"
    (src,) = hyp_dir.glob("H-001-*.md")
    text = src.read_text(encoding="utf-8")
    assert "id: H-001\n" in text
    text = text.replace("id: H-001\n", f"id: {DUAL}\n", 1) + _YURTLE_BLOCK
    (hyp_dir / f"{DUAL}-Dual-item.md").write_text(text, encoding="utf-8")
    src.unlink()
    return tmp_path


@pytest.fixture
def engine(repo: Path) -> QueryEngine:
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    dual = eng._ug.get_item(DUAL)
    assert dual is not None, "fixture: H-004 not scanned"
    assert dual.graph is not None and len(dual.graph) > 0, "fixture: yurtle block not parsed"
    return eng


def _engine_ids(engine: QueryEngine, parsed: ParsedQuery) -> list[str]:
    return [i.id for i in engine.structured_query(parsed)]


def _cli_ids(query: str) -> list[str]:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "--json", query])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    return [row["id"] for row in json.loads(result.output)]


def _assert_unique(ids: list[str]) -> None:
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


# ---------------------------------------------------------------------------
# RED before the fix: an item matching twice comes back twice
# ---------------------------------------------------------------------------


def test_engine_second_rdf_type_in_filter(engine: QueryEngine) -> None:
    ids = _engine_ids(engine, ParsedQuery(type_filter=["paper", "hypothesis"]))
    _assert_unique(ids)
    assert ids == [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]


def test_engine_two_matching_tags(engine: QueryEngine) -> None:
    ids = _engine_ids(engine, ParsedQuery(tag="brain"))
    _assert_unique(ids)
    assert ids == [DUAL, "PAPER-002"]


def test_engine_two_matching_assignees(engine: QueryEngine) -> None:
    ids = _engine_ids(engine, ParsedQuery(assignee="mini"))
    _assert_unique(ids)
    assert ids == [DUAL, "PAPER-001"]


def test_engine_all_three_combined(engine: QueryEngine) -> None:
    # 2 types x 2 tags x 2 assignees = 8 rows for H-004 today
    parsed = ParsedQuery(type_filter=["paper", "hypothesis"], tag="brain", assignee="mini")
    assert _engine_ids(engine, parsed) == [DUAL]


def test_cli_second_rdf_type_in_filter(repo: Path) -> None:
    ids = _cli_ids("papers with pending hypotheses")
    _assert_unique(ids)
    assert ids == [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]


def test_cli_two_matching_tags(repo: Path) -> None:
    ids = _cli_ids("items tagged brain")
    _assert_unique(ids)
    assert ids == [DUAL, "PAPER-002"]


def test_cli_two_matching_assignees(repo: Path) -> None:
    ids = _cli_ids("items assigned to mini")
    _assert_unique(ids)
    assert ids == [DUAL, "PAPER-001"]


# ---------------------------------------------------------------------------
# Controls (GREEN before and after)
# ---------------------------------------------------------------------------


def test_control_single_type_order(engine: QueryEngine) -> None:
    # one type in the filter binds ?type once: H-004 (kb:Paper via its block) once
    ids = _engine_ids(engine, ParsedQuery(type_filter=["paper"]))
    assert ids == [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]


def test_control_single_type_hypothesis(engine: QueryEngine) -> None:
    assert _engine_ids(engine, ParsedQuery(type_filter=["hypothesis"])) == [DUAL]


def test_control_single_tag_match(engine: QueryEngine) -> None:
    assert _engine_ids(engine, ParsedQuery(tag="brainstorm")) == ["PAPER-002"]
    assert _engine_ids(engine, ParsedQuery(tag="brain-a")) == [DUAL]


def test_control_single_assignee_match(engine: QueryEngine) -> None:
    assert _engine_ids(engine, ParsedQuery(assignee="mini-2")) == [DUAL]


def test_control_status_only_order(engine: QueryEngine) -> None:
    ids = _engine_ids(engine, ParsedQuery(status_include=["backlog"]))
    assert ids == [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]


def test_control_cli_single_tag(repo: Path) -> None:
    assert _cli_ids("items tagged brainstorm") == ["PAPER-002"]


def test_control_cli_backlog_order(repo: Path) -> None:
    assert _cli_ids("backlog items") == [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]
