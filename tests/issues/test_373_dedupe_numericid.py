# ruff: noqa: F811  -- pytest fixtures imported from the #349 test module are re-bound as args
"""Issue #373 — follow-ups from the reviews of PR #370 (#349) and PR #366 (#357).

1. ``structured_query`` runs ``SELECT DISTINCT ?id ?numId``. An item whose yurtle
   block adds a second ``kb:numericId`` (``item:PAPER-002 kb:numericId 999 .``) has two
   distinct ``(?id, ?numId)`` rows, so it still comes back twice.
   Decided: each item once, ordered by its HIGHEST numericId (the kept row is the
   first, highest one). Red through QueryEngine and ``query --no-semantic --json``.
   Controls: the #349 multi-type / multi-tag / multi-assignee duplicates stay fixed,
   and ordering on a board with no extra numericId is unchanged.

2. ``HookEngine.trigger()`` copies the caller's context with ``dataclasses.replace``
   and restores only ``timestamp``. Decided: the context an action sees carries every
   field of the caller's context (``repo_root`` aside, which the copy fills in). A
   field-list guard fails if ``HookContext`` grows another ``init=False`` or
   ``__post_init__``-derived field, pointing at ``trigger()``'s restore. Expected green
   today (#357 round 2 restores the timestamp): a pinning test.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_349_sparql_distinct import (  # noqa: F401 (fixtures)
    DUAL,
    engine,
    repo,
)
from tests.issues.test_357_hooks_context_copy_safe_paths import FIXED_TS, _engine
from yurtle_kanban import hooks as hooks_mod
from yurtle_kanban.cli import get_service, main
from yurtle_kanban.hooks import HookContext, HookEvent
from yurtle_kanban.query import ParsedQuery, QueryEngine

# ---------------------------------------------------------------------------
# 1. a second kb:numericId
# ---------------------------------------------------------------------------

# PAPER-002 (numericId 2) also claims 999: it must sort first, once.
# PAPER-003 (numericId 3) also claims 0: it must keep its place (max is 3), once.
_EXTRA_NUMID = {
    "PAPER-002": 999,
    "PAPER-003": 0,
}

_BLOCK = """
```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix item: <https://yurtle.dev/kanban/item/> .
item:{item_id} kb:numericId {num} .
```
"""

# highest numericId first: PAPER-002 (999), H-004 (4), PAPER-003 (3), PAPER-001 (1)
ALL_ORDER = ["PAPER-002", DUAL, "PAPER-003", "PAPER-001"]


@pytest.fixture
def numid_repo(repo: Path) -> Path:
    """The #349 board, plus a second kb:numericId on PAPER-002 and PAPER-003."""
    for item_id, num in _EXTRA_NUMID.items():
        (path,) = repo.rglob(f"{item_id}*.md")
        path.write_text(
            path.read_text(encoding="utf-8") + _BLOCK.format(item_id=item_id, num=num),
            encoding="utf-8",
        )
    return repo


@pytest.fixture
def numid_engine(numid_repo: Path) -> QueryEngine:
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    for item_id, num in _EXTRA_NUMID.items():
        item = eng._ug.get_item(item_id)
        assert item is not None, f"fixture: {item_id} not scanned"
        rows = eng.sparql(
            "PREFIX kb: <https://yurtle.dev/kanban/>\n"
            f'SELECT ?n WHERE {{ ?i kb:id "{item_id}" ; kb:numericId ?n . }}'
        )
        nums = sorted(int(r["n"]) for r in rows)
        assert len(nums) == 2 and num in nums, (
            f"fixture: {item_id} should carry two kb:numericId values, got {nums}"
        )
    return eng


def _ids(engine: QueryEngine, parsed: ParsedQuery) -> list[str]:
    return [i.id for i in engine.structured_query(parsed)]


def _cli_ids(query: str) -> list[str]:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "--json", query])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    return [row["id"] for row in json.loads(result.output)]


def _assert_unique(ids: list[str]) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"items returned more than once {dupes}: {ids}"


# --- RED before the fix ------------------------------------------------------


def test_engine_two_numericids_status_query(numid_engine: QueryEngine) -> None:
    ids = _ids(numid_engine, ParsedQuery(status_include=["backlog"]))
    _assert_unique(ids)
    assert ids == ALL_ORDER


def test_engine_two_numericids_type_filter(numid_engine: QueryEngine) -> None:
    ids = _ids(numid_engine, ParsedQuery(type_filter=["paper"]))
    _assert_unique(ids)
    assert ids == ALL_ORDER  # H-004 is a kb:Paper through its #349 block


def test_engine_two_numericids_with_349_multimatch(numid_engine: QueryEngine) -> None:
    # H-004 matches both types; PAPER-002 carries two numericIds
    ids = _ids(numid_engine, ParsedQuery(type_filter=["paper", "hypothesis"]))
    _assert_unique(ids)
    assert ids == ALL_ORDER


def test_engine_two_numericids_tag(numid_engine: QueryEngine) -> None:
    ids = _ids(numid_engine, ParsedQuery(tag="brain"))
    _assert_unique(ids)
    assert ids == ["PAPER-002", DUAL]


def test_engine_lower_second_numericid_keeps_place(numid_engine: QueryEngine) -> None:
    # PAPER-003's extra 0 must not add a row nor move it below PAPER-001
    ids = _ids(numid_engine, ParsedQuery(type_filter=["paper"], status_include=["backlog"]))
    _assert_unique(ids)
    assert ids.index("PAPER-003") < ids.index("PAPER-001"), ids


def test_cli_two_numericids_backlog(numid_repo: Path) -> None:
    ids = _cli_ids("backlog items")
    _assert_unique(ids)
    assert ids == ALL_ORDER


def test_cli_two_numericids_tag(numid_repo: Path) -> None:
    ids = _cli_ids("items tagged brain")
    _assert_unique(ids)
    assert ids == ["PAPER-002", DUAL]


# --- controls (GREEN before and after) ---------------------------------------


def test_control_single_numericid_item(numid_engine: QueryEngine) -> None:
    assert _ids(numid_engine, ParsedQuery(type_filter=["hypothesis"])) == [DUAL]
    assert _ids(numid_engine, ParsedQuery(assignee="mini")) == [DUAL, "PAPER-001"]


def test_control_349_duplicates_stay_fixed(engine: QueryEngine) -> None:
    ids = _ids(engine, ParsedQuery(type_filter=["paper", "hypothesis"]))
    assert ids == [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]
    assert _ids(engine, ParsedQuery(tag="brain")) == [DUAL, "PAPER-002"]
    assert _ids(engine, ParsedQuery(assignee="mini")) == [DUAL, "PAPER-001"]
    parsed = ParsedQuery(type_filter=["paper", "hypothesis"], tag="brain", assignee="mini")
    assert _ids(engine, parsed) == [DUAL]


def test_control_normal_board_order(engine: QueryEngine) -> None:
    ids = _ids(engine, ParsedQuery(status_include=["backlog"]))
    assert ids == [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]


def test_control_cli_normal_board_order(repo: Path) -> None:
    assert _cli_ids("backlog items") == [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]


# ---------------------------------------------------------------------------
# 2. HookContext copies keep every field
# ---------------------------------------------------------------------------

_COPIED = [f.name for f in dataclasses.fields(HookContext) if f.name != "repo_root"]


def _full_ctx() -> HookContext:
    """A context with every field off its default."""
    ctx = HookContext(
        event=HookEvent.STATUS_CHANGE,
        item_id="E-373",
        item_type="expedition",
        title="Every field set",
        old_status="backlog",
        new_status="in_progress",
        assignee="Mini",
        forced=True,
        metadata={"extra": "kept", "n": 3},
    )
    ctx.timestamp = FIXED_TS
    return ctx


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[HookContext]:
    seen: list[HookContext] = []
    real = hooks_mod._action_log

    def spy(action: dict, context: HookContext) -> None:
        seen.append(context)
        real(action, context)

    monkeypatch.setattr(hooks_mod, "_action_log", spy)
    return seen


def test_hookcontext_fields_are_the_known_set() -> None:
    assert set(_COPIED) | {"repo_root"} == {f.name for f in dataclasses.fields(HookContext)}
    assert "repo_root" in {f.name for f in dataclasses.fields(HookContext)}
    assert sorted(_COPIED) == sorted(
        [
            "event",
            "item_id",
            "item_type",
            "title",
            "old_status",
            "new_status",
            "assignee",
            "forced",
            "metadata",
            "timestamp",
        ]
    ), "HookContext gained/lost a field: extend _full_ctx() and check trigger()'s copy"


def test_derived_fields_are_only_timestamp() -> None:
    """Tripwire: trigger() restores only `timestamp` after dataclasses.replace()."""
    no_init = {f.name for f in dataclasses.fields(HookContext) if not f.init}
    post_init = getattr(HookContext, "__post_init__", None)
    derived: set[str] = set()
    if post_init is not None:
        derived = set(re.findall(r"self\.(\w+)\s*(?::[^=]+)?=(?!=)", inspect.getsource(post_init)))
    names = {f.name for f in dataclasses.fields(HookContext)}
    found = (no_init | derived) & names
    assert found == {"timestamp"}, (
        f"HookContext fields that are init=False or set in __post_init__: {sorted(found)}. "
        "dataclasses.replace() in HookEngine.trigger() re-runs __post_init__ and drops "
        "init=False values; trigger() restores only `timestamp` — extend that restore "
        "(and this test) for the new field(s)."
    )


def test_action_sees_every_field_of_callers_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, engine = _engine(tmp_path, "repo")
    seen = _capture(monkeypatch)
    ctx = _full_ctx()
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    assert len(seen) == 1, f"the log action ran {len(seen)} times"
    (got,) = seen
    assert got is not ctx, "trigger() handed the caller's own context to the action"
    diffs = {
        name: (getattr(ctx, name), getattr(got, name))
        for name in _COPIED
        if getattr(got, name) != getattr(ctx, name)
    }
    assert not diffs, f"the copy differs from the caller's context (caller, copy): {diffs}"
    assert got.repo_root == root, "the copy did not get the engine's repo_root"
    assert ctx.repo_root is None, "trigger() mutated the caller's context"
    assert ctx.timestamp == FIXED_TS


def test_action_sees_every_field_across_two_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_a, engine_a = _engine(tmp_path, "repo-a")
    root_b, engine_b = _engine(tmp_path, "repo-b")
    seen = _capture(monkeypatch)
    ctx = _full_ctx()
    engine_a.trigger(HookEvent.ITEM_CREATED, ctx)
    engine_b.trigger(HookEvent.ITEM_CREATED, ctx)
    assert [c.repo_root for c in seen] == [root_a, root_b]
    for got in seen:
        assert {n: getattr(got, n) for n in _COPIED} == {n: getattr(ctx, n) for n in _COPIED}


def test_control_context_with_repo_root_passes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, engine = _engine(tmp_path, "repo")
    own = tmp_path / "own-root"
    own.mkdir()
    seen = _capture(monkeypatch)
    ctx = _full_ctx()
    ctx.repo_root = own
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    (got,) = seen
    assert got.repo_root == own
    assert {n: getattr(got, n) for n in _COPIED} == {n: getattr(ctx, n) for n in _COPIED}
