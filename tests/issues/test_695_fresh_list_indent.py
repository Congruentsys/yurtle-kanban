# ruff: noqa: F811  -- the `repo` fixture imported from #596's module is re-bound as an arg
"""#695: a list written fresh (it had a multi-line entry) keeps the key's own item
indent, and the comment lines before its first item."""

from __future__ import annotations

from pathlib import Path

from tests.issues.test_596_frontmatter_keys_layout import (  # noqa: F401  (fixture)
    ITEM_ID,
    _service,
    _write,
    repo,
)

BASE = f"id: {ITEM_ID}\ntitle: T\ntype: feature\nstatus: backlog\n"


def _tags_block(text: str) -> list[str]:
    lines = text.split("---")[1].splitlines()
    start = lines.index("tags:")
    out = []
    for line in lines[start + 1:]:
        if line and not line.startswith((" ", "\t", "-", "#")):
            break
        out.append(line)
    return out


def test_fresh_write_keeps_the_keys_item_indent(repo: Path) -> None:
    path = _write(repo, BASE + "tags:\n  -\n    - a\n    - b\n  - plain\n")
    svc = _service(repo)
    svc.update_item(ITEM_ID, tags=svc.get_item(ITEM_ID).tags + ["x"])
    items = [ln for ln in _tags_block(path.read_text()) if ln.strip()]
    assert items and all(ln.startswith("  - ") for ln in items), items
    assert _service(repo).get_item(ITEM_ID).tags == ["[a, b]", "plain", "x"]


def test_fresh_write_keeps_leading_comment_lines(repo: Path) -> None:
    path = _write(repo, BASE + "tags:\n  # why these tags\n  - k: v\n    j: w\n  - plain\n")
    svc = _service(repo)
    svc.update_item(ITEM_ID, tags=svc.get_item(ITEM_ID).tags + ["x"])
    block = _tags_block(path.read_text())
    assert block[0] == "  # why these tags", block
    assert _service(repo).get_item(ITEM_ID).tags == ["{k: v, j: w}", "plain", "x"]


def test_dash_spacing_is_kept(repo: Path) -> None:
    """A list written `-   a` keeps its spacing on new items (#695 review)."""
    path = _write(repo, BASE + "tags:\n  -   a\n  -   b\n")
    svc = _service(repo)
    svc.update_item(ITEM_ID, tags=svc.get_item(ITEM_ID).tags + ["x"])
    assert "  -   x\n" in path.read_text(), path.read_text()


def test_dash_inside_a_block_scalar_is_not_the_list_dash(repo: Path) -> None:
    """The dash comes from the value's first item line only (#695 review)."""
    path = _write(repo, BASE + "tags:\n  note: |\n    -\n    text\n")
    svc = _service(repo)
    svc.update_item(ITEM_ID, tags=["a", "x"])
    assert "    - a" not in path.read_text(), path.read_text()
    assert _service(repo).get_item(ITEM_ID).tags == ["a", "x"]
