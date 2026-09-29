"""Issue #1011: `_shape` keeps the sign of an int too big to print."""

from __future__ import annotations

import sys

import pytest

from yurtle_kanban.config import _shape


@pytest.mark.skipif(not hasattr(sys, "get_int_max_str_digits"), reason="no str-digits limit")
def test_huge_negative_int_keeps_its_sign() -> None:
    assert _shape(-(10**5000)).startswith("int -<"), _shape(-(10**5000))
    assert _shape(10**5000).startswith("int <"), _shape(10**5000)
