"""Issue #1124: the fast-forward-refused note strips git's labels once.

`_git_refusal` already drops each kept line's leading `error:`/`fatal:` label into
`_ff_why` (#1062), but the note sites (`pull_note_text`, `_won`) ran
`_without_git_prefix` over it again, so label-shaped text left after the first
strip went too: git's `"fatal: error: x"` read `(fast-forward refused: x)`, and
an untracked `error: odd.md` blocking the fast-forward read `odd.md`.
"""

from __future__ import annotations

import pytest

from yurtle_kanban.service import KanbanService, _git_refusal, pull_note_text
from yurtle_kanban.sync import Change

CASES = [
    ("fatal: error: x\n", "error: x"),
    ("error:\n\terror: odd.md\n", "error: odd.md"),
]


def _won_message(out: str) -> str:
    """`_won`'s message when the fast-forward is refused with git output `out`,
    `_ff_why` set the way `_fast_forward_to` sets it."""
    svc = KanbanService.__new__(KanbanService)

    def refused(branch: str, sha: str, *, warn: bool = True) -> bool:
        svc._ff_why = _git_refusal(out)[1]
        return False

    svc._fast_forward_to = refused  # type: ignore[method-assign]
    return svc._won("main", "0" * 40, Change(files={}, message="Moved X"), 1).message


@pytest.mark.parametrize(("out", "named"), CASES)
def test_reason_keeps_label_shaped_text(out: str, named: str) -> None:
    assert named in (_git_refusal(out)[1] or ""), _git_refusal(out)


@pytest.mark.parametrize(("out", "named"), CASES)
def test_pull_note_strips_once(out: str, named: str) -> None:
    note = pull_note_text("main", why=_git_refusal(out)[1])
    assert f"(fast-forward refused: {named})" in note, note


@pytest.mark.parametrize(("out", "named"), CASES)
def test_won_message_strips_once(out: str, named: str) -> None:
    message = _won_message(out)
    assert f"(fast-forward refused: {named})" in message, message
