# ruff: noqa: F811  (the borrowed `root` fixture)
"""Issue #973: the other direction of #938's pin. `epic add`'s found-item warnings
name the file's own spelling of the id (`item.id`, #751): an NFD-spelled `id:` is
printed NFD even when the argument is typed NFC, so `fold_id(item_id)` would fail."""

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
def test_warning_names_the_files_own_nfd_spelling(
    root: Path, monkeypatch: pytest.MonkeyPatch, text: str, words: str
) -> None:
    """The file spells its `id:` NFD and the argument is typed NFC: the warning names
    the item as its file spells it (#751), not the folded argument."""
    _write(root, "EPIC-001", "Epic", kind="epic")
    path = _write(root, NFD_ITEM, "Item")
    before = path.read_bytes()
    _reads_as(monkeypatch, path, text)
    result = _invoke(["epic", "add", "EPIC-001", NFC_ITEM])
    _no_traceback(result)
    assert words in result.output, result.output
    assert NFD_ITEM in result.output, result.output
    assert NFC_ITEM not in result.output, result.output  # not both spellings (#1007)
    assert path.read_bytes() == before, "the item file was written"
