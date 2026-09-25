"""Issue #232 — frontmatter append edge cases (follow-ups from PR #228 / #211).

When `_add_or_update_frontmatter_field` APPENDS a missing key, the parsed YAML of
every existing key is unchanged (yaml.safe_load before vs after, minus the new key):

1. Trailing spaces on the last content line of a block scalar are kept (the
   append used to `rstrip()` them away, changing the value).
2. Keep-chomping (`|+`) detection covers other last-value forms: a quoted key, a
   nested key, a list item, a key starting with `-`. Each keeps its trailing
   newlines.
3. Only a column-0 key counts: a `|+`-looking line INSIDE a plain `|` scalar
   keeps #188's layout exactly (control).

The #188 / #211 exact-text expectations live in their own files (controls).
"""

from __future__ import annotations

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


def _doc(frontmatter: str) -> str:
    return "---\nid: X-1\n" + frontmatter + "---\nbody\n"


def _assert_append_preserves(content: str) -> str:
    before = _fm(content)
    assert "status" not in before
    out = _append(content)
    after = _fm(out)
    assert after.pop("status") == "done", out
    assert after == before, f"existing values changed:\n{content!r}\n->\n{out!r}"
    assert out.endswith("---\nbody\n"), out
    return out


# ---------------------------------------------------------------------------
# 1. Trailing spaces on the last content line of a block scalar
# ---------------------------------------------------------------------------

SPACES_CASES = [
    # (frontmatter after `id`, expected value of `n`, expected frontmatter after append)
    pytest.param(
        "n: |\n  x  \n", "x  \n", "n: |\n  x  \nstatus: done\n", id="literal-clip",
    ),
    pytest.param(
        "n: |\n  x  \n\n", "x  \n", "n: |\n  x  \nstatus: done\n\n",
        id="literal-clip-blank-before-close",
    ),
    pytest.param(
        "n: |+\n  x  \n", "x  \n", "n: |+\n  x  \nstatus: done\n",
        id="literal-keep-no-blank",
    ),
    pytest.param(
        "n: |+\n  x  \n\n", "x  \n\n", "n: |+\n  x  \n\nstatus: done\n",
        id="literal-keep-with-blank",
    ),
    pytest.param(
        "n: |-\n  x  \n", "x  ", "n: |-\n  x  \nstatus: done\n", id="literal-strip",
    ),
]


class TestTrailingSpacesIssue232:
    @pytest.mark.parametrize(("fm", "value", "expected"), SPACES_CASES)
    def test_value_keeps_trailing_spaces(self, fm: str, value: str, expected: str) -> None:
        content = _doc(fm)
        assert _fm(content)["n"] == value  # sanity: the fixture means what we think
        out = _assert_append_preserves(content)
        assert _fm(out)["n"] == value

    @pytest.mark.parametrize(("fm", "value", "expected"), SPACES_CASES)
    def test_exact_text(self, fm: str, value: str, expected: str) -> None:
        assert _append(_doc(fm)) == _doc(expected)

    def test_spaces_on_every_line_kept(self) -> None:
        # inner lines were always kept; the last one lost its spaces
        content = _doc("n: |\n  a  \n  b  \n")
        out = _assert_append_preserves(content)
        assert _fm(out)["n"] == "a  \nb  \n"


# ---------------------------------------------------------------------------
# 2. Keep-scalar detection for other last-value forms
# ---------------------------------------------------------------------------

KEEP_FORM_CASES = [
    pytest.param('"a: b": |+\n  x\n\n', ("a: b",), id="double-quoted-key"),
    pytest.param("'a: b': |+\n  x\n\n", ("a: b",), id="single-quoted-key"),
    pytest.param("m:\n  n: |+\n    x\n\n", ("m", "n"), id="nested-key"),
    pytest.param("l:\n- a: |+\n    x\n\n", ("l", 0, "a"), id="column-0-list-item"),
    pytest.param("l:\n  - a: |+\n      x\n\n", ("l", 0, "a"), id="indented-list-item"),
    pytest.param("-a: |+\n  x\n\n", ("-a",), id="dash-key"),
    pytest.param("--a: |+\n  x\n\n", ("--a",), id="double-dash-key"),
    pytest.param('"a: b": |+\n  x\n\n\n', ("a: b",), id="quoted-key-two-blanks"),
    pytest.param("m:\n  n: >+\n    x\n\n", ("m", "n"), id="nested-folded-keep"),
]


def _dig(data: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        data = data[step]
    return data


class TestKeepFormsIssue232:
    @pytest.mark.parametrize(("fm", "path"), KEEP_FORM_CASES)
    def test_kept_newlines_survive_append(self, fm: str, path: tuple[Any, ...]) -> None:
        content = _doc(fm)
        try:
            before = _fm(content)
        except yaml.YAMLError:  # pragma: no cover - fixture guard
            pytest.skip("YAML does not parse this form")
        value = _dig(before, path)
        if not (isinstance(value, str) and value.endswith("\n\n")):
            pytest.skip(f"YAML reads this form differently: {value!r}")
        out = _assert_append_preserves(content)
        assert _dig(_fm(out), path) == value


# ---------------------------------------------------------------------------
# 3. Column-0-only rule (control, #188 layout)
# ---------------------------------------------------------------------------


class TestColumnZeroRuleIssue232:
    def test_keep_looking_line_inside_plain_literal(self) -> None:
        content = _doc("note: |\n  a: |+\n\n")
        out = _assert_append_preserves(content)
        # `  a: |+` is text inside `note`, not a key: #188's layout, blank before ---
        assert out == _doc("note: |\n  a: |+\nstatus: done\n\n")
        assert _fm(out)["note"] == "a: |+\n"
