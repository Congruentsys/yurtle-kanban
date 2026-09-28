"""Issue #787 — the rendered fence refusal names the rendered file's line.

A templated create's unclosed-fence refusal read "The rendered content can't leave a
code fence open (the fence on line N is never closed)", where N counts lines of the
RENDERED item file (template frontmatter included), so a user looked at the wrong line.

Decided ([steer], bucket 1): the rendered-content refusal says "the fence on line N of
the rendered item file", and N stays the rendered file's line: the named line of the
rendered text really is the fence opener. The description refusal is unchanged: it
still says "(the fence on line N is never closed)", N being the description's own line,
and never says "rendered".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_583_update_field_level import _service
from tests.issues.test_644_comments_followups import (  # noqa: F401  (fixture)
    _clean,
    _files,
    _flat,
    board,
)
from tests.issues.test_666_input_refused import (  # noqa: F401  (fixtures)
    epic_board,
    hdd_board,
    raw_title_render,
)
from tests.issues.test_666_rendered_wording import OPEN_FENCE_BODY, _rendered
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused, WorkItemType

RENDERED_LINE = re.compile(r"the fence on line (\d+) of the rendered item file")
DESC_LINE = re.compile(r"\(the fence on line (\d+) is never closed\)")


def _assert_rendered_line(message: str, rendered: str | None) -> None:
    """The message names the rendered item file's line, and (when the rendered text
    is known) that line really is the unclosed fence's opener."""
    flat = _flat(message)
    match = RENDERED_LINE.search(flat)
    assert match, f"expected 'the fence on line N of the rendered item file'; got:\n{flat}"
    if rendered is not None:
        n = int(match.group(1))
        lines = rendered.splitlines()
        assert 1 <= n <= len(lines), (n, rendered)
        assert lines[n - 1].lstrip().startswith("```"), (n, lines[n - 1], rendered)
        # the opener in the fixture is the only fence, and it's ```python
        assert lines[n - 1].strip() == "```python", (n, lines[n - 1])


# ---------------------------------------------------------------------------
# service entry points, no remote
# ---------------------------------------------------------------------------


def test_create_item_content_names_rendered_file_line(board: Path) -> None:  # noqa: F811
    rendered = _rendered("FEAT-001", OPEN_FENCE_BODY)
    before = _files(board)
    with pytest.raises(InputRefused) as info:
        _service(board).create_item(WorkItemType.FEATURE, "Rendered", content=rendered)
    _assert_rendered_line(str(info.value), rendered)
    assert _files(board) == before
    _clean(board)


def test_create_item_and_push_content_names_rendered_file_line(board: Path) -> None:  # noqa: F811
    rendered = _rendered("FEAT-001", OPEN_FENCE_BODY)
    before = _files(board)
    with pytest.raises(InputRefused) as info:
        _service(board).create_item_and_push(
            WorkItemType.FEATURE, "Rendered", content=rendered
        )
    _assert_rendered_line(str(info.value), rendered)
    assert _files(board) == before
    _clean(board)


def test_create_item_and_push_render_names_rendered_file_line(board: Path) -> None:  # noqa: F811
    renders: list[str] = []

    def render(new_id: str) -> str:
        text = _rendered(new_id, OPEN_FENCE_BODY)
        renders.append(text)
        return text

    before = _files(board)
    with pytest.raises(InputRefused) as info:
        _service(board).create_item_and_push(WorkItemType.FEATURE, "Rendered", render=render)
    assert renders, "render was never called"
    _assert_rendered_line(str(info.value), renders[-1])
    assert _files(board) == before
    _clean(board)


# ---------------------------------------------------------------------------
# CLI: epic create (raw {{TITLE}} template copy), a fence reaching the body raw
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [pytest.param([], id="local"), pytest.param(["--push"], id="push")])
def test_cli_epic_create_names_rendered_file_line(
    epic_board: Path, extra: list[str]  # noqa: F811
) -> None:
    before = _files(epic_board)
    result = CliRunner().invoke(main, ["epic", "create", "Forge\n```", *extra])
    assert result.exit_code != 0, f"accepted:\n{result.output}"
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        repr(result.exception)
    )
    _assert_rendered_line(result.output, None)
    assert _files(epic_board) == before
    _clean(epic_board)


# ---------------------------------------------------------------------------
# control: the description refusal is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["create_item", "create_item_and_push"])
def test_description_fence_refusal_unchanged(board: Path, method: str) -> None:  # noqa: F811
    before = _files(board)
    with pytest.raises(InputRefused) as info:
        getattr(_service(board), method)(
            WorkItemType.FEATURE, "Described", description=OPEN_FENCE_BODY
        )
    message = str(info.value)
    match = DESC_LINE.search(message)
    assert match, message
    n = int(match.group(1))
    assert OPEN_FENCE_BODY.splitlines()[n - 1] == "```python", (n, message)
    assert "rendered" not in message.lower(), message
    assert _files(board) == before
    _clean(board)
