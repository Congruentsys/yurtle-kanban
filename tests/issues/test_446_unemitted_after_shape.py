"""Issue #446 — the "declared but not emitted yet" warning (#440) fires before the
shape check, so a malformed `on_stale` / `on_wip_exceeded` value gets two warnings,
one of them for the wrong reason.

Decided: the not-emitted warning is logged only when at least ONE valid hook for that
event survives the shape checks.

- `on_stale: "foo"` (not a list) -> exactly one warning, the not-a-list one.
- `on_stale: [just-a-string, 5]` (every hook invalid) -> only the per-hook shape
  warnings, no not-emitted warning.
- `on_stale: [{actions: [{type: log}]}, "junk"]` -> one shape warning for "junk" plus
  one not-emitted warning.

Controls: the #440 tests stay green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_388_junk_root_no_crash import (  # noqa: F401 (fixtures)
    _warnings,
    warnings_log,
)
from tests.issues.test_425_hook_config_shapes import _doc, _load
from tests.issues.test_440_unemitted_hook_events import (
    UNEMITTED,
    _assert_not_emitted_warning,
    _not_emitted_warnings,
)

EVENTS = [e.value for e in UNEMITTED]


# --- not a list: only the not-a-list warning ------------------------------------------


@pytest.mark.parametrize("event", EVENTS)
def test_not_a_list_only_shape_warning(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    event: str,
) -> None:
    _, path = _load(tmp_path, _doc(f'\n  {event}: "foo"\n'))
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected only the not-a-list warning, got {warned!r}"
    assert "not a list" in warned[0], warned[0]
    assert event in warned[0] and path.name in warned[0], warned[0]
    assert _not_emitted_warnings(warnings_log) == []


# --- every hook invalid: only the per-hook shape warnings -----------------------------


@pytest.mark.parametrize(
    "entries, n_bad",
    [
        ("    - just-a-string\n    - 5\n", 2),
        ("    - actions: log\n", 1),  # a mapping, but its `actions` is not a list
    ],
    ids=["non-mappings", "bad-field"],
)
@pytest.mark.parametrize("event", EVENTS)
def test_all_hooks_invalid_no_not_emitted_warning(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    event: str,
    entries: str,
    n_bad: int,
) -> None:
    engine, _ = _load(tmp_path, _doc(f"\n  {event}:\n{entries}"))
    warned = _warnings(warnings_log)
    assert _not_emitted_warnings(warnings_log) == [], (
        f"no valid {event!r} hook survived, yet warned not-emitted: {warned!r}"
    )
    assert len(warned) == n_bad, f"expected {n_bad} shape warning(s), got {warned!r}"
    for n in range(1, n_bad + 1):
        assert any(f"hook {n}:" in w for w in warned), f"no warning for hook {n}: {warned!r}"
    assert not engine._hooks_config.get(event), engine._hooks_config


# --- one valid + one junk: one shape warning, one not-emitted warning ------------------


@pytest.mark.parametrize("event", EVENTS)
def test_one_valid_one_junk_both_warnings(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    event: str,
) -> None:
    engine, path = _load(
        tmp_path,
        _doc(f"\n  {event}:\n    - actions:\n        - type: log\n    - junk\n"),
    )
    warned = _warnings(warnings_log)
    assert len(warned) == 2, f"expected shape + not-emitted warnings, got {warned!r}"
    shape = [w for w in warned if "hook 2:" in w]
    assert len(shape) == 1, f"no single shape warning for 'junk': {warned!r}"
    not_emitted = _not_emitted_warnings(warnings_log)
    assert len(not_emitted) == 1, f"want one not-emitted warning, got {warned!r}"
    _assert_not_emitted_warning(not_emitted[0], path, event)
    assert len(engine._hooks_config[event]) == 1, engine._hooks_config
