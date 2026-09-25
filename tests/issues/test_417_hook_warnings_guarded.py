"""Issue #417 — `HookEngine.trigger()` warnings format caller-supplied values unguarded.

Follow-up to #408 (PR #415): same shape. In `trigger()`:
- the action-failure warning formats `{e}` (`str(e)`) and `{context.item_id}` inside
  the `except`, so an exception whose `__str__` raises escapes `trigger()` from the
  handler; it also calls `action.get(...)`, which assumes a dict action;
- the depth-limit warning formats `{context.item_id}` outside any `try`.

Decided behaviour: no warning path in `trigger()` raises because of a
caller-supplied value.
1. A failing action whose exception's `__str__`/`__repr__` raise (via a
   `create_item` callback, or an action handler raising directly): `trigger()`
   does not raise, logs ONE action-failure warning naming the exception's type,
   runs the hook's remaining actions, and restores `_depth`.
2. A non-dict entry in a hook's `actions` list (str, int, list, None): `trigger()`
   does not raise, skips it with ONE warning, and the other actions still run.
3. At the depth limit (reached by a `create_item` callback that re-triggers), a
   context whose `item_id` has raising `__str__`/`__repr__`: the depth-limited
   `trigger()` does not raise, logs ONE depth warning and skips.

Controls: a normal action failure still logs its message (and the item id); an
unknown dict action type still warns; the depth warning still names a normal
item id. The #388/#399/#408 tests stay green.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_388_junk_root_no_crash import (  # noqa: F401 (fixtures)
    SENTINEL_TS,
    _record_action_contexts,
    _warnings,
    warnings_log,
)
from yurtle_kanban import HookEngine
from yurtle_kanban import hooks as hooks_mod
from yurtle_kanban.hooks import HookContext, HookEvent

FAILURE_WARNING = re.compile(r"fail", re.IGNORECASE)
DEPTH_WARNING = re.compile(r"depth", re.IGNORECASE)


def _hooks_doc(actions_yaml: str) -> str:
    return (
        "---\n"
        "type: kanban-hooks\n"
        "id: hooks-417\n"
        "version: 1\n"
        "hooks:\n"
        "  on_create:\n"
        "    - item_types: [expedition]\n"
        "      actions:\n"
        f"{actions_yaml}"
        "---\n"
        "# Hooks 417\n"
    )


CREATE_THEN_LOG = (
    "        - type: create_item\n"
    "          item_type: chore\n"
    "          title: child\n"
    "        - type: log\n"
)

NOTIFY_THEN_LOG = (
    "        - type: notify\n"
    "        - type: log\n"
)


def _engine(tmp_path: Path, actions_yaml: str) -> HookEngine:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    hooks = tmp_path / "hooks.yurtle.md"
    hooks.write_text(_hooks_doc(actions_yaml), encoding="utf-8")
    engine = HookEngine(hooks, repo_root=repo)
    assert engine.is_configured  # sanity: the hooks file loaded
    return engine


def _ctx(item_id: object = "E-1") -> HookContext:
    ctx = HookContext(
        event=HookEvent.ITEM_CREATED,
        item_id=item_id,  # type: ignore[arg-type]
        item_type="expedition",
        title="A title",
    )
    ctx.timestamp = SENTINEL_TS
    return ctx


def _trigger(engine: HookEngine, ctx: HookContext) -> None:
    """`engine.trigger`, failing the test (by exception type — its str can't be
    printed, and pytest would crash reporting it) when anything escapes."""
    escaped: str | None = None
    try:
        engine.trigger(HookEvent.ITEM_CREATED, ctx)
    except BaseException as e:  # noqa: BLE001 - recording any escape
        escaped = type(e).__name__
    if escaped is not None:
        pytest.fail(f"trigger() raised {escaped}", pytrace=False)


# --- values whose str/repr raise ------------------------------------------------------


class _UnprintableError(Exception):
    """An exception whose `__str__` and `__repr__` raise another one of itself, so any
    handler that formats the one it caught raises again."""

    def __str__(self) -> str:
        raise _UnprintableError()

    def __repr__(self) -> str:
        raise _UnprintableError()


class _UnprintableId:
    """An item id whose `__str__` and `__repr__` raise."""

    def __str__(self) -> str:
        raise _UnprintableError()

    def __repr__(self) -> str:
        raise _UnprintableError()


def test_sanity_unprintables_raise() -> None:
    with pytest.raises(_UnprintableError):
        str(_UnprintableError())
    with pytest.raises(_UnprintableError):
        repr(_UnprintableError())
    with pytest.raises(_UnprintableError):
        f"{_UnprintableId()}"
    with pytest.raises(_UnprintableError):
        repr(_UnprintableId())


# --- case 1: a failing action whose exception can't be printed -----------------------


def _raise_unprintable(**_: Any) -> None:
    raise _UnprintableError()


def _via_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HookEngine:
    engine = _engine(tmp_path, CREATE_THEN_LOG)
    engine.set_callback("create_item", _raise_unprintable)
    return engine


def _via_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HookEngine:
    def raising(action: dict, context: HookContext) -> None:
        raise _UnprintableError()

    monkeypatch.setattr(hooks_mod, "_action_notify", raising)
    return _engine(tmp_path, NOTIFY_THEN_LOG)


FAILING_SOURCES: list[Any] = [
    pytest.param(_via_callback, id="create_item-callback"),
    pytest.param(_via_handler, id="action-handler"),
]

_Source = Callable[[Path, pytest.MonkeyPatch], HookEngine]


@pytest.mark.parametrize("make_engine", FAILING_SOURCES)
def test_unprintable_failure_does_not_raise_and_later_actions_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_engine: _Source
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine = make_engine(tmp_path, monkeypatch)
    _trigger(engine, _ctx())  # must not raise
    assert len(seen) == 1, f"the later log action ran {len(seen)} times"


@pytest.mark.parametrize("make_engine", FAILING_SOURCES)
def test_unprintable_failure_logs_one_warning_naming_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    make_engine: _Source,
) -> None:
    _record_action_contexts(monkeypatch)
    engine = make_engine(tmp_path, monkeypatch)
    _trigger(engine, _ctx())
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert FAILURE_WARNING.search(warned[0]), f"not a failure warning: {warned[0]!r}"
    assert "_UnprintableError" in warned[0], (
        f"warning doesn't name the exception's type: {warned[0]!r}"
    )


@pytest.mark.parametrize("make_engine", FAILING_SOURCES)
def test_unprintable_failure_depth_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_engine: _Source
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine = make_engine(tmp_path, monkeypatch)
    assert engine._depth == 0
    _trigger(engine, _ctx())
    assert engine._depth == 0
    _trigger(engine, _ctx())  # the depth guard isn't stuck
    assert engine._depth == 0
    assert len(seen) == 2


# --- case 2: a non-dict action entry -------------------------------------------------


NON_DICT_ACTIONS = [
    pytest.param("just-a-string", id="str"),
    pytest.param("42", id="int"),
    pytest.param("[log]", id="list"),
    pytest.param("null", id="none"),
]


def _log_junk_log(junk_yaml: str) -> str:
    return (
        "        - type: log\n"
        f"        - {junk_yaml}\n"
        "        - type: log\n"
    )


@pytest.mark.parametrize("junk_yaml", NON_DICT_ACTIONS)
def test_non_dict_action_skipped_with_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    junk_yaml: str,
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine = _engine(tmp_path, _log_junk_log(junk_yaml))
    _trigger(engine, _ctx())  # must not raise
    assert len(seen) == 2, f"the dict log actions ran {len(seen)} times, want 2"
    assert engine._depth == 0
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"


# --- case 3: depth-limit warning with an unprintable item id -------------------------


def _depth_engine(
    tmp_path: Path, deepest_item_id: object
) -> tuple[HookEngine, list[str]]:
    """An engine whose create_item callback re-triggers on_create; at the depth limit
    it re-triggers with `deepest_item_id`. Anything that escapes the re-trigger is
    recorded (not re-raised, so the action's own handler can't hide it)."""
    engine = _engine(tmp_path, CREATE_THEN_LOG)
    escaped: list[str] = []

    def retrigger(**_: Any) -> None:
        at_limit = engine._depth >= HookEngine._MAX_HOOK_DEPTH
        ctx = _ctx(deepest_item_id if at_limit else "E-1")
        try:
            engine.trigger(HookEvent.ITEM_CREATED, ctx)
        except BaseException as e:  # noqa: BLE001 - recording any escape
            escaped.append(type(e).__name__)

    engine.set_callback("create_item", retrigger)
    return engine, escaped


def test_depth_limit_unprintable_item_id_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, escaped = _depth_engine(tmp_path, _UnprintableId())
    _trigger(engine, _ctx())
    assert escaped == [], f"the depth-limited trigger raised: {escaped!r}"
    assert len(seen) == HookEngine._MAX_HOOK_DEPTH  # one log per level that ran
    assert engine._depth == 0


def test_depth_limit_unprintable_item_id_logs_one_depth_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    _record_action_contexts(monkeypatch)
    engine, _ = _depth_engine(tmp_path, _UnprintableId())
    _trigger(engine, _ctx())
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert DEPTH_WARNING.search(warned[0]), f"not a depth warning: {warned[0]!r}"


# --- green controls ------------------------------------------------------------------


def test_normal_callback_failure_logs_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine = _engine(tmp_path, CREATE_THEN_LOG)

    def failing(**_: Any) -> None:
        raise ValueError("plain failure 417")

    engine.set_callback("create_item", failing)
    _trigger(engine, _ctx())
    assert len(seen) == 1
    assert engine._depth == 0
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert "plain failure 417" in warned[0]


def test_normal_handler_failure_logs_message_and_item_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    seen = _record_action_contexts(monkeypatch)

    def raising(action: dict, context: HookContext) -> None:
        raise ValueError("plain failure 417")

    monkeypatch.setattr(hooks_mod, "_action_notify", raising)
    engine = _engine(tmp_path, NOTIFY_THEN_LOG)
    _trigger(engine, _ctx())
    assert len(seen) == 1
    assert engine._depth == 0
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert "plain failure 417" in warned[0]
    assert "notify" in warned[0]
    assert "E-1" in warned[0]


def test_unknown_dict_action_type_still_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine = _engine(tmp_path, _log_junk_log("type: frobnicate"))
    _trigger(engine, _ctx())
    assert len(seen) == 2
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert "frobnicate" in warned[0]


def test_depth_limit_normal_item_id_warns_and_skips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, escaped = _depth_engine(tmp_path, "E-DEEP")
    _trigger(engine, _ctx())
    assert escaped == []
    assert len(seen) == HookEngine._MAX_HOOK_DEPTH
    assert engine._depth == 0
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert DEPTH_WARNING.search(warned[0])
    assert "E-DEEP" in warned[0]
