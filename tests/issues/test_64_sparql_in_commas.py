"""#64: structured_query must build comma-separated SPARQL ``IN`` lists.

``QueryEngine.structured_query`` joined the values of ``FILTER(?status IN (...))``
and ``FILTER(?type IN (...))`` with spaces. SPARQL requires commas between the
expressions in an ``IN`` list, so any query naming 2+ types or 2+ included statuses
raised ``pyparsing.ParseException: Expected SelectQuery, found 'FILTER'``. Example:
``query --no-semantic "papers with pending hypotheses"`` decomposes to types
[paper, hypothesis] and crashed with a traceback.

Red before the fix: the CLI query, and 2+ types / 2+ statuses through the engine.
Controls (green before and after): one type, one status, and status exclusions.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.query import NLDecomposer, ParsedQuery, QueryEngine, UnifiedGraph

NL_QUERY = "papers with pending hypotheses"

# ---------------------------------------------------------------------------
# Engine fixtures
# ---------------------------------------------------------------------------

_ITEMS = [
    ("PAPER-001", WorkItemType.PAPER, WorkItemStatus.BACKLOG),
    ("PAPER-002", WorkItemType.PAPER, WorkItemStatus.DONE),
    ("H-003", WorkItemType.HYPOTHESIS, WorkItemStatus.IN_PROGRESS),
    ("EXPR-004", WorkItemType.EXPERIMENT, WorkItemStatus.REVIEW),
    ("EXP-005", WorkItemType.EXPEDITION, WorkItemStatus.BLOCKED),
    ("EXP-006", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
]


def _item(item_id: str, item_type: WorkItemType, status: WorkItemStatus) -> WorkItem:
    return WorkItem(
        id=item_id,
        title=f"Item {item_id}",
        item_type=item_type,
        status=status,
        file_path=Path(f"/tmp/{item_id}.md"),
        priority="medium",
        created=date(2026, 1, 1),
    )


@pytest.fixture
def engine() -> QueryEngine:
    ug = UnifiedGraph()
    ug.add_items([_item(*spec) for spec in _ITEMS])
    return QueryEngine(unified_graph=ug, embedding_index=None)


def _ids(engine: QueryEngine, parsed: ParsedQuery) -> list[str]:
    items = engine.structured_query(parsed)
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids)), f"duplicate rows for {parsed}: {ids}"
    return ids


# ---------------------------------------------------------------------------
# New behaviour (RED before the fix): 2+ values in an IN list
# ---------------------------------------------------------------------------


def test_two_types(engine: QueryEngine) -> None:
    parsed = ParsedQuery(type_filter=["paper", "hypothesis"])
    assert set(_ids(engine, parsed)) == {"PAPER-001", "PAPER-002", "H-003"}


def test_two_types_ordered_by_numeric_id_desc(engine: QueryEngine) -> None:
    parsed = ParsedQuery(type_filter=["paper", "hypothesis"])
    assert _ids(engine, parsed) == ["H-003", "PAPER-002", "PAPER-001"]


def test_two_status_includes(engine: QueryEngine) -> None:
    parsed = ParsedQuery(status_include=["in_progress", "backlog"])
    assert set(_ids(engine, parsed)) == {"PAPER-001", "H-003", "EXP-006"}


def test_three_types(engine: QueryEngine) -> None:
    parsed = ParsedQuery(type_filter=["paper", "hypothesis", "experiment"])
    assert set(_ids(engine, parsed)) == {"PAPER-001", "PAPER-002", "H-003", "EXPR-004"}


def test_two_types_and_two_statuses(engine: QueryEngine) -> None:
    parsed = ParsedQuery(
        type_filter=["paper", "expedition"],
        status_include=["in_progress", "backlog"],
    )
    assert set(_ids(engine, parsed)) == {"PAPER-001", "EXP-006"}


def test_two_types_with_status_exclusion(engine: QueryEngine) -> None:
    parsed = ParsedQuery(type_filter=["paper", "hypothesis"], status_filter=["done"])
    assert set(_ids(engine, parsed)) == {"PAPER-001", "H-003"}


def test_engine_nl_query_two_types(engine: QueryEngine) -> None:
    assert NLDecomposer().parse(NL_QUERY).type_filter == ["paper", "hypothesis"]
    ids = {r.item.id for r in engine.query(NL_QUERY)}
    assert ids == {"PAPER-001", "PAPER-002", "H-003"}


def test_engine_nl_query_two_statuses(engine: QueryEngine) -> None:
    nl = "backlog or in progress items"
    assert sorted(NLDecomposer().parse(nl).status_include) == ["backlog", "in_progress"]
    ids = {r.item.id for r in engine.query(nl)}
    assert ids == {"PAPER-001", "H-003", "EXP-006"}


# ---------------------------------------------------------------------------
# Controls (GREEN before and after): single values and exclusions
# ---------------------------------------------------------------------------


def test_control_one_type(engine: QueryEngine) -> None:
    parsed = ParsedQuery(type_filter=["paper"])
    assert _ids(engine, parsed) == ["PAPER-002", "PAPER-001"]


def test_control_one_status(engine: QueryEngine) -> None:
    parsed = ParsedQuery(status_include=["in_progress"])
    assert set(_ids(engine, parsed)) == {"H-003", "EXP-006"}


def test_control_status_exclusion(engine: QueryEngine) -> None:
    parsed = ParsedQuery(status_filter=["done"])
    assert set(_ids(engine, parsed)) == {i for i, _, s in _ITEMS if s is not WorkItemStatus.DONE}


def test_control_one_type_one_status(engine: QueryEngine) -> None:
    parsed = ParsedQuery(type_filter=["expedition"], status_include=["blocked"])
    assert _ids(engine, parsed) == ["EXP-005"]


# ---------------------------------------------------------------------------
# CLI: the reported repro on an hdd-themed scratch repo
# ---------------------------------------------------------------------------


@pytest.fixture
def hdd_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
        ["create", "paper", "Graph paper"],
        ["create", "hypothesis", "Pending hypothesis"],
        ["create", "experiment", "Unrelated experiment"],
    ):
        result = runner.invoke(main, args)
        assert result.exit_code == 0, f"fixture step {args} failed: {result.output}"
    assert list((tmp_path / "research" / "papers").glob("PAPER-*.md"))
    assert list((tmp_path / "research" / "hypotheses").glob("H-*.md"))
    return tmp_path


def test_cli_query_two_types_verbose(hdd_repo: Path) -> None:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "-v", NL_QUERY])
    assert "Type: ['paper', 'hypothesis']" in result.output
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "PAPER-001" in result.output
    assert "H-001" in result.output
    assert "EXPR-001" not in result.output


def test_cli_query_two_types_json(hdd_repo: Path) -> None:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "--json", NL_QUERY])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    ids = {row["id"] for row in json.loads(result.output)}
    assert ids == {"PAPER-001", "H-001"}


def test_cli_control_one_type(hdd_repo: Path) -> None:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "--json", "papers"])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    assert {row["id"] for row in json.loads(result.output)} == {"PAPER-001"}
