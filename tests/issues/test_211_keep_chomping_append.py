"""Issue #211 — follow-ups from the review of PR #209 (#188).

1. `_add_or_update_frontmatter_field` appending a missing key after a
   keep-chomping block scalar (`|+`, `>+`, with an optional indentation
   indicator) that ends the frontmatter keeps that value's trailing blank
   lines: they belong to the value, so the new key goes after them (right
   before the closing `---`) and the scalar parses exactly as before.
2. Non-keep block scalars (`|`, `|-`, `>`) and plain values keep #188's
   behaviour: the key goes after the last non-blank line, blank lines stay
   before `---` (controls).
3. The dead `gap = gap if not gap.strip() else ""` guard is removed.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
import yaml

from yurtle_kanban.service import KanbanService


def _svc() -> KanbanService:
    return KanbanService.__new__(KanbanService)  # the writer uses no instance state


def _append(content: str, field: str = "status", value: str = "done") -> str:
    return _svc()._add_or_update_frontmatter_field(content, field, value)


def _fm(content: str) -> dict[str, Any]:
    match = KanbanService._FRONTMATTER_RE.match(content)
    assert match, f"no frontmatter in {content!r}"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"frontmatter is not a mapping: {data!r}"
    return data


# ---------------------------------------------------------------------------
# 1. Keep-chomping block scalar ending the frontmatter
# ---------------------------------------------------------------------------

KEEP_CASES = [
    pytest.param("---\nid: X-1\nnote: |+\n  x\n\n---\nbody\n", "note", id="literal-keep"),
    pytest.param("---\nid: X-1\nnote: >+\n  x\n\n---\nbody\n", "note", id="folded-keep"),
    pytest.param("---\nid: X-1\nnote: |2+\n  x\n\n---\nbody\n", "note", id="indent-then-keep"),
    pytest.param("---\nid: X-1\nnote: |+2\n  x\n\n---\nbody\n", "note", id="keep-then-indent"),
    pytest.param(
        "---\nid: X-1\nnote: |+\n  x\n\n\n---\nbody\n", "note", id="literal-keep-two-blanks",
    ),
    pytest.param(
        "---\nid: X-1\nnote: |+\n  a\n\n  b\n\n---\nbody\n", "note", id="inner-and-trailing",
    ),
]


class TestKeepChompingAppendIssue211:
    @pytest.mark.parametrize(("content", "key"), KEEP_CASES)
    def test_block_scalar_value_unchanged(self, content: str, key: str):
        before = _fm(content)[key]
        assert before.endswith("\n\n")  # sanity: the kept newlines are there to lose
        after = _fm(_append(content))
        assert after[key] == before

    @pytest.mark.parametrize(("content", "key"), KEEP_CASES)
    def test_new_key_present(self, content: str, key: str):
        after = _fm(_append(content))
        assert after["status"] == "done"
        assert after["id"] == "X-1"

    def test_literal_keep_exact_text(self):
        content = "---\nid: X-1\nnote: |+\n  x\n\n---\nbody\n"
        assert _fm(content)["note"] == "x\n\n"
        out = _append(content)
        assert out == "---\nid: X-1\nnote: |+\n  x\n\nstatus: done\n---\nbody\n"
        assert _fm(out) == {"id": "X-1", "note": "x\n\n", "status": "done"}

    def test_folded_keep_exact_text(self):
        content = "---\nid: X-1\nnote: >+\n  x\n\n---\nbody\n"
        out = _append(content)
        assert out == "---\nid: X-1\nnote: >+\n  x\n\nstatus: done\n---\nbody\n"

    def test_indent_indicator_keep_exact_text(self):
        content = "---\nid: X-1\nnote: |2+\n  x\n\n---\nbody\n"
        out = _append(content)
        assert out == "---\nid: X-1\nnote: |2+\n  x\n\nstatus: done\n---\nbody\n"

    def test_body_untouched(self):
        content = "---\nid: X-1\nnote: |+\n  x\n\n---\nbody line\n\nmore\n"
        out = _append(content)
        assert out.endswith("\n---\nbody line\n\nmore\n")

    # -- controls ---------------------------------------------------------

    def test_control_keep_without_trailing_blank(self):
        content = "---\nid: X-1\nnote: |+\n  x\n---\nbody\n"
        out = _append(content)
        assert out == "---\nid: X-1\nnote: |+\n  x\nstatus: done\n---\nbody\n"
        assert _fm(out)["note"] == "x\n"

    def test_control_keep_scalar_not_last(self):
        content = "---\nnote: |+\n  x\n\nid: X-1\n\n---\nbody\n"
        before = _fm(content)["note"]
        out = _append(content)
        assert out == "---\nnote: |+\n  x\n\nid: X-1\nstatus: done\n\n---\nbody\n"
        assert _fm(out)["note"] == before


# ---------------------------------------------------------------------------
# 2. Non-keep block scalars and plain values keep #188's behaviour (controls)
# ---------------------------------------------------------------------------


class TestNonKeepAppendControlsIssue211:
    @pytest.mark.parametrize("indicator", ["|", "|-", ">", ">-", "|2"])
    def test_control_non_keep_block_scalar(self, indicator: str):
        content = f"---\nid: X-1\nnote: {indicator}\n  x\n\n---\nbody\n"
        before = _fm(content)["note"]
        out = _append(content)
        assert out == f"---\nid: X-1\nnote: {indicator}\n  x\nstatus: done\n\n---\nbody\n"
        after = _fm(out)
        assert after["note"] == before
        assert after["status"] == "done"

    def test_control_plain_value(self):
        content = "---\nid: X-1\ntitle: T\n\n---\nbody\n"
        out = _append(content)
        assert out == "---\nid: X-1\ntitle: T\nstatus: done\n\n---\nbody\n"

    def test_control_plain_value_with_plus_text(self):
        content = "---\nid: X-1\ntitle: a |+ b\n\n---\nbody\n"
        out = _append(content)
        assert out == "---\nid: X-1\ntitle: a |+ b\nstatus: done\n\n---\nbody\n"

    def test_control_update_existing_key(self):
        content = "---\nstatus: todo\nnote: |+\n  x\n\n---\nbody\n"
        out = _append(content)
        assert out == "---\nstatus: done\nnote: |+\n  x\n\n---\nbody\n"
        assert _fm(out)["note"] == "x\n\n"


# ---------------------------------------------------------------------------
# 3. Dead guard removed
# ---------------------------------------------------------------------------


def test_dead_gap_guard_removed():
    source = inspect.getsource(KanbanService._add_or_update_frontmatter_field)
    assert 'gap = gap if not gap.strip() else ""' not in source
