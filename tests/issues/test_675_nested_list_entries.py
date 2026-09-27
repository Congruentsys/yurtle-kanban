# ruff: noqa: F811  -- the `repo` fixture imported from #596's module is re-bound as an arg
"""#675: a nested entry in a frontmatter list field reads as its YAML flow text,
so list fields stay `list[str]` and `update_item(tags=item.tags + [...])` keeps
every line; a mapping-valued whole field stays as it is (#262, #653)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_596_frontmatter_keys_layout import (  # noqa: F401  (fixture)
    ITEM_ID,
    _service,
    _write,
    repo,
)

BASE = f"id: {ITEM_ID}\ntitle: T\ntype: feature\nstatus: backlog\n"


@pytest.mark.parametrize("name", ["tags", "depends_on", "related", "superseded_by"])
def test_nested_entries_read_as_flow_text(repo: Path, name: str) -> None:
    _write(repo, BASE + f"{name}:\n  - a\n  - [b, c]\n  - {{k: v}}\n")
    value = getattr(_service(repo).get_item(ITEM_ID), name)
    assert value == ["a", "[b, c]", "{k: v}"]
    assert all(type(v) is str for v in value)


def test_update_keeps_nested_lines(repo: Path) -> None:
    path = _write(repo, BASE + "tags:\n  - a # s\n  - [b, c] # pair\n  - {k: v} # map\n")
    svc = _service(repo)
    item = svc.get_item(ITEM_ID)
    svc.update_item(ITEM_ID, tags=item.tags + ["x"])
    text = path.read_text()
    for line in ("  - a # s\n", "  - [b, c] # pair\n", "  - {k: v} # map\n", "  - x\n"):
        assert line in text, text
    assert _service(repo).get_item(ITEM_ID).tags == ["a", "[b, c]", "{k: v}", "x"]


def test_mapping_valued_field_stays_a_mapping(repo: Path) -> None:
    _write(repo, BASE + "depends_on:\n  a: [x]\n")
    assert _service(repo).get_item(ITEM_ID).depends_on == {"a": ["x"]}


@pytest.mark.parametrize(
    "block",
    [
        "tags:\n  -\n    - a\n    - b\n  - plain\n",  # a bare dash opening a nested list
        "tags:\n  - plain\n  -\n    - a\n    - b\n",
        "tags:\n  - k: v\n    j: w\n  - plain\n",  # a block mapping entry
    ],
    ids=["bare-dash-first", "bare-dash-last", "block-mapping"],
)
def test_update_after_block_nested_entry_keeps_the_item(repo: Path, block: str) -> None:
    """A nested entry written in block style spans several lines: the list is
    written fresh, as valid YAML, and the item stays readable (#675 review)."""
    path = _write(repo, BASE + block)
    svc = _service(repo)
    item = svc.get_item(ITEM_ID)
    svc.update_item(ITEM_ID, tags=item.tags + ["x"])
    reread = _service(repo).get_item(ITEM_ID)
    assert reread is not None, path.read_text()
    assert reread.tags == item.tags + ["x"], path.read_text()
