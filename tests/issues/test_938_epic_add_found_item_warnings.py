# ruff: noqa: F811  (the borrowed `root` fixture)
"""Issue #938: `epic add`'s warnings about a FOUND item name the item's own id
(`item.id`, the file's spelling), never the raw argument: "No frontmatter in X"
and "X's frontmatter doesn't parse". The item is found by the scan, then its
file reads differently when the link is written (edited in between), which is
when these warnings fire."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_904_epic_leftovers import (  # noqa: F401 (fixture)
    NFC_ITEM,
    NFD_ITEM,
    _invoke,
    _no_traceback,
    _write,
    root,
)
from yurtle_kanban.service import KanbanService


def _reads_as(monkeypatch: pytest.MonkeyPatch, path: Path, text: str) -> None:
    real = KanbanService._read_item_text

    def read(p: Path) -> tuple[str, str]:
        return (text, "\n") if Path(p) == path else real(p)

    monkeypatch.setattr(KanbanService, "_read_item_text", staticmethod(read))


@pytest.mark.parametrize(
    "text,words",
    [("Just a note, no frontmatter.\n", "No frontmatter"),
     ("---\ntitle: [unclosed\n---\n\nBody\n", "doesn't parse")],
    ids=["no-frontmatter", "broken-frontmatter"],
)
def test_found_item_warning_names_its_own_id(
    root: Path, monkeypatch: pytest.MonkeyPatch, text: str, words: str
) -> None:
    _write(root, "EPIC-001", "Epic", kind="epic")
    path = _write(root, NFC_ITEM, "Item")
    before = path.read_bytes()
    _reads_as(monkeypatch, path, text)
    result = _invoke(["epic", "add", "EPIC-001", NFD_ITEM])
    _no_traceback(result)
    assert words in result.output, result.output
    assert NFC_ITEM in result.output, result.output
    assert NFD_ITEM not in result.output, result.output
    assert path.read_bytes() == before, "the item file was written"
