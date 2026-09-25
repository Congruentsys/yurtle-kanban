"""Issue #268 — frontmatter append: pin the C loader; tighten the regex fallback.

Follow-ups from the review of PR #264 (#249). When
`_add_or_update_frontmatter_field` APPENDS a missing key to frontmatter that
`safe_load` rejects (an unknown tag `x: !foo bar`) and a blank run sits before
the closing `---`, the writer falls back to looking at the last column-0 key:

1. The libyaml loader is the one in use when libyaml is available.
2. A column-0 `# comment` after a `n: |+` value ends the block scalar, so the
   blank run after the comment is not the scalar's: the key goes BEFORE the
   blank run (the #188 layout).
3. A plain value whose comment ends in `: |+` (`k: v #: |+`) is not a keep-chomp
   value: #188 layout.
4. Control: a genuine last `n: |+` (nothing after it) keeps the #249 layout —
   the key goes AFTER the blank run.

Out of scope: a nested `|+` (under an indented key) in unparseable frontmatter.
"""

from __future__ import annotations

import pytest
import yaml

from yurtle_kanban.service import KanbanService


def _append(content: str, field: str = "status", value: str = "done") -> str:
    svc = KanbanService.__new__(KanbanService)  # the writer uses no instance state
    return svc._add_or_update_frontmatter_field(content, field, value)


def _frontmatter(content: str) -> str:
    match = KanbanService._FRONTMATTER_RE.match(content)
    assert match, f"no frontmatter in {content!r}"
    return match.group(1)


def _doc(frontmatter: str) -> str:
    return "---\n" + frontmatter + "---\nbody\n"


def _unparseable(fm: str) -> str:
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(fm)  # precondition: the fallback path is taken
    return fm


# ---------------------------------------------------------------------------
# 1. The C loader is pinned
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(yaml, "CSafeLoader"), reason="libyaml not available")
def test_yaml_loader_is_csafeloader() -> None:
    assert KanbanService._YAML_LOADER is yaml.CSafeLoader


# ---------------------------------------------------------------------------
# 2. A column-0 comment after the `|+` value: #188 layout
# ---------------------------------------------------------------------------


def test_comment_after_keep_value_puts_key_before_blank_run() -> None:
    # The parseable twin shows the comment ends the scalar: the blank run is not kept.
    assert yaml.safe_load("n: |+\n  x\n# note\n\n") == {"n": "x\n"}
    fm = _unparseable("x: !foo bar\nn: |+\n  x\n# note\n\n")
    out = _append(_doc(fm))
    assert _frontmatter(out) == "x: !foo bar\nn: |+\n  x\n# note\nstatus: done\n\n", out
    assert out.endswith("---\nbody\n"), out


# ---------------------------------------------------------------------------
# 3. A plain value whose comment ends in `: |+`: #188 layout
# ---------------------------------------------------------------------------


def test_comment_ending_in_keep_indicator_is_not_keep_value() -> None:
    assert yaml.safe_load("k: v #: |+\n\n") == {"k": "v"}
    fm = _unparseable("x: !foo bar\nk: v #: |+\n\n")
    out = _append(_doc(fm))
    assert _frontmatter(out) == "x: !foo bar\nk: v #: |+\nstatus: done\n\n", out
    assert out.endswith("---\nbody\n"), out


# ---------------------------------------------------------------------------
# 4. Controls
# ---------------------------------------------------------------------------


def test_genuine_keep_value_last_puts_key_after_blank_run() -> None:
    fm = _unparseable("x: !foo bar\nn: |+\n  x\n\n")
    out = _append(_doc(fm))
    assert _frontmatter(out) == "x: !foo bar\nn: |+\n  x\n\nstatus: done\n", out


def test_genuine_keep_value_with_trailing_comment_on_key_line() -> None:
    # A comment on the `|+` header line itself is still a keep-chomp value.
    fm = _unparseable("x: !foo bar\nn: |+ # keep\n  x\n\n")
    out = _append(_doc(fm))
    assert _frontmatter(out) == "x: !foo bar\nn: |+ # keep\n  x\n\nstatus: done\n", out
