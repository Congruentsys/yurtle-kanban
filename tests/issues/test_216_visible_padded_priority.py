"""#216: the unknown-priority message makes an empty or whitespace-padded value
visible. A string is shown raw only when it is printable, non-empty, and equal to
its own strip(); otherwise it is shown as its repr, so `'Urgent '` keeps its
trailing space and `''` is not an empty slot. `urgent` stays byte-identical."""

from __future__ import annotations

from typing import Any

import pytest

from yurtle_kanban.models import unknown_priority_message
from yurtle_kanban.service import KanbanService

VALID = "valid: critical, high, medium, low"

# Values that must be shown as their repr
VISIBLE: list[tuple[str, str]] = [
    ("Urgent ", f"Unknown priority: 'Urgent '; {VALID}"),
    (" urgent", f"Unknown priority: ' urgent'; {VALID}"),
    ("", f"Unknown priority: ''; {VALID}"),
    (" ", f"Unknown priority: ' '; {VALID}"),
    ("\turgent", f"Unknown priority: '\\turgent'; {VALID}"),
]
VISIBLE_IDS = ["trailing-space", "leading-space", "empty", "space", "leading-tab"]

# Controls: already correct before #216
RAW: list[tuple[str, str]] = [
    ("urgent", f"Unknown priority: urgent; {VALID}"),
    ("café", f"Unknown priority: café; {VALID}"),
    ("Urgent", f"Unknown priority: Urgent; {VALID}"),
]
NON_STRINGS: list[tuple[Any, str]] = [
    (5, f"Unknown priority: 5; {VALID}"),
    (True, f"Unknown priority: True; {VALID}"),
    (["high"], f"Unknown priority: ['high']; {VALID}"),
    ("a\x1bb", f"Unknown priority: 'a\\x1bb'; {VALID}"),
]


class TestHelperMakesPaddingVisible:
    @pytest.mark.parametrize(("value", "expected"), VISIBLE, ids=VISIBLE_IDS)
    def test_repr_for_empty_or_padded(self, value: str, expected: str) -> None:
        assert unknown_priority_message(value) == expected


class TestNormalizeRejectsWithVisibleValue:
    @pytest.mark.parametrize(("value", "expected"), VISIBLE, ids=VISIBLE_IDS)
    def test_rejected_message(self, value: str, expected: str) -> None:
        with pytest.raises(ValueError) as exc:
            KanbanService._normalize_priority(value)
        assert str(exc.value) == expected


class TestControls:
    @pytest.mark.parametrize(("value", "expected"), RAW, ids=["urgent", "cafe", "Urgent"])
    def test_helper_raw_unchanged(self, value: str, expected: str) -> None:
        assert unknown_priority_message(value) == expected

    @pytest.mark.parametrize(("value", "expected"), RAW, ids=["urgent", "cafe", "Urgent"])
    def test_normalize_raw_unchanged(self, value: str, expected: str) -> None:
        with pytest.raises(ValueError) as exc:
            KanbanService._normalize_priority(value)
        assert str(exc.value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"), NON_STRINGS, ids=["int", "bool", "list", "ctrl"]
    )
    def test_helper_repr_unchanged(self, value: Any, expected: str) -> None:
        assert unknown_priority_message(value) == expected

    @pytest.mark.parametrize("value", ["high ", " high", "\tHIGH\n", "Low "])
    def test_padded_valid_priority_still_accepted(self, value: str) -> None:
        assert KanbanService._normalize_priority(value) == value.strip().lower()

    def test_none_still_no_priority(self) -> None:
        assert KanbanService._normalize_priority(None) is None
