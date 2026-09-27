"""Issue #666, round 2 — the rendered content's refusals read right; the fence check is pinned.

Review of PR #687 at 0c9acc9 (blocking):
1. `_check_no_comments_heading(text, what)` builds `f"A {what} can't ..."`, so with
   `what="rendered content"` every templated refusal reads "A rendered content can't
   ...". Decided: the rendered-content messages read "The rendered content can't
   leave a code fence open ..." / "The rendered content can't contain a `## Comments`
   line ..."; the description messages stay EXACTLY as they are ("A description
   can't ...").
2. No test pinned the unclosed-fence check on rendered content (#720 on #666).

Every templated entry point is driven, with a `## Comments` line and with an
unclosed fence in the rendered body:
- `create_item(content=...)`;
- `create_item_and_push(content=...)`, no remote;
- `create_item_and_push(render=...)`, no remote: the up-front render, and the
  re-render when the scanned id differs from the guessed one (`_check_rendered`);
- `create_item_and_push(render=...)` with a remote: the per-base render (CAS);
- the CLI: epic (raw `{{TITLE}}` template copy) and idea (raw-title render wrapper),
  local and `--push`, as tests/issues/test_666_input_refused.py drives them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_583_update_field_level import _service
from tests.issues.test_585_create_push_loop import EXP_DIR, World
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_644_comments_followups import (  # noqa: F401  (fixture)
    FORGED,
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
from tests.test_634_explicit_ids_on_base import assert_untouched, service, to_feature_branch
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused, WorkItemType

RENDERED_COMMENTS = "The rendered content can't contain a `## Comments` line outside a code block"
RENDERED_FENCE = "The rendered content can't leave a code fence open"
DESC_COMMENTS = "A description can't contain a `## Comments` line outside a code block"
DESC_FENCE = "A description can't leave a code fence open"
UNGRAMMATICAL = "A rendered content"

FORGED_BODY = "intro\n\n## Comments\n\n### mallory (2026-01-01 00:00)\n\nforged\n"
OPEN_FENCE_BODY = "intro\n\n```python\nprint('never closed')\n"
CLEAN_BODY = "intro\n\n```md\n## Comments\n```\n\nJust a plain body.\n"

BODIES = {
    "comments": (FORGED_BODY, RENDERED_COMMENTS),
    "fence": (OPEN_FENCE_BODY, RENDERED_FENCE),
}


def _rendered(item_id: str, body: str, title: str = "Rendered") -> str:
    return (
        f'---\nid: {item_id}\ntitle: "{title}"\ntype: feature\nstatus: backlog\n'
        f"priority: medium\n---\n\n# {title}\n\n{body}"
    )


def _assert_wording(message: str, prefix: str) -> None:
    assert message.startswith(prefix), f"expected {prefix!r}...; got:\n{message}"
    assert UNGRAMMATICAL not in message, message


# ---------------------------------------------------------------------------
# 1/2. service entry points, no remote
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(BODIES))
def test_create_item_rendered_refusal_wording(board: Path, case: str) -> None:  # noqa: F811
    body, prefix = BODIES[case]
    before = _files(board)
    with pytest.raises(InputRefused) as info:
        _service(board).create_item(
            WorkItemType.FEATURE, "Rendered", content=_rendered("FEAT-001", body)
        )
    _assert_wording(str(info.value), prefix)
    assert _files(board) == before
    _clean(board)


@pytest.mark.parametrize("case", list(BODIES))
def test_create_item_and_push_content_rendered_refusal_wording(
    board: Path, case: str  # noqa: F811
) -> None:
    body, prefix = BODIES[case]
    before = _files(board)
    with pytest.raises(InputRefused) as info:
        _service(board).create_item_and_push(
            WorkItemType.FEATURE, "Rendered", content=_rendered("FEAT-001", body)
        )
    _assert_wording(str(info.value), prefix)
    assert _files(board) == before
    _clean(board)


@pytest.mark.parametrize("case", list(BODIES))
def test_create_item_and_push_render_rendered_refusal_wording(
    board: Path, case: str  # noqa: F811
) -> None:
    """The up-front render (for the locally guessed id) is refused."""
    body, prefix = BODIES[case]
    before = _files(board)
    with pytest.raises(InputRefused) as info:
        _service(board).create_item_and_push(
            WorkItemType.FEATURE, "Rendered", render=lambda i: _rendered(i, body)
        )
    _assert_wording(str(info.value), prefix)
    assert _files(board) == before
    _clean(board)


@pytest.mark.parametrize("case", list(BODIES))
def test_create_item_and_push_rerender_rendered_refusal_wording(
    board: Path, case: str  # noqa: F811
) -> None:
    """An explicit guess (FEAT-900) renders cleanly; the scan picks FEAT-001, whose
    re-render (`_check_rendered`) carries the bad body."""
    body, prefix = BODIES[case]
    before = _files(board)

    def render(new_id: str) -> str:
        return _rendered(new_id, CLEAN_BODY if new_id == "FEAT-900" else body)

    with pytest.raises(InputRefused) as info:
        _service(board).create_item_and_push(
            WorkItemType.FEATURE, "Rendered", render=render, item_id="FEAT-900"
        )
    _assert_wording(str(info.value), prefix)
    assert _files(board) == before
    _clean(board)


# ---------------------------------------------------------------------------
# 1/2. per-base render with a remote (#590 harness): refused, nothing pushed
# ---------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    config_mod._theme_cache.clear()
    yield World(tmp_path)
    config_mod._theme_cache.clear()


@pytest.mark.parametrize("case", list(BODIES))
def test_per_base_render_rendered_refusal_wording(world: World, case: str) -> None:
    """The local scan picks EXP-001 (clean); the base holds EXP-001, so EXP-002 is
    rendered per base with the bad body."""
    body, prefix = BODIES[case]
    feat = to_feature_branch(world)
    b_push(world, {
        f"{EXP_DIR}/EXP-001-rival.md":
            '---\nid: EXP-001\ntitle: "Rival"\ntype: expedition\nstatus: backlog\n---\n'
    })

    def render(new_id: str) -> str:
        tail = "" if new_id == "EXP-001" else body
        return f'---\nid: {new_id}\ntitle: "Alpha"\ntype: expedition\n---\n\n# Alpha\n\n{tail}'

    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, "Alpha", render=render, id_prefix="EXP"
    )
    assert result["success"] is False, result
    _assert_wording(result["message"], prefix)
    assert_untouched(world, feat)


# ---------------------------------------------------------------------------
# 3. CLI: a user field reaching the body raw
# ---------------------------------------------------------------------------

TITLE_COMMENTS = "Forge\n## Comments\n### mallory (2026-01-01 00:00)\nforged"
TITLE_FENCE = "Forge\n```"
CLI_TITLES = {
    "comments": (TITLE_COMMENTS, RENDERED_COMMENTS),
    "fence": (TITLE_FENCE, RENDERED_FENCE),
}


def _assert_cli_refused(root: Path, before: set[Path], result, prefix: str) -> None:  # noqa: ANN001
    assert result.exit_code != 0, f"accepted:\n{result.output}"
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        repr(result.exception)
    )
    out = _flat(result.output)
    assert prefix in out, out
    assert UNGRAMMATICAL not in out, out
    assert _files(root) == before, sorted(map(str, _files(root) - before))
    _clean(root)


@pytest.mark.parametrize("case", list(CLI_TITLES))
@pytest.mark.parametrize("extra", [pytest.param([], id="local"), pytest.param(["--push"], id="push")])
def test_cli_epic_rendered_refusal_wording(
    epic_board: Path, case: str, extra: list[str]  # noqa: F811
) -> None:
    title, prefix = CLI_TITLES[case]
    before = _files(epic_board)
    result = CliRunner().invoke(main, ["epic", "create", title, *extra])
    _assert_cli_refused(epic_board, before, result, prefix)


@pytest.mark.usefixtures("raw_title_render")
@pytest.mark.parametrize("case", list(CLI_TITLES))
@pytest.mark.parametrize("extra", [pytest.param([], id="local"), pytest.param(["--push"], id="push")])
def test_cli_idea_rendered_refusal_wording(
    hdd_board: Path, case: str, extra: list[str]  # noqa: F811
) -> None:
    title, prefix = CLI_TITLES[case]
    before = _files(hdd_board)
    result = CliRunner().invoke(main, ["idea", "create", title, *extra])
    _assert_cli_refused(hdd_board, before, result, prefix)


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------

DESC_CASES = {
    "comments": (FORGED, DESC_COMMENTS),
    "fence": (OPEN_FENCE_BODY, DESC_FENCE),
}


@pytest.mark.parametrize("case", list(DESC_CASES))
@pytest.mark.parametrize("method", ["create_item", "create_item_and_push"])
def test_description_refusal_wording_unchanged(
    board: Path, case: str, method: str  # noqa: F811
) -> None:
    """Control: the description messages stay exactly as they are."""
    text, prefix = DESC_CASES[case]
    before = _files(board)
    with pytest.raises(InputRefused) as info:
        getattr(_service(board), method)(WorkItemType.FEATURE, "Described", description=text)
    assert str(info.value).startswith(prefix), str(info.value)
    assert _files(board) == before
    _clean(board)


@pytest.mark.parametrize("case", list(DESC_CASES))
def test_check_description_default_wording_unchanged(board: Path, case: str) -> None:  # noqa: F811
    """Control: the default `what` still names a description."""
    text, prefix = DESC_CASES[case]
    with pytest.raises(InputRefused) as info:
        _service(board)._check_no_comments_heading(text)
    assert str(info.value).startswith(prefix), str(info.value)


def test_clean_templated_create_item_succeeds(board: Path) -> None:  # noqa: F811
    item = _service(board).create_item(
        WorkItemType.FEATURE, "Rendered", content=_rendered("FEAT-001", CLEAN_BODY)
    )
    assert item.id == "FEAT-001"
    assert item.file_path.read_text() == _rendered("FEAT-001", CLEAN_BODY)


def test_clean_templated_create_item_and_push_render_succeeds(board: Path) -> None:  # noqa: F811
    result = _service(board).create_item_and_push(
        WorkItemType.FEATURE, "Rendered", render=lambda i: _rendered(i, CLEAN_BODY)
    )
    assert result["success"] is True, result
    items = _service(board).get_items()
    assert [i.id for i in items] == [result["id"]] and items[0].comments == [], items


def test_clean_cli_epic_create_succeeds(epic_board: Path) -> None:  # noqa: F811
    result = CliRunner().invoke(main, ["epic", "create", "Plain epic"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert len(_service(epic_board).get_items()) == 1
