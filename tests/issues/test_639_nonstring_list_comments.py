# ruff: noqa: F811  -- the `repo` fixture imported from #596's module is re-bound as an arg
"""Issue #639 — follow-ups to #619's frontmatter comment keeping.

1. `_block_list_lines` matches a kept block-list item by its YAML-parsed value, so an
   item YAML reads as a non-string (`- 2026`, `- yes`, `- 1.5`, `- null`) never
   equals its string tag. The item is kept, but its trailing comment is dropped and
   its line is rewritten quoted (`- "2026"`). A kept item must keep its old line:
   its comment and its original unquoted spelling.
2. Removing such an item drops its line and its comment (as for a string item).
3. `_trailing_comment` and `_block_list_lines` catch only `YAMLError`/`TypeError`,
   but the safe loader raises `ValueError` for an invalid date (`2026-02-30 # x`).
   Unreachable end to end today, so the helpers are called directly: they must not
   raise.

The blocks carry a full-line comment (`# c`) so the anchoring path of
`_block_list_lines` is the one under test (#619 decision 2). Fixtures are #596's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_596_frontmatter_keys_layout import (  # noqa: F401  (fixture)
    FM_TAIL,
    ITEM_ID,
    _service,
    _write,
    repo,
)
from yurtle_kanban.service import KanbanService

HEAD = 'id: FEAT-001\ntitle: "Seed"\ntype: feature\nstatus: backlog\npriority: high\n'

BLOCK = (
    "tags:\n"
    "  # c\n"
    "  - a # s\n"
    "  - 2026 # year\n"
    "  - yes # flag\n"
    "  - 1.5 # v\n"
    "  - null # n\n"
)
TAGS = ["a", "2026", "yes", "1.5", "null"]


def _edit(repo: Path, block: str, **kwargs: object) -> tuple[str, str]:
    path = _write(repo, HEAD + block + FM_TAIL)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, commit=False, **kwargs)  # type: ignore[arg-type]
    return old, path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. kept non-string items keep their comment and spelling
# ---------------------------------------------------------------------------


def test_unrelated_field_edit_leaves_list_alone(repo: Path) -> None:
    """Control: an edit to another field doesn't touch the tags block."""
    old, new = _edit(repo, BLOCK, title="Renamed")
    expected = old.replace('title: "Seed"', 'title: "Renamed"').replace("# Seed\n", "# Renamed\n")
    assert new == expected, new
    assert BLOCK in new, new


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        pytest.param(
            [*TAGS, "x"],
            BLOCK + "  - x\n",
            id="add-item",
        ),
        pytest.param(
            ["x", *TAGS],
            "tags:\n  - x\n  # c\n  - a # s\n  - 2026 # year\n  - yes # flag\n"
            "  - 1.5 # v\n  - null # n\n",
            id="add-item-first",
        ),
        pytest.param(
            ["2026", "yes", "1.5", "null"],
            "tags:\n  - 2026 # year\n  - yes # flag\n  - 1.5 # v\n  - null # n\n",
            id="remove-string-item",
        ),
        pytest.param(
            ["a", "2026", "1.5", "null"],
            "tags:\n  # c\n  - a # s\n  - 2026 # year\n  - 1.5 # v\n  - null # n\n",
            id="remove-yes-item",
        ),
    ],
)
def test_nonstring_items_keep_comment_and_spelling(
    repo: Path, tags: list[str], expected: str
) -> None:
    old, new = _edit(repo, BLOCK, tags=tags)
    assert '"2026"' not in new and '"yes"' not in new, f"kept item re-quoted:\n{new}"
    assert new == old.replace(BLOCK, expected), f"kept item lost its line:\n{new}"


# ---------------------------------------------------------------------------
# 2. removing a non-string item drops its line and its comment
# ---------------------------------------------------------------------------


def test_removed_nonstring_item_drops_line_and_comment(repo: Path) -> None:
    old, new = _edit(repo, BLOCK, tags=["a", "yes", "1.5", "null"])
    assert "2026" not in new and "# year" not in new, new
    expected = "tags:\n  # c\n  - a # s\n  - yes # flag\n  - 1.5 # v\n  - null # n\n"
    assert new == old.replace(BLOCK, expected), new


# ---------------------------------------------------------------------------
# 3. a ValueError from the safe loader doesn't escape the helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("head", [" 2026-02-30 # x", " 2026-02-30  # x"])
def test_trailing_comment_invalid_date_does_not_raise(head: str) -> None:
    KanbanService._trailing_comment(head)


@pytest.mark.parametrize(
    "rest",
    [
        pytest.param("\n  # c\n  - 2026-02-30 # x\n  - a", id="with-comment"),
        pytest.param("\n  # c\n  - 2026-02-30\n  - a", id="bare-date"),
    ],
)
def test_block_list_lines_invalid_date_does_not_raise(rest: str) -> None:
    out = KanbanService._block_list_lines(rest, "  - ", ["a"])
    assert out.endswith("\n  - a"), out
    assert "2026-02-30" not in out, out


# ---------------------------------------------------------------------------
# 4. a list with no full-line comment keeps its items' trailing comments too
# ---------------------------------------------------------------------------

BARE = "tags:\n  - a # s\n  - b\n  - 2026 # year\n"


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        pytest.param(
            ["a", "b", "2026", "x"],
            BARE + "  - x\n",
            id="add-item",
        ),
        pytest.param(
            ["a", "2026"],
            "tags:\n  - a # s\n  - 2026 # year\n",
            id="remove-b",
        ),
    ],
)
def test_no_comment_line_items_keep_comment_and_spelling(
    repo: Path, tags: list[str], expected: str
) -> None:
    old, new = _edit(repo, BARE, tags=tags)
    assert new == old.replace(BARE, expected), f"item comment/spelling lost:\n{new}"
