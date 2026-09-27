# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#758: the swallowing-fence report no longer says the fence "is never closed".

Refusal 2 (#736) is a CLOSED 4-tick fence that quotes the full canonical
status-history opener: `swallowed_fence_line` reports it, and "never closed" is
false there. Decided ([steer] on #758): wording true in both cases.

- `validate --json`: the `swallowing_fence` message reads
  "<ID>: the body's code fence on line N runs over <what>: close it, or reword a
  quoted heading inside it"; `type`, `line` and `swallows` are unchanged.
- `validate` text: the `SWALLOWING FENCE:` line carries the same wording.
- The body-edit refusal (`_replace_body`, via `update --body`) carries the hint
  "... reword a quoted heading inside it".

Item 1 of the issue (over-reporting a quoted `## Comments` / opener) is kept by
decision and not tested here.
"""

from __future__ import annotations

import json

import pytest

from tests.issues.test_576_cli_update_deps import Repo, _flat, invoke, repo  # noqa: F401
from tests.issues.test_743_swallowed_what import _comments_only, _history_only, _ok
from yurtle_kanban.service import KanbanService

HINT = "reword a quoted heading"

# The full canonical history opener, quoted inside a CLOSED 4-tick fence (#736)
OPENER = (
    "```yurtle\n@prefix kb: <https://yurtle.dev/kanban/> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n\n<> kb:statusChange"
)
QUOTED_HISTORY = (
    "\n````text\n" + OPENER + ' [ kb:status kb:ready ; kb:by "t" ] .\n```\n````\n'
)


def _refusal_2(repo: Repo) -> tuple[str, int]:
    """A closed 4-tick fence in EXP-4's body quoting the full history opener; no
    real history or comments. Return the 4-tick fence's 1-based file line."""
    assert KanbanService._HISTORY_OPEN_RE.search(QUOTED_HISTORY), QUOTED_HISTORY
    path = repo.path("EXP-4")
    text = path.read_text()
    assert "kb:statusChange" not in text and "## Comments" not in text, text
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + QUOTED_HISTORY)
    repo.commit("closed 4-tick fence quoting the history opener")
    return "EXP-4", text.count("\n") + 2


CASES = {
    # (a) a real unclosed 3-tick fence above the status history
    "unclosed-above-history": (_history_only, "the status history"),
    # (b) refusal 2: a closed fence quoting the opener
    "closed-quoting-opener": (_refusal_2, "the status history"),
    # (c) an unclosed fence above real comments, no history
    "comments-only": (_comments_only, "the comments"),
}


def _expected(item_id: str, line: int, what: str) -> str:
    return (
        f"{item_id}: the body's code fence on line {line} runs over {what}: "
        "close it, or reword a quoted heading inside it"
    )


def test_refusal_2_fixture_is_reported(repo: Repo) -> None:
    """Guard on the fixture: the closed 4-tick fence IS reported today (#736)."""
    item_id, line = _refusal_2(repo)
    content = repo.path(item_id).read_text()
    assert repo.service().swallowed_fence_line(content) == line
    assert repo.service().swallowed_what(content, line) == "the status history"


@pytest.mark.parametrize("case", CASES)
def test_validate_json_message_says_runs_over(repo: Repo, case: str) -> None:
    build, what = CASES[case]
    item_id, line = build(repo)
    result = invoke(["validate", "--json"])
    issues = [i for i in json.loads(result.output)["issues"] if i["type"] == "swallowing_fence"]
    assert [i["id"] for i in issues] == [item_id], issues
    issue = issues[0]
    assert issue["line"] == line and issue["swallows"] == what, issue
    message = _flat(issue["message"])
    assert "never closed" not in message, message
    assert _expected(item_id, line, what) in message, message


@pytest.mark.parametrize("case", CASES)
def test_validate_text_says_runs_over(repo: Repo, case: str) -> None:
    build, what = CASES[case]
    item_id, line = build(repo)
    out = _flat(invoke(["validate"]).output)
    assert "SWALLOWING FENCE" in out, out
    report = out[out.index("SWALLOWING FENCE") :]
    assert "never closed" not in report, report
    assert f"SWALLOWING FENCE: {_expected(item_id, line, what)}" in report, report


@pytest.mark.parametrize("case", CASES)
def test_body_edit_refusal_hints_reword(repo: Repo, case: str) -> None:
    build, what = CASES[case]
    item_id, line = build(repo)
    before = repo.snapshot()
    result = invoke(["update", item_id, "--body", "repaired"])
    assert result.exit_code == 1, result.output
    assert repo.snapshot() == before
    out = _flat(result.output)
    assert "never closed" not in out, out
    assert f"line {line}" in out and what in out, out
    assert HINT in out, out


@pytest.mark.parametrize("case", CASES)
def test_service_replace_body_hints_reword(repo: Repo, case: str) -> None:
    build, what = CASES[case]
    item_id, line = build(repo)
    content = repo.path(item_id).read_text()
    with pytest.raises(ValueError) as refused:
        repo.service()._replace_body(content, "repaired")
    message = _flat(str(refused.value))
    assert "never closed" not in message, message
    assert f"line {line}" in message and what in message, message
    assert HINT in message, message


def test_clean_item_has_no_fence_report(repo: Repo) -> None:
    """Control: history, comments and a closed body fence: no report, body edits work."""
    _ok(["move", "EXP-2", "ready", "--force", "--skip-gates"])
    _ok(["comment", "EXP-2", "--body", "a comment", "--agent", "a"])
    _ok(["update", "EXP-2", "--body", "Intro\n```python\nx = 1\n```\n"])
    result = invoke(["validate", "--json"])
    issues = json.loads(result.output)["issues"]
    assert not [i for i in issues if i["type"] == "swallowing_fence"], issues
    assert "SWALLOWING FENCE" not in _flat(invoke(["validate"]).output)
    _ok(["update", "EXP-2", "--body", "repaired"])
