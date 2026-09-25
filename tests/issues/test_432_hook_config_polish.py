"""Issue #432 — hook config polish after #425 (PR #429).

Decided ([steer] bucket-2 on #432), at `HookEngine` load from a hooks file:

1. Unknown event names: an event key that is not a `HookEvent` value
   (`on_created:`, `5:`, `null:`) is dropped with ONE warning naming the file and the
   key and saying it is not a hook event. Valid events in the same file still load
   and fire, and "Loaded N hook(s)" does not count the dropped hooks.
2. Lone-string `item_types`: `item_types: expedition` behaves exactly like
   `[expedition]`, silently — an exact match, never a substring (`exp` does not
   match `expedition`).
3. YAML 1.1 booleans in `from`/`to` (`yes`/`no`/`on`/`off`): the hook is still
   dropped, and its one warning tells the user to quote the status name.
4. Pins for the two #429 mutations that survived: `hooks: [a, b]` warns
   "not a mapping" (not the generic "Failed to load hooks config"), and
   `on_create: null` is silent while the other events still load.

Controls: the #425 tests stay green; a valid config loads unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from tests.issues.test_388_junk_root_no_crash import (  # noqa: F401 (fixtures)
    _record_action_contexts,
    _warnings,
    warnings_log,
)
from tests.issues.test_417_hook_warnings_guarded import _ctx, _trigger
from tests.issues.test_425_hook_config_shapes import (
    VALID_HOOK,
    VALID_HOOKS_YAML,
    _doc,
    _load,
    _status_ctx,
)
from yurtle_kanban.hooks import HookContext, HookEvent

HOOKS_LOGGER = "yurtle-kanban.hooks"


@pytest.fixture
def info_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog at INFO, attached straight to the hooks logger (whatever its propagation)."""
    log = logging.getLogger(HOOKS_LOGGER)
    old_level = log.level
    log.addHandler(caplog.handler)
    log.setLevel(logging.INFO)
    caplog.handler.setLevel(logging.INFO)
    try:
        yield caplog
    finally:
        log.removeHandler(caplog.handler)
        log.setLevel(old_level)


def _loaded_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    records = {id(r): r for r in caplog.records}.values()
    return [r.getMessage() for r in records if r.getMessage().startswith("Loaded ")]


def _one_warning(caplog: pytest.LogCaptureFixture) -> str:
    warned = _warnings(caplog)
    assert len(warned) == 1, f"expected one load warning, got {warned!r}"
    return warned[0]


def _expedition_ctx(item_type: str = "expedition") -> HookContext:
    ctx = _ctx()
    ctx.item_type = item_type
    return ctx


# --- 1. unknown event names -----------------------------------------------------------

# (yaml key, the texts any one of which must name it in the warning)
UNKNOWN_EVENTS = [
    pytest.param("on_created", ("on_created",), id="typo"),
    pytest.param("5", ("5",), id="int"),
    pytest.param("null", ("None", "null"), id="null"),
    pytest.param("ON_CREATE", ("ON_CREATE",), id="wrong-case"),
    pytest.param("ITEM_CREATED", ("ITEM_CREATED",), id="enum-member-name"),
]


def _unknown_doc(key: str) -> str:
    # a valid hook under the unknown key, and a valid on_create hook beside it
    return _doc(
        "\n"
        f"  {key}:\n"
        f"{VALID_HOOK}"
        "  on_create:\n"
        f"{VALID_HOOK}"
    )


@pytest.mark.parametrize(("key", "names"), UNKNOWN_EVENTS)
def test_unknown_event_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    key: str,
    names: tuple[str, ...],
) -> None:
    _record_action_contexts(monkeypatch)
    engine, path = _load(tmp_path, _unknown_doc(key))
    _trigger(engine, _ctx())
    warning = _one_warning(warnings_log)
    assert path.name in warning, f"warning doesn't name the file: {warning!r}"
    assert any(n in warning for n in names), f"warning doesn't name {key!r}: {warning!r}"
    assert "not a hook event" in warning, warning


@pytest.mark.parametrize(("key", "names"), UNKNOWN_EVENTS)
def test_unknown_event_dropped_valid_event_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, names: tuple[str, ...]
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _unknown_doc(key))
    assert engine.is_configured
    assert set(engine._hooks_config) == {"on_create"}, (
        f"unknown event kept: {list(engine._hooks_config)!r}"
    )
    _trigger(engine, _ctx())
    assert len(seen) == 1, f"the valid on_create hook ran {len(seen)} times, want 1"
    assert engine._depth == 0


@pytest.mark.parametrize(("key", "names"), UNKNOWN_EVENTS)
def test_unknown_event_not_counted_in_loaded(
    tmp_path: Path, info_log: pytest.LogCaptureFixture, key: str, names: tuple[str, ...]
) -> None:
    _load(tmp_path, _unknown_doc(key))
    loaded = _loaded_messages(info_log)
    assert len(loaded) == 1, loaded
    assert loaded[0].startswith("Loaded 1 hook(s)"), loaded[0]


def test_only_unknown_events_is_unconfigured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _doc("\n  on_created:\n" f"{VALID_HOOK}"))
    assert not engine.is_configured
    _trigger(engine, _ctx())
    assert seen == []
    assert "not a hook event" in _one_warning(warnings_log)


def test_unknown_event_with_bad_value_one_warning(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    """`on_created: 5` — one warning (the key is not an event), not two."""
    engine, _ = _load(tmp_path, _doc("\n  on_created: 5\n  on_create:\n" f"{VALID_HOOK}"))
    _trigger(engine, _ctx())
    warning = _one_warning(warnings_log)
    assert "on_created" in warning and "not a hook event" in warning, warning


# --- 2. lone-string item_types --------------------------------------------------------


def _item_types_doc(value: str) -> str:
    return _doc(
        "\n"
        "  on_create:\n"
        f"    - item_types: {value}\n"
        "      actions:\n"
        "        - type: log\n"
    )


def test_lone_string_item_types_fires_silently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _item_types_doc("expedition"))
    assert engine.is_configured, "`item_types: expedition` was dropped"
    _trigger(engine, _expedition_ctx())
    assert len(seen) == 1, f"hook ran {len(seen)} times for an expedition, want 1"
    assert _warnings(warnings_log) == []


def test_lone_string_item_types_excludes_other_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _item_types_doc("expedition"))
    assert engine.is_configured, "`item_types: expedition` was dropped"
    _trigger(engine, _expedition_ctx("chore"))
    assert seen == [], "hook fired for a chore"


@pytest.mark.parametrize("item_type", ["expedition", "exp", "e", ""])
def test_lone_string_item_types_exact_not_substring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    item_type: str,
) -> None:
    """`item_types: exp` loads (it's a valid shape) but matches only `exp`."""
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _item_types_doc("exp"))
    assert engine.is_configured, "`item_types: exp` was dropped"
    _trigger(engine, _expedition_ctx(item_type))
    want = 1 if item_type == "exp" else 0
    assert len(seen) == want, f"`exp` vs {item_type!r}: ran {len(seen)}, want {want}"
    assert _warnings(warnings_log) == []


def test_lone_string_item_types_same_as_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A string and a one-element list fire for exactly the same item types."""
    seen = _record_action_contexts(monkeypatch)
    (tmp_path / "s").mkdir()
    (tmp_path / "l").mkdir()
    as_str, _ = _load(tmp_path / "s", _item_types_doc("expedition"))
    as_list, _ = _load(tmp_path / "l", _item_types_doc("[expedition]"))
    for item_type in ("expedition", "chore", "exp", "expeditions", "Expedition"):
        seen.clear()
        _trigger(as_str, _expedition_ctx(item_type))
        from_str = len(seen)
        seen.clear()
        _trigger(as_list, _expedition_ctx(item_type))
        assert from_str == len(seen), f"{item_type!r}: str ran {from_str}, list {len(seen)}"


# --- 3. YAML 1.1 booleans in from / to ------------------------------------------------

BOOL_FILTERS = [
    pytest.param("from", "yes", id="from-yes"),
    pytest.param("from", "no", id="from-no"),
    pytest.param("to", "on", id="to-on"),
    pytest.param("to", "off", id="to-off"),
    pytest.param("to", "true", id="to-true"),
]


def _status_doc(key: str, value: str) -> str:
    # the bool-filtered hook, then a valid on_status_change hook
    return _doc(
        "\n"
        "  on_status_change:\n"
        f"    - {key}: {value}\n"
        "      actions:\n"
        "        - type: log\n"
        f"{VALID_HOOK}"
    )


@pytest.mark.parametrize(("key", "value"), BOOL_FILTERS)
def test_bool_status_dropped_warns_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    key: str,
    value: str,
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, path = _load(tmp_path, _status_doc(key, value))
    engine.trigger(HookEvent.STATUS_CHANGE, _status_ctx())
    assert len(seen) == 1, f"log ran {len(seen)} times, want 1 (the valid hook)"
    warning = _one_warning(warnings_log)
    for name in (path.name, "on_status_change", key):
        assert name in warning, f"warning doesn't name {name!r}: {warning!r}"
    assert "quote" in warning.lower(), f"warning doesn't say to quote: {warning!r}"


def test_quoted_bool_status_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    """Control: the fix the warning suggests — `to: "on"` — works."""
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _status_doc("to", '"on"'))
    ctx = _status_ctx()
    ctx.new_status = "on"
    engine.trigger(HookEvent.STATUS_CHANGE, ctx)
    assert len(seen) == 2
    assert _warnings(warnings_log) == []


# --- 4. pins for the two surviving #429 mutations -------------------------------------


def test_hooks_list_warns_not_a_mapping(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    engine, path = _load(tmp_path, _doc(" [a, b]\n"))
    assert not engine.is_configured
    warning = _one_warning(warnings_log)
    assert "not a mapping" in warning, warning
    assert "Failed to load hooks config" not in warning, warning
    assert path.name in warning, warning


def test_event_null_is_silent_others_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(
        tmp_path, _doc("\n  on_create: null\n  on_status_change:\n" f"{VALID_HOOK}")
    )
    assert engine.is_configured
    _trigger(engine, _ctx())
    assert seen == []
    engine.trigger(HookEvent.STATUS_CHANGE, _status_ctx())
    assert len(seen) == 1
    assert _warnings(warnings_log) == []


# --- controls -------------------------------------------------------------------------


def test_control_valid_config_loads_unchanged(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    content = _doc(VALID_HOOKS_YAML)
    engine, _ = _load(tmp_path, content)
    assert engine._hooks_config == yaml.safe_load(content.split("---\n")[1])["hooks"]
    assert _warnings(warnings_log) == []


def test_control_valid_config_loaded_count(
    tmp_path: Path, info_log: pytest.LogCaptureFixture
) -> None:
    _load(tmp_path, _doc(VALID_HOOKS_YAML))
    loaded = _loaded_messages(info_log)
    assert len(loaded) == 1 and loaded[0].startswith("Loaded 4 hook(s)"), loaded
