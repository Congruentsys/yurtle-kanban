"""Issue #964: a refused config value is named in full by `_shape`, so a 401-digit
`version` printed 401 digits (and past Python's 4300-digit limit `repr` raises).
Long reprs are cut; the refusal still names the type."""

from __future__ import annotations

import pytest

from yurtle_kanban.config import _shape, _version
from yurtle_kanban.models import InputRefused


def test_a_huge_int_is_cut() -> None:
    shown = _shape(10**400)
    assert shown.startswith("int "), shown
    assert len(shown) < 120, shown


def test_an_int_past_the_str_digits_limit_does_not_crash() -> None:
    shown = _shape(10**5000)
    assert shown.startswith("int "), shown
    assert len(shown) < 120, shown


def test_the_version_refusal_is_short() -> None:
    with pytest.raises(InputRefused) as caught:
        _version(10**400)
    assert len(str(caught.value)) < 200, str(caught.value)


def test_control_a_short_value_is_named_in_full() -> None:
    assert _shape(5) == "int 5"
    assert _shape([1]) == "list [1]"
