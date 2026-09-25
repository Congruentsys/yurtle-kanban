"""Issue #249 — frontmatter append: fast path, NaN / unparseable fallbacks, speed.

Follow-ups from the review of PR #246 (#232). When
`_add_or_update_frontmatter_field` APPENDS a missing key:

1. Fast path: with no blank run before the closing `---` (the common case) both
   layouts are the same string, so no YAML is parsed at all.
2. A top-level `.nan` value (`nan != nan`) no longer defeats the value check: a
   column-0 `|+` last value still gets the keep layout (key after the blank run).
3. Unparseable frontmatter (an unknown tag `!foo`) falls back to the old column-0
   `|+` rule: the key goes after the blank run, keeping the kept newline (#211).
4. Performance guard: a 10 KB frontmatter with a blank run appends quickly
   (CSafeLoader when available).

The #188 / #211 / #232 files are the controls.
"""

from __future__ import annotations

import math
import time
from typing import Any

import pytest
import yaml

import yurtle_kanban.service as service_mod
from yurtle_kanban.service import KanbanService


def _svc() -> KanbanService:
    return KanbanService.__new__(KanbanService)  # the writer uses no instance state


def _append(content: str, field: str = "status", value: str = "done") -> str:
    return _svc()._add_or_update_frontmatter_field(content, field, value)


def _frontmatter(content: str) -> str:
    match = KanbanService._FRONTMATTER_RE.match(content)
    assert match, f"no frontmatter in {content!r}"
    return match.group(1)


def _fm(content: str) -> dict[str, Any]:
    data = yaml.safe_load(_frontmatter(content))
    assert isinstance(data, dict), f"frontmatter is not a mapping: {data!r}"
    return data


def _doc(frontmatter: str) -> str:
    return "---\n" + frontmatter + "---\nbody\n"


# ---------------------------------------------------------------------------
# 1. Fast path: no blank run -> no YAML parsing
# ---------------------------------------------------------------------------


def _forbid_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> Any:
        raise AssertionError("YAML was parsed on the no-blank-run fast path")

    ymod = service_mod.yaml
    for name in (
        "safe_load", "load", "full_load", "unsafe_load",
        "safe_load_all", "load_all", "compose", "compose_all", "parse", "scan",
    ):
        if hasattr(ymod, name):
            monkeypatch.setattr(ymod, name, boom)
    # Any loader class (Python or libyaml, even one bound at import time)
    # constructs its document through BaseConstructor.
    monkeypatch.setattr(yaml.constructor.BaseConstructor, "get_single_data", boom)
    monkeypatch.setattr(yaml.constructor.BaseConstructor, "construct_document", boom)


def test_no_blank_run_appends_without_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_yaml(monkeypatch)
    out = _append(_doc("a: 1\nb: 2\n"), "new", "x")
    assert _frontmatter(out) == "a: 1\nb: 2\nnew: x\n", out
    assert out == "---\na: 1\nb: 2\nnew: x\n---\nbody\n"


def test_no_blank_run_update_path_also_parse_free(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: updating an existing key never parsed YAML.
    _forbid_yaml(monkeypatch)
    out = _append(_doc("a: 1\nb: 2\n"), "b", "3")
    assert out == "---\na: 1\nb: 3\n---\nbody\n"


# ---------------------------------------------------------------------------
# 2. Top-level NaN no longer forces layout A
# ---------------------------------------------------------------------------


def test_nan_value_keeps_keep_chomping_layout() -> None:
    content = _doc("f: .nan\nn: |+\n  x\n\n")
    out = _append(content)
    after = _fm(out)
    assert after["n"] == "x\n\n", f"kept newline lost:\n{out!r}"
    assert math.isnan(after["f"]), out
    assert after["status"] == "done", out
    assert _frontmatter(out) == "f: .nan\nn: |+\n  x\n\nstatus: done\n", out


# ---------------------------------------------------------------------------
# 3. Unparseable frontmatter falls back to the column-0 `|+` rule
# ---------------------------------------------------------------------------


def test_unknown_tag_keeps_keep_chomping_layout() -> None:
    fm = "x: !foo bar\nn: |+\n  x\n\n"
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(fm)  # precondition: safe_load rejects it
    out = _append(_doc(fm))
    assert _frontmatter(out) == "x: !foo bar\nn: |+\n  x\n\nstatus: done\n", out
    assert out.endswith("---\nbody\n"), out


def test_unknown_tag_without_keep_chomp_keeps_gap_before_close() -> None:
    # Control (#188 layout): a plain last value -> key before the blank run.
    fm = "x: !foo bar\nn: plain\n\n"
    out = _append(_doc(fm))
    assert _frontmatter(out) == "x: !foo bar\nn: plain\nstatus: done\n\n", out


# ---------------------------------------------------------------------------
# 4. Performance guard: 10 KB frontmatter with a blank run
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(yaml, "CSafeLoader"), reason="libyaml not available")
def test_large_frontmatter_append_is_fast() -> None:
    lines = [f"key_{i:04d}: value number {i} with some padding text\n" for i in range(220)]
    fm = "".join(lines) + "n: |+\n  x\n\n"
    assert len(fm) >= 10_000
    content = _doc(fm)
    _append(content)  # warm-up
    runs = 20
    start = time.perf_counter()
    for _ in range(runs):
        out = _append(content)
    avg_ms = (time.perf_counter() - start) / runs * 1000
    assert _fm(out)["n"] == "x\n\n", out[-80:]
    assert avg_ms < 50, f"append took {avg_ms:.1f} ms on average (limit 50 ms)"
