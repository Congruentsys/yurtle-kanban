# ruff: noqa: F811  -- the `repo` fixture imported from #596's module is re-bound as an arg
"""Issue #619 — frontmatter edits drop YAML comments.

`_add_or_update_frontmatter_field` replaced a field's whole value, so `update_item`
lost a trailing comment on the key line (`tags:  # note`, `title: "Seed"  # why`)
and every comment line inside a rewritten block list.

Decided ([steer] on #619, G1: don't silently drop user text):
1. A trailing comment on the key line is kept, with its spacing, for block lists,
   flow lists and scalars.
2. A comment line inside a rewritten block list is kept, as written (same
   indentation), anchored to the item that follows it: it stays right before that
   item wherever the item lands.
3. A comment anchored to an item the edit removes is dropped (pinned).
4. Scalar fields keep their trailing comment through `update_item` and `rank_item`.

Fixtures are #596's (a software FEAT-001 written with exact frontmatter).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.issues.test_596_frontmatter_keys_layout import (  # noqa: F401  (fixture)
    FM_TAIL,
    ITEM_ID,
    _fm_lines,
    _service,
    _write,
    repo,
)

HEAD = 'id: FEAT-001\ntitle: "Seed"\ntype: feature\nstatus: backlog\npriority: high\n'


def _update_tags(repo: Path, block: str, tags: list[str]) -> tuple[str, str]:
    path = _write(repo, HEAD + block + FM_TAIL)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, tags=tags, commit=False)
    new = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load("\n".join(_fm_lines(new)))
    assert parsed["tags"] == tags, new
    item = _service(repo).get_item(ITEM_ID)
    assert item is not None and item.tags == tags
    return old, new


# ---------------------------------------------------------------------------
# 1. a trailing comment on the key line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        pytest.param(
            "tags:  # note\n  - a\n  - b\n",
            "tags:  # note\n  - a\n  - b\n  - x\n",
            id="block",
        ),
        pytest.param(
            "tags: # note\n- a\n- b\n",
            "tags: # note\n- a\n- b\n- x\n",
            id="block-column-0",
        ),
        pytest.param(
            "tags: [a, b]  # note\n",
            "tags: [a, b, x]  # note\n",
            id="flow",
        ),
        pytest.param(
            "tags: [a, b] # note\n",
            "tags: [a, b, x] # note\n",
            id="flow-one-space",
        ),
        pytest.param(
            "'tags': [a, b]  # note\n",
            "'tags': [a, b, x]  # note\n",
            id="flow-quoted-key",
        ),
    ],
)
def test_key_line_comment_kept(repo: Path, block: str, expected: str) -> None:
    old, new = _update_tags(repo, block, ["a", "b", "x"])
    assert new == old.replace(block, expected), f"key-line comment lost:\n{new}"


# ---------------------------------------------------------------------------
# 2. comment lines inside a block list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "tags", "expected"),
    [
        pytest.param(
            "tags:\n  - a\n  # c\n  - b\n",
            ["a", "b", "x"],
            "tags:\n  - a\n  # c\n  - b\n  - x\n",
            id="grow",
        ),
        pytest.param(
            "tags:\n- a\n# c\n- b\n",
            ["a", "b", "x"],
            "tags:\n- a\n# c\n- b\n- x\n",
            id="grow-column-0",
        ),
        pytest.param(
            "tags:\n  - a\n    # c, deeper\n  - b\n",
            ["a", "b", "x"],
            "tags:\n  - a\n    # c, deeper\n  - b\n  - x\n",
            id="grow-own-indent",
        ),
        pytest.param(
            "tags:\n  # first\n  - a\n  - b\n",
            ["x", "a", "b"],
            "tags:\n  - x\n  # first\n  - a\n  - b\n",
            id="insert-before-anchor",
        ),
        pytest.param(
            "tags:\n  - a\n  # c\n  - b\n",
            ["b", "a"],
            "tags:\n  # c\n  - b\n  - a\n",
            id="reorder-follows-anchor",
        ),
        pytest.param(
            "tags:  # note\n  - a\n  # c\n  - b\n",
            ["a", "b", "x"],
            "tags:  # note\n  - a\n  # c\n  - b\n  - x\n",
            id="with-key-line-comment",
        ),
    ],
)
def test_inner_comment_kept_before_its_item(
    repo: Path, block: str, tags: list[str], expected: str
) -> None:
    old, new = _update_tags(repo, block, tags)
    assert new == old.replace(block, expected), f"inner comment not kept:\n{new}"


def test_inner_comment_kept_crlf(repo: Path) -> None:
    block = "tags:\n  - a\n  # c\n  - b\n"
    path = _write(repo, HEAD + block + FM_TAIL)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    old = path.read_bytes()
    _service(repo).update_item(ITEM_ID, tags=["a", "b", "x"], commit=False)
    new = path.read_bytes()
    assert new == old.replace(
        b"tags:\r\n  - a\r\n  # c\r\n  - b\r\n",
        b"tags:\r\n  - a\r\n  # c\r\n  - b\r\n  - x\r\n",
    ), new


# ---------------------------------------------------------------------------
# 3. a comment anchored to a removed item is dropped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "tags", "expected"),
    [
        pytest.param(
            "tags:\n  - a\n  # c\n  - b\n",
            ["a"],
            "tags:\n  - a\n",
            id="anchor-removed",
        ),
        pytest.param(
            "tags:\n  - a\n  # c\n  - b\n",
            ["b"],
            "tags:\n  # c\n  - b\n",
            id="other-removed-anchor-kept",
        ),
        pytest.param(
            "tags:  # note\n  - a\n  # c\n  - b\n",
            ["z"],
            "tags:  # note\n  - z\n",
            id="all-replaced-key-comment-kept",
        ),
    ],
)
def test_comment_of_removed_item_dropped(
    repo: Path, block: str, tags: list[str], expected: str
) -> None:
    old, new = _update_tags(repo, block, tags)
    assert new == old.replace(block, expected), new


# ---------------------------------------------------------------------------
# 4. scalar fields: update_item and rank_item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "kwargs", "expected"),
    [
        pytest.param(
            'title: "Seed"  # working name\n',
            {"title": "Renamed"},
            'title: "Renamed"  # working name\n',
            id="title",
        ),
        pytest.param(
            "priority: high # P1 for now\n",
            {"priority": "low"},
            "priority: low # P1 for now\n",
            id="priority",
        ),
        pytest.param(
            "assignee: null  # nobody yet\n",
            {"assignee": "alice"},
            "assignee: alice  # nobody yet\n",
            id="assignee",
        ),
        pytest.param(
            "'priority': high  # quoted key\n",
            {"priority": "low"},
            "'priority': low  # quoted key\n",
            id="priority-quoted-key",
        ),
    ],
)
def test_scalar_comment_kept_through_update(
    repo: Path, line: str, kwargs: dict[str, object], expected: str
) -> None:
    key = line.split(":")[0].strip("'\"")
    fm = "".join(
        line if ln.startswith(f"{key}:") else ln + "\n" for ln in (HEAD + FM_TAIL).splitlines()
    )
    assert line in fm, fm
    path = _write(repo, fm)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, commit=False, **kwargs)  # type: ignore[arg-type]
    new = path.read_text(encoding="utf-8")
    if "title" in kwargs:
        old = old.replace("# Seed\n", f"# {kwargs['title']}\n")
    assert new == old.replace(line, expected), f"trailing comment lost:\n{new}"
    parsed = yaml.safe_load("\n".join(_fm_lines(new)))
    assert parsed[key] == next(iter(kwargs.values())), parsed


def test_scalar_comment_kept_through_rank(repo: Path) -> None:
    extra = 'priority_rank: 5  # was 3 last sprint\nvalue_summary: "old"  # from planning\n'
    path = _write(repo, HEAD + FM_TAIL + extra)
    old = path.read_text(encoding="utf-8")
    _service(repo).rank_item(ITEM_ID, 2, value_summary="new value", commit=False)
    new = path.read_text(encoding="utf-8")
    assert new == old.replace(
        extra,
        'priority_rank: 2  # was 3 last sprint\nvalue_summary: "new value"  # from planning\n',
    ), new


# ---------------------------------------------------------------------------
# controls: a `#` that isn't a comment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        pytest.param('title: "Fix C# parser #2"\n', id="hash-inside-quotes"),
        pytest.param("title: C#-parser\n", id="hash-without-space"),
    ],
)
def test_hash_inside_value_is_not_a_comment(repo: Path, line: str) -> None:
    fm = HEAD.replace('title: "Seed"\n', line) + FM_TAIL
    path = _write(repo, fm)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, title="Renamed", commit=False)
    new = path.read_text(encoding="utf-8")
    assert new == old.replace(line, 'title: "Renamed"\n').replace("# Seed\n", "# Renamed\n"), new
