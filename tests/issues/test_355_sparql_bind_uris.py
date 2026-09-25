"""#355: structured_query must not splice ParsedQuery status/type values into SPARQL.

``QueryEngine.structured_query`` pastes ``status_filter`` / ``status_include`` /
``type_filter`` values into the query text as ``kb:{s}`` / ``kb:{t.title()}``. No NL
phrase reaches this, but a ``ParsedQuery`` built directly does:
``status_include=['done.']``, ``type_filter=['user story']`` and
``status_filter=['-x']`` each raise ``pyparsing.ParseException``, and a crafted
value can inject SPARQL. The fix may bind URIRefs (as #162 binds literals) or
validate against ``WorkItemStatus`` / ``WorkItemType``; these tests pin only the
results: no raise, and the genuinely matching items. A value that cannot be a
real status/type matches nothing when included and excludes nothing when excluded.

Also: the NL decomposer must dedupe ``status_include`` ("blocked and stranded"
both map to ``blocked``).

Controls (green before and after): valid statuses/types, and the CLI repro from
#64 with the issue's exact string.
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

# ---------------------------------------------------------------------------
# Engine fixtures
# ---------------------------------------------------------------------------

_ITEMS = [
    ("PAPER-001", WorkItemType.PAPER, WorkItemStatus.BACKLOG),
    ("PAPER-002", WorkItemType.PAPER, WorkItemStatus.DONE),
    ("H-003", WorkItemType.HYPOTHESIS, WorkItemStatus.IN_PROGRESS),
    ("EXPR-004", WorkItemType.EXPERIMENT, WorkItemStatus.REVIEW),
    ("EXP-005", WorkItemType.EXPEDITION, WorkItemStatus.BLOCKED),
    ("EXP-006", WorkItemType.EXPEDITION, WorkItemStatus.DONE),
]
_ALL = {i for i, _, _ in _ITEMS}


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


def _ids(engine: QueryEngine, parsed: ParsedQuery) -> set[str]:
    try:
        items = engine.structured_query(parsed)
    except Exception as exc:  # noqa: BLE001 - any raise here is the bug
        pytest.fail(f"structured_query raised for {parsed}: {exc!r}")
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids)), f"duplicate rows for {parsed}: {ids}"
    return set(ids)


def _with_status(*statuses: WorkItemStatus) -> set[str]:
    return {i for i, _, s in _ITEMS if s in statuses}


def _without_status(*statuses: WorkItemStatus) -> set[str]:
    return {i for i, _, s in _ITEMS if s not in statuses}


# Values that can never be a real status or type. The issue's three repros come
# first; the rest try to break out of / inject into the generated query.
_BAD = [
    "done.",
    "user story",
    "-x",
    "done)",
    "done}",
    "done #",
    'done"',
    "done'",
    "kb:x) || true || (",
    "x) || true || (kb:x",
    "x } UNION { ?item kb:id ?id",
    "",
]

# ---------------------------------------------------------------------------
# New behaviour (RED before the fix): the issue's exact repros
# ---------------------------------------------------------------------------


def test_repro_status_include_done_dot() -> None:
    engine = QueryEngine(UnifiedGraph(), None)
    assert _ids(engine, ParsedQuery(status_include=["done."])) == set()


def test_repro_type_filter_user_story() -> None:
    engine = QueryEngine(UnifiedGraph(), None)
    assert _ids(engine, ParsedQuery(type_filter=["user story"])) == set()


def test_repro_status_filter_dash_x() -> None:
    engine = QueryEngine(UnifiedGraph(), None)
    assert _ids(engine, ParsedQuery(status_filter=["-x"])) == set()


# ---------------------------------------------------------------------------
# New behaviour: bad values on a populated graph return the right items
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", _BAD)
def test_bad_status_include_matches_nothing(engine: QueryEngine, value: str) -> None:
    assert _ids(engine, ParsedQuery(status_include=[value])) == set()


@pytest.mark.parametrize("value", _BAD)
def test_bad_type_filter_matches_nothing(engine: QueryEngine, value: str) -> None:
    assert _ids(engine, ParsedQuery(type_filter=[value])) == set()


@pytest.mark.parametrize("value", _BAD)
def test_bad_status_exclude_excludes_nothing(engine: QueryEngine, value: str) -> None:
    assert _ids(engine, ParsedQuery(status_filter=[value])) == _ALL


@pytest.mark.parametrize("value", _BAD)
def test_bad_status_include_beside_valid(engine: QueryEngine, value: str) -> None:
    parsed = ParsedQuery(status_include=["backlog", value, "blocked"])
    assert _ids(engine, parsed) == _with_status(WorkItemStatus.BACKLOG, WorkItemStatus.BLOCKED)


@pytest.mark.parametrize("value", _BAD)
def test_bad_type_filter_beside_valid(engine: QueryEngine, value: str) -> None:
    parsed = ParsedQuery(type_filter=["paper", value])
    assert _ids(engine, parsed) == {"PAPER-001", "PAPER-002"}


@pytest.mark.parametrize("value", _BAD)
def test_bad_status_exclude_beside_valid(engine: QueryEngine, value: str) -> None:
    parsed = ParsedQuery(status_filter=["done", value])
    assert _ids(engine, parsed) == _without_status(WorkItemStatus.DONE)


@pytest.mark.parametrize("value", _BAD)
def test_bad_values_with_other_constraints(engine: QueryEngine, value: str) -> None:
    parsed = ParsedQuery(
        type_filter=["paper", "expedition", value],
        status_filter=[value, "done"],
    )
    assert _ids(engine, parsed) == {"PAPER-001", "EXP-005"}


# ---------------------------------------------------------------------------
# New behaviour: the decomposer dedupes status_include
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nl",
    ["blocked and stranded items", "stranded or blocked expeditions"],
)
def test_decomposer_dedupes_status_include(nl: str) -> None:
    assert NLDecomposer().parse(nl).status_include == ["blocked"]


def test_engine_blocked_and_stranded(engine: QueryEngine) -> None:
    ids = {r.item.id for r in engine.query("blocked and stranded items")}
    assert ids == {"EXP-005"}


# ---------------------------------------------------------------------------
# Controls (GREEN before and after): valid values
# ---------------------------------------------------------------------------


def test_control_decomposer_single_blocked() -> None:
    assert NLDecomposer().parse("blocked items").status_include == ["blocked"]


def test_control_decomposer_two_distinct_statuses() -> None:
    parsed = NLDecomposer().parse("backlog or in progress items")
    assert sorted(parsed.status_include) == ["backlog", "in_progress"]


@pytest.mark.parametrize("status", list(WorkItemStatus))
def test_control_one_status_include(engine: QueryEngine, status: WorkItemStatus) -> None:
    assert _ids(engine, ParsedQuery(status_include=[status.value])) == _with_status(status)


@pytest.mark.parametrize("status", list(WorkItemStatus))
def test_control_one_status_exclude(engine: QueryEngine, status: WorkItemStatus) -> None:
    assert _ids(engine, ParsedQuery(status_filter=[status.value])) == _without_status(status)


@pytest.mark.parametrize("item_type", list(WorkItemType))
def test_control_one_type(engine: QueryEngine, item_type: WorkItemType) -> None:
    expected = {i for i, t, _ in _ITEMS if t is item_type}
    assert _ids(engine, ParsedQuery(type_filter=[item_type.value])) == expected


def test_control_two_status_includes(engine: QueryEngine) -> None:
    parsed = ParsedQuery(status_include=["in_progress", "backlog"])
    assert _ids(engine, parsed) == {"PAPER-001", "H-003"}


def test_control_two_status_excludes(engine: QueryEngine) -> None:
    parsed = ParsedQuery(status_filter=["done", "blocked"])
    assert _ids(engine, parsed) == {"PAPER-001", "H-003", "EXPR-004"}


def test_control_two_types(engine: QueryEngine) -> None:
    parsed = ParsedQuery(type_filter=["paper", "hypothesis"])
    assert _ids(engine, parsed) == {"PAPER-001", "PAPER-002", "H-003"}


def test_control_include_plus_exclude(engine: QueryEngine) -> None:
    parsed = ParsedQuery(status_include=["done", "blocked"], status_filter=["done"])
    assert _ids(engine, parsed) == {"EXP-005"}


def test_control_types_include_and_exclude(engine: QueryEngine) -> None:
    parsed = ParsedQuery(
        type_filter=["paper", "expedition"],
        status_include=["backlog", "done", "blocked"],
        status_filter=["done"],
    )
    assert _ids(engine, parsed) == {"PAPER-001", "EXP-005"}


def test_control_order_numeric_desc(engine: QueryEngine) -> None:
    items = engine.structured_query(ParsedQuery(type_filter=["paper", "expedition"]))
    assert [i.id for i in items] == ["EXP-006", "EXP-005", "PAPER-002", "PAPER-001"]


# ---------------------------------------------------------------------------
# CLI control: the issue's exact string on an hdd-themed scratch repo
# ---------------------------------------------------------------------------

ISSUE_QUERY = "all not-done papers with pending hypotheses"


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
    return tmp_path


def test_cli_issue_exact_string_json(hdd_repo: Path) -> None:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "--json", ISSUE_QUERY])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert isinstance(rows, list)
    assert {row["id"] for row in rows} == {"PAPER-001", "H-001"}
