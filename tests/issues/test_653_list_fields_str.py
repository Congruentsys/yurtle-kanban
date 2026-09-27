# ruff: noqa: F811  -- the `repo` fixture imported from #596's module is re-bound as an arg
"""Issue #653 — list fields read back as non-strings break `update_item`.

`tags:` block items `- 2026` / `- yes` are read by YAML as `2026` / `True`, so
`item.tags` held an int and a bool and `update_item(tags=item.tags + ["x"])` raised
`TypeError` in `yaml_flow_list`.

Decided ([steer] on #653): the model's list fields are `list[str]`, so the
frontmatter reader reads each scalar entry as text by #225's `_scalar_text`
(`2026` -> "2026", `yes` -> "true", `1.5` -> "1.5") and drops `null`
entries (amended [steer]). This applies to every
list field `_parse_file` builds from the frontmatter: `tags`, `depends_on`,
`related` and `superseded_by` (`blocks` isn't read from the frontmatter; it lands
in `metadata`). The round trip then works, and #639's matcher keeps each kept
item's original line (`- yes # flag`): it matches the `_scalar_text` spelling too.

Fixtures are #596's; the edit helper follows #639's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_596_frontmatter_keys_layout import (  # noqa: F401  (fixture)
    ITEM_ID,
    _service,
    _write,
    repo,
)
from tests.issues.test_639_nonstring_list_comments import HEAD
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer

TAIL = "assignee: null\n"
LIST_FIELDS = ["tags", "depends_on", "related", "superseded_by"]
EXPECTED = ["2026", "true", "1.5", "a"]

BLOCK_NO_COMMENTS = "tags:\n  - 2026\n  - yes\n  - 1.5\n  - null\n  - a\n"


def _block(field: str) -> str:
    return f"{field}:\n  - 2026\n  - yes\n  - 1.5\n  - null\n  - a\n"


def _flow(field: str) -> str:
    return f"{field}: [2026, yes, 1.5, null, a]\n"


def _get(repo: Path, block: str) -> object:
    _write(repo, HEAD + block + TAIL)
    item = _service(repo).get_item(ITEM_ID)
    assert item is not None, "item didn't parse"
    return item


# ---------------------------------------------------------------------------
# (a) tags read back as strings, null dropped
# ---------------------------------------------------------------------------


def test_string_tags_unchanged(repo: Path) -> None:
    """Control: an all-string list reads back as it is."""
    item = _get(repo, "tags:\n  - a\n  - b\n")
    assert item.tags == ["a", "b"]  # type: ignore[attr-defined]


def test_block_tags_read_as_str(repo: Path) -> None:
    item = _get(repo, BLOCK_NO_COMMENTS)
    tags = item.tags  # type: ignore[attr-defined]
    assert tags == EXPECTED, tags
    assert all(type(t) is str for t in tags), [type(t).__name__ for t in tags]


# ---------------------------------------------------------------------------
# (b) the round trip works and keeps each kept item's line (#639)
# ---------------------------------------------------------------------------

COMMENTED = "tags:\n  # c\n  - a # s\n  - 2026 # year\n  - yes # flag\n  - 1.5 # v\n"


def test_update_item_round_trips_nonstring_tags(repo: Path) -> None:
    path = _write(repo, HEAD + COMMENTED + TAIL)
    old = path.read_text(encoding="utf-8")
    svc = _service(repo)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    svc.update_item(ITEM_ID, tags=item.tags + ["x"], commit=False)
    new = path.read_text(encoding="utf-8")
    assert new == old.replace(COMMENTED, COMMENTED + "  - x\n"), new
    again = _service(repo).get_item(ITEM_ID)
    assert again is not None and again.tags == ["a", "2026", "true", "1.5", "x"], (
        again and again.tags
    )


def test_update_item_round_trips_with_null_tag(repo: Path) -> None:
    """A `- null` entry isn't in `item.tags`; the kept lines survive, `- x` is added."""
    block = COMMENTED + "  - null # n\n"
    path = _write(repo, HEAD + block + TAIL)
    svc = _service(repo)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    svc.update_item(ITEM_ID, tags=item.tags + ["x"], commit=False)
    new = path.read_text(encoding="utf-8")
    for line in ("  - a # s\n", "  - 2026 # year\n", "  - yes # flag\n", "  - 1.5 # v\n",
                 "  - x\n"):
        assert line in new, f"missing {line!r}:\n{new}"
    assert '"2026"' not in new and '"true"' not in new, new


# ---------------------------------------------------------------------------
# (c) every list field the reader builds, block and flow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", [_block, _flow], ids=["block", "flow"])
@pytest.mark.parametrize("field", LIST_FIELDS)
def test_list_field_read_as_str(repo: Path, field: str, layout: object) -> None:
    item = _get(repo, layout(field))  # type: ignore[operator]
    values = getattr(item, field)
    assert values == EXPECTED, values
    assert all(type(v) is str for v in values), [type(v).__name__ for v in values]


@pytest.mark.parametrize("field", LIST_FIELDS)
def test_list_field_short_flow_read_as_str(repo: Path, field: str) -> None:
    item = _get(repo, f"{field}: [2026, yes]\n")
    assert getattr(item, field) == ["2026", "true"], getattr(item, field)


@pytest.mark.parametrize("field", LIST_FIELDS)
def test_list_field_string_values_control(repo: Path, field: str) -> None:
    """Control: string entries read back as they are."""
    item = _get(repo, f"{field}: [FEAT-002, FEAT-003]\n")
    assert getattr(item, field) == ["FEAT-002", "FEAT-003"]


# ---------------------------------------------------------------------------
# (d) MCP get_item and show --json
# ---------------------------------------------------------------------------


def _all_lists(style: str) -> str:
    make = _block if style == "block" else _flow
    return "".join(make(f) for f in LIST_FIELDS)


@pytest.mark.parametrize("style", ["block", "flow"])
def test_mcp_get_item_lists_are_str(repo: Path, style: str) -> None:
    _write(repo, HEAD + _all_lists(style) + TAIL)
    out = KanbanMCPServer(repo_root=repo).handle_tool_call(
        "kanban_get_item", {"item_id": ITEM_ID}
    )
    assert "item" in out, out
    for field in LIST_FIELDS:
        assert out["item"][field] == EXPECTED, (field, out["item"][field])


@pytest.mark.parametrize("style", ["block", "flow"])
def test_show_json_lists_are_str(repo: Path, style: str) -> None:
    _write(repo, HEAD + _all_lists(style) + TAIL)
    result = CliRunner().invoke(main, ["show", ITEM_ID, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    for field in LIST_FIELDS:
        assert data[field] == EXPECTED, (field, data[field])
