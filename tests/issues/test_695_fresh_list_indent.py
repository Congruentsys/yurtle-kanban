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
