"""Issue #440 — hooks for events that yurtle-kanban declares but never emits.

`HookEvent.STALE_DETECTED` (`on_stale`) and `HookEvent.WIP_EXCEEDED` (`on_wip_exceeded`)
have no `trigger(...)` call in src/, so hooks on them load (and are counted) but can
never fire: a silent no-op.

Decided ([steer] bucket-2 on #440), at `HookEngine` load from a hooks file:

1. For each event that is declared but not emitted, a file with hooks for it logs
   exactly ONE warning per event, naming the file and the event and saying the event
   isn't emitted yet, so the hooks won't run. The hooks are KEPT in `_hooks_config`
   (they start running once the event is wired). Emitted events get no such warning.
2. The unknown-event warning's `(known: …)` list names every `HookEvent` value.

Data-driven: `EMITTED` is the one list to extend when an emitter is wired;
`test_emitted_list_matches_src` keeps it honest against the `trigger(` calls in src/.

Controls: the #432 and #425 tests stay green; a config with only emitted events loads
with no warnings.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.issues.test_388_junk_root_no_crash import (  # noqa: F401 (fixtures)
    _record_action_contexts,
    _warnings,
    warnings_log,
)
from tests.issues.test_417_hook_warnings_guarded import _ctx
from tests.issues.test_425_hook_config_shapes import VALID_HOOK, _doc, _load
from yurtle_kanban.hooks import HookEvent

SRC = Path(__file__).resolve().parents[2] / "src" / "yurtle_kanban"

# The events something in src/ actually passes to `HookEngine.trigger()`.
# Wiring a new emitter (e.g. stale detection) = add its event here.
EMITTED: list[HookEvent] = [
    HookEvent.ITEM_CREATED,
    HookEvent.STATUS_CHANGE,
    HookEvent.ASSIGNED,
    HookEvent.BLOCKED,
]
UNEMITTED: list[HookEvent] = [e for e in HookEvent if e not in EMITTED]

_TRIGGER_RE = re.compile(r"\.trigger\(\s*HookEvent\.(\w+)")


def _event_doc(*events: str, hooks_each: int = 1) -> str:
    body = "\n"
    for event in events:
        body += f"  {event}:\n" + VALID_HOOK * hooks_each
    return _doc(body)


def _not_emitted_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [w for w in _warnings(caplog) if "emit" in w.lower()]


def _assert_not_emitted_warning(warning: str, path: Path, event: str) -> None:
    assert path.name in warning, f"warning doesn't name the file: {warning!r}"
    assert event in warning, f"warning doesn't name {event!r}: {warning!r}"
    low = warning.lower()
    assert "emit" in low, f"warning doesn't say the event isn't emitted: {warning!r}"
    assert "run" in low, f"warning doesn't say the hooks won't run: {warning!r}"
    assert "not a hook event" not in warning, f"treated as unknown: {warning!r}"


# --- the emitted set, verified against src/ -------------------------------------------


def test_emitted_list_matches_src() -> None:
    """`EMITTED` is exactly the events src/ passes to `trigger(...)`."""
    found: set[str] = set()
    for py in SRC.rglob("*.py"):
        found |= set(_TRIGGER_RE.findall(py.read_text(encoding="utf-8")))
    assert found, "no trigger(HookEvent.X) calls found in src/ — regex stale?"
    assert found == {e.name for e in EMITTED}, (
        f"src/ triggers {sorted(found)}, EMITTED lists {sorted(e.name for e in EMITTED)}"
    )


def test_some_event_is_unemitted() -> None:
    """Sanity: the decision only bites while some declared event has no emitter."""
    assert UNEMITTED, "every HookEvent is emitted — #440's warning is moot"


# --- 1. unemitted events warn once, and are kept --------------------------------------


@pytest.mark.parametrize("event", [e.value for e in UNEMITTED])
def test_unemitted_event_one_warning(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    event: str,
) -> None:
    _, path = _load(tmp_path, _event_doc(event, "on_create"))
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning for {event!r}, got {warned!r}"
    _assert_not_emitted_warning(warned[0], path, event)


@pytest.mark.parametrize("event", [e.value for e in UNEMITTED])
def test_unemitted_event_one_warning_per_event_not_per_hook(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    event: str,
) -> None:
    """Three hooks on one unemitted event -> still ONE warning."""
    _, path = _load(tmp_path, _event_doc(event, hooks_each=3))
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning for 3 {event!r} hooks, got {warned!r}"
    _assert_not_emitted_warning(warned[0], path, event)


def test_all_unemitted_events_one_warning_each(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    events = [e.value for e in UNEMITTED]
    _, path = _load(tmp_path, _event_doc(*events, "on_create", hooks_each=2))
    warned = _warnings(warnings_log)
    assert len(warned) == len(events), f"want one warning per {events!r}, got {warned!r}"
    for event in events:
        naming = [w for w in warned if event in w]
        assert len(naming) == 1, f"{event!r} named by {len(naming)} warnings: {warned!r}"
        _assert_not_emitted_warning(naming[0], path, event)


@pytest.mark.parametrize("event", [e.value for e in UNEMITTED])
def test_unemitted_event_hooks_kept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
) -> None:
    """Kept in `_hooks_config`, and they run the moment the event is triggered."""
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _event_doc(event, hooks_each=2))
    assert engine.is_configured
    assert event in engine._hooks_config, f"{event!r} dropped: {engine._hooks_config!r}"
    assert len(engine._hooks_config[event]) == 2
    ctx = _ctx()
    ctx.item_type = "expedition"
    engine.trigger(HookEvent(event), ctx)
    assert len(seen) == 2, f"kept {event!r} hooks ran {len(seen)} times, want 2"


@pytest.mark.parametrize("value", ["null", "[]"])
@pytest.mark.parametrize("event", [e.value for e in UNEMITTED])
def test_unemitted_event_without_hooks_is_silent(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    event: str,
    value: str,
) -> None:
    """No hooks for the event -> nothing that won't run -> no warning."""
    _load(tmp_path, _doc(f"\n  {event}: {value}\n  on_create:\n{VALID_HOOK}"))
    assert _not_emitted_warnings(warnings_log) == []


# --- emitted events: no such warning --------------------------------------------------


@pytest.mark.parametrize("event", [e.value for e in EMITTED])
def test_emitted_event_no_warning(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    event: str,
) -> None:
    _load(tmp_path, _event_doc(event, hooks_each=2))
    assert _warnings(warnings_log) == []


def test_control_only_emitted_events_no_warnings(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    engine, _ = _load(tmp_path, _event_doc(*(e.value for e in EMITTED)))
    assert set(engine._hooks_config) == {e.value for e in EMITTED}
    assert _warnings(warnings_log) == []


# --- 2. the unknown-event warning's (known: …) list -----------------------------------


def test_unknown_event_known_list_is_every_hook_event(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    _load(tmp_path, _event_doc("on_created", "on_create"))
    warned = _warnings(warnings_log)
    assert len(warned) == 1, warned
    match = re.search(r"\(known: ([^)]*)\)", warned[0])
    assert match, f"no `(known: …)` list in: {warned[0]!r}"
    listed = [s.strip() for s in match.group(1).split(",")]
    assert sorted(listed) == sorted(e.value for e in HookEvent), (
        f"known list {listed!r} != every HookEvent value"
    )
