"""#367: structured_query must not build URIRefs rdflib logs as invalid.

Since #355, ``QueryEngine.structured_query`` binds ParsedQuery status/type values
as ``URIRef(KB + value)``. For a value holding any of rdflib's invalid IRI
characters (space, ``<``, ``>``, ``"``, ``{``, ``}``, ``|``, backslash, ``^``,
backtick), rdflib 7.6 logs "... does not look like a valid URI, trying to
serialize this will break." on the ``rdflib.term`` logger, which with no handler
lands on stderr. No NL phrase reaches this; a library caller does, e.g.
``ParsedQuery(type_filter=['user story'])``.

Decided behaviour: such values are skipped before any URIRef is built, so no
``rdflib.term`` warning is emitted, and the results stay as #355 pinned them:
a skipped include value matches nothing, a skipped exclude value excludes
nothing, a skipped type matches nothing, and beside valid values the result is
that of the valid values alone.

Fixing it by silencing the ``rdflib.term`` logger around the query is not the
decided behaviour: the spy on ``rdflib.term._is_valid_uri`` catches any invalid
URIRef still being built, and the logger's level/state must be unchanged after
the call. ``KanbanService._parse_graph`` keeps its own level juggling (#59) and
must restore the level it found.

Of #355's ``_BAD`` values, today's warners are ``_WARNS_TODAY`` below;
``done.``, ``-x``, ``done)``, ``done'`` and ``""`` do not warn (their results
stay pinned by #355's tests and are re-checked here as controls).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import rdflib.term

from tests.issues.test_355_sparql_bind_uris import _ALL, _BAD, _ITEMS, _item
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.query import ParsedQuery, QueryEngine, UnifiedGraph
from yurtle_kanban.service import KanbanService

RDFLIB_LOGGER = "rdflib.term"
_SRC = Path(__file__).resolve().parents[2] / "src"
_ORIGINAL_IS_VALID_URI = rdflib.term._is_valid_uri

# #355's bad values that make rdflib 7.6 warn today (checked one by one).
_WARNS_TODAY = [
    "user story",
    "done}",
    "done #",
    'done"',
    "kb:x) || true || (",
    "x) || true || (kb:x",
    "x } UNION { ?item kb:id ?id",
]
# #355's bad values that do NOT warn today (no rdflib-invalid character).
_QUIET_TODAY = [v for v in _BAD if v not in _WARNS_TODAY]
# One value per rdflib-invalid character, plus the issue's own example.
_PER_CHAR = [
    "a b",
    "a<b",
    "a>b",
    'a"b',
    "a{b",
    "a}b",
    "a|b",
    "a\\b",
    "a^b",
    "a`b",
    "user story",
]
_INVALID = list(dict.fromkeys(_WARNS_TODAY + _PER_CHAR))
_FIELDS = ["status_include", "status_filter", "type_filter"]


@pytest.fixture
def engine() -> QueryEngine:
    ug = UnifiedGraph()
    ug.add_items([_item(*spec) for spec in _ITEMS])
    return QueryEngine(unified_graph=ug, embedding_index=None)


@pytest.fixture
def rdflib_caplog(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.WARNING, logger=RDFLIB_LOGGER)
    return caplog


@pytest.fixture
def invalid_uris(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every string URIRef.__new__ judges invalid while the test runs."""
    seen: list[str] = []

    def spy(uri: str) -> bool:
        ok = _ORIGINAL_IS_VALID_URI(uri)
        if not ok:
            seen.append(uri)
        return ok

    monkeypatch.setattr(rdflib.term, "_is_valid_uri", spy)
    return seen


def _rdflib_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == RDFLIB_LOGGER and r.levelno >= logging.WARNING
    ]


def _ids(engine: QueryEngine, parsed: ParsedQuery) -> set[str]:
    return {i.id for i in engine.structured_query(parsed)}


def _with_status(*statuses: WorkItemStatus) -> set[str]:
    return {i for i, _, s in _ITEMS if s in statuses}


def _without_status(*statuses: WorkItemStatus) -> set[str]:
    return {i for i, _, s in _ITEMS if s not in statuses}


def _logger_state() -> tuple[Any, ...]:
    lg = logging.getLogger(RDFLIB_LOGGER)
    return (lg.level, lg.disabled, lg.propagate, tuple(lg.handlers), tuple(lg.filters))


# ---------------------------------------------------------------------------
# Harness sanity (GREEN): caplog sees rdflib.term warnings; query.py doesn't mute it
# ---------------------------------------------------------------------------


def test_harness_caplog_sees_rdflib_warning(rdflib_caplog: pytest.LogCaptureFixture) -> None:
    rdflib.term.URIRef("https://yurtle.dev/kanban/user story")
    assert _rdflib_warnings(rdflib_caplog), "caplog must capture rdflib.term warnings"


def test_harness_query_module_leaves_rdflib_logger_alone() -> None:
    lg = logging.getLogger(RDFLIB_LOGGER)
    assert not lg.disabled
    assert lg.getEffectiveLevel() <= logging.WARNING
    assert not any(isinstance(f, logging.Filter) for f in lg.filters)


# ---------------------------------------------------------------------------
# New behaviour (RED before the fix): no rdflib warning for invalid values
# ---------------------------------------------------------------------------


def test_repro_type_filter_user_story_no_warning(
    rdflib_caplog: pytest.LogCaptureFixture,
) -> None:
    engine = QueryEngine(UnifiedGraph(), None)
    assert _ids(engine, ParsedQuery(type_filter=["user story"])) == set()
    assert _rdflib_warnings(rdflib_caplog) == []


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("value", _INVALID)
def test_invalid_value_logs_no_rdflib_warning(
    engine: QueryEngine,
    rdflib_caplog: pytest.LogCaptureFixture,
    field: str,
    value: str,
) -> None:
    engine.structured_query(ParsedQuery(**{field: [value]}))
    assert _rdflib_warnings(rdflib_caplog) == []


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("value", _INVALID)
def test_invalid_value_builds_no_invalid_uriref(
    engine: QueryEngine, invalid_uris: list[str], field: str, value: str
) -> None:
    """Skipped, not silenced: no invalid URIRef is built at all."""
    engine.structured_query(ParsedQuery(**{field: [value]}))
    assert invalid_uris == []


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("value", _INVALID)
def test_invalid_value_leaves_rdflib_logger_state(
    engine: QueryEngine, field: str, value: str
) -> None:
    before = _logger_state()
    engine.structured_query(ParsedQuery(**{field: [value]}))
    assert _logger_state() == before


def test_all_invalid_everywhere_no_warning(
    engine: QueryEngine, rdflib_caplog: pytest.LogCaptureFixture
) -> None:
    parsed = ParsedQuery(
        status_include=["backlog", *_INVALID, "blocked"],
        status_filter=[*_INVALID, "blocked"],
        type_filter=["paper", *_INVALID, "expedition"],
    )
    assert _ids(engine, parsed) == {"PAPER-001"}
    assert _rdflib_warnings(rdflib_caplog) == []


_STDERR_SCRIPT = """
from yurtle_kanban.query import ParsedQuery, QueryEngine, UnifiedGraph
parsed = ParsedQuery(
    type_filter=["user story"], status_filter=["a b"], status_include=["done}", "done"]
)
print(len(QueryEngine(UnifiedGraph(), None).structured_query(parsed)))
"""


def test_invalid_value_nothing_on_stderr() -> None:
    """A fresh process with no logging configured: the library caller's view.
    (In-process, pytest's own root handler hides Python's last-resort stderr.)"""
    proc = subprocess.run(
        [sys.executable, "-c", _STDERR_SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(_SRC)},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "0"
    assert proc.stderr == ""


# ---------------------------------------------------------------------------
# Results stay as #355 pinned them (GREEN before and after), no warning (RED)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", _INVALID)
def test_invalid_include_matches_nothing(
    engine: QueryEngine, rdflib_caplog: pytest.LogCaptureFixture, value: str
) -> None:
    assert _ids(engine, ParsedQuery(status_include=[value])) == set()
    assert _rdflib_warnings(rdflib_caplog) == []


@pytest.mark.parametrize("value", _INVALID)
def test_invalid_exclude_excludes_nothing(
    engine: QueryEngine, rdflib_caplog: pytest.LogCaptureFixture, value: str
) -> None:
    assert _ids(engine, ParsedQuery(status_filter=[value])) == _ALL
    assert _rdflib_warnings(rdflib_caplog) == []


@pytest.mark.parametrize("value", _INVALID)
def test_invalid_type_matches_nothing(
    engine: QueryEngine, rdflib_caplog: pytest.LogCaptureFixture, value: str
) -> None:
    assert _ids(engine, ParsedQuery(type_filter=[value])) == set()
    assert _rdflib_warnings(rdflib_caplog) == []


@pytest.mark.parametrize("value", _INVALID)
def test_invalid_mixed_with_valid(
    engine: QueryEngine, rdflib_caplog: pytest.LogCaptureFixture, value: str
) -> None:
    assert _ids(engine, ParsedQuery(status_include=["backlog", value, "blocked"])) == (
        _with_status(WorkItemStatus.BACKLOG, WorkItemStatus.BLOCKED)
    )
    assert _ids(engine, ParsedQuery(status_filter=["done", value])) == _without_status(
        WorkItemStatus.DONE
    )
    assert _ids(engine, ParsedQuery(type_filter=["paper", value])) == {"PAPER-001", "PAPER-002"}
    assert _ids(
        engine,
        ParsedQuery(type_filter=["paper", "expedition", value], status_filter=[value, "done"]),
    ) == {"PAPER-001", "EXP-005"}
    assert _rdflib_warnings(rdflib_caplog) == []


def test_invalid_include_alone_does_not_widen(
    engine: QueryEngine, rdflib_caplog: pytest.LogCaptureFixture
) -> None:
    """Skipping every include value must not drop the IN filter (match-all)."""
    assert _ids(engine, ParsedQuery(status_include=list(_INVALID))) == set()
    assert _ids(engine, ParsedQuery(type_filter=list(_INVALID))) == set()
    assert _rdflib_warnings(rdflib_caplog) == []


# ---------------------------------------------------------------------------
# Controls (GREEN before and after)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", _QUIET_TODAY)
@pytest.mark.parametrize("field", _FIELDS)
def test_control_quiet_bad_values_no_warning_and_results(
    engine: QueryEngine,
    rdflib_caplog: pytest.LogCaptureFixture,
    field: str,
    value: str,
) -> None:
    expected = _ALL if field == "status_filter" else set()
    assert _ids(engine, ParsedQuery(**{field: [value]})) == expected
    assert _rdflib_warnings(rdflib_caplog) == []


@pytest.mark.parametrize("status", list(WorkItemStatus))
def test_control_valid_status_no_warning(
    engine: QueryEngine,
    rdflib_caplog: pytest.LogCaptureFixture,
    invalid_uris: list[str],
    status: WorkItemStatus,
) -> None:
    assert _ids(engine, ParsedQuery(status_include=[status.value])) == _with_status(status)
    assert _ids(engine, ParsedQuery(status_filter=[status.value])) == _without_status(status)
    assert _rdflib_warnings(rdflib_caplog) == []
    assert invalid_uris == []


@pytest.mark.parametrize("item_type", list(WorkItemType))
def test_control_valid_type_no_warning(
    engine: QueryEngine,
    rdflib_caplog: pytest.LogCaptureFixture,
    invalid_uris: list[str],
    item_type: WorkItemType,
) -> None:
    expected = {i for i, t, _ in _ITEMS if t is item_type}
    assert _ids(engine, ParsedQuery(type_filter=[item_type.value])) == expected
    assert _rdflib_warnings(rdflib_caplog) == []
    assert invalid_uris == []


def test_control_valid_combined_no_warning(
    engine: QueryEngine, rdflib_caplog: pytest.LogCaptureFixture
) -> None:
    parsed = ParsedQuery(
        type_filter=["paper", "expedition"],
        status_include=["backlog", "done", "blocked"],
        status_filter=["done"],
    )
    before = _logger_state()
    assert _ids(engine, parsed) == {"PAPER-001", "EXP-005"}
    assert _rdflib_warnings(rdflib_caplog) == []
    assert _logger_state() == before


def test_control_valid_order_unchanged(engine: QueryEngine) -> None:
    items = engine.structured_query(ParsedQuery(type_filter=["paper", "expedition"]))
    assert [i.id for i in items] == ["EXP-006", "EXP-005", "PAPER-002", "PAPER-001"]


# ---------------------------------------------------------------------------
# _parse_graph keeps silencing rdflib.term around its own parse, and restores it
# ---------------------------------------------------------------------------

_GRAPH_DOC = """---
id: FEAT-001
---
# Title

```turtle
@prefix kb: <https://yurtle.dev/kanban/> .
<https://yurtle.dev/kanban/item/FEAT-001> kb:note "x" .
```
"""


@pytest.mark.parametrize("start_level", [logging.NOTSET, logging.DEBUG, logging.WARNING])
def test_parse_graph_mutes_then_restores_rdflib_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    start_level: int,
) -> None:
    import yurtle_rdflib

    lg = logging.getLogger(RDFLIB_LOGGER)
    real_parse = yurtle_rdflib.parse_yurtle
    seen_levels: list[int] = []

    def spy_parse(content: str, *args: Any, **kwargs: Any) -> Any:
        seen_levels.append(lg.level)
        lg.warning("https://x/a b does not look like a valid URI")  # as rdflib would
        return real_parse(content, *args, **kwargs)

    monkeypatch.setattr(yurtle_rdflib, "parse_yurtle", spy_parse)
    old = lg.level
    try:
        lg.setLevel(start_level)
        caplog.set_level(logging.DEBUG)  # root: capture anything that gets through
        svc = KanbanService(KanbanConfig(), tmp_path)
        graph = svc._parse_graph(_GRAPH_DOC)
        assert graph is not None
        assert seen_levels == [logging.ERROR], "parse must run with rdflib.term at ERROR"
        assert lg.level == start_level, "level change leaked out of _parse_graph"
        assert _rdflib_warnings(caplog) == []
    finally:
        lg.setLevel(old)


def test_parse_graph_restores_level_when_parse_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yurtle_rdflib

    lg = logging.getLogger(RDFLIB_LOGGER)

    def boom(content: str, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("bad turtle")

    monkeypatch.setattr(yurtle_rdflib, "parse_yurtle", boom)
    old = lg.level
    try:
        lg.setLevel(logging.INFO)
        assert KanbanService(KanbanConfig(), tmp_path)._parse_graph(_GRAPH_DOC) is None
        assert lg.level == logging.INFO
    finally:
        lg.setLevel(old)


def test_control_valid_items_graph_builds_no_invalid_uriref(invalid_uris: list[str]) -> None:
    ug = UnifiedGraph()
    ug.add_items(
        [
            WorkItem(
                id="PAPER-001",
                title="t",
                item_type=WorkItemType.PAPER,
                status=WorkItemStatus.DONE,
                file_path=Path("/tmp/PAPER-001.md"),
                priority="medium",
                created=date(2026, 1, 1),
            )
        ]
    )
    QueryEngine(ug, None).structured_query(ParsedQuery(status_include=["done"]))
    assert invalid_uris == []
