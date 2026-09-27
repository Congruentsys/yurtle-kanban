# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#769: `validate` calls a body fence that swallows the history or comments a
SWALLOWING fence, not an unclosed one.

Refusal 2 (#736) is a CLOSED 4-tick fence quoting the full history opener, so
"unclosed" is false for it. Decided ([steer] on #769; no users, no back-compat):

- `validate` text: the label is `SWALLOWING FENCE:` (was `UNCLOSED FENCE:`);
- `validate --json`: the issue `type` is `swallowing_fence` (was `unclosed_fence`);
- the same for a real unclosed fence and for refusal 2;
- neither output says "UNCLOSED FENCE" or "unclosed_fence" any more;
- `line`, `swallows` and `message` are unchanged.
"""

from __future__ import annotations

import json

import pytest

from tests.issues.test_576_cli_update_deps import Repo, _flat, invoke, repo  # noqa: F401
from tests.issues.test_743_swallowed_what import _comments_only, _history_only, _ok
from tests.issues.test_758_fence_wording import _expected, _refusal_2

LABEL = "SWALLOWING FENCE"
TYPE = "swallowing_fence"
OLD_LABEL = "UNCLOSED FENCE"
OLD_TYPE = "unclosed_fence"

CASES = {
    # (a) a real unclosed 3-tick fence above the status history
    "unclosed-above-history": (_history_only, "the status history"),
    # (b) refusal 2: a CLOSED 4-tick fence quoting the history opener
    "closed-quoting-opener": (_refusal_2, "the status history"),
    # (c) an unclosed fence above real comments, no history
    "comments-only": (_comments_only, "the comments"),
}


@pytest.mark.parametrize("case", CASES)
def test_fixture_is_reported_today(repo: Repo, case: str) -> None:
    """Guard on the fixtures: each one IS reported by the service today."""
    build, what = CASES[case]
    item_id, line = build(repo)
    content = repo.path(item_id).read_text()
    assert repo.service().swallowed_fence_line(content) == line
    assert repo.service().swallowed_what(content, line) == what


@pytest.mark.parametrize("case", CASES)
def test_validate_json_type_is_swallowing_fence(repo: Repo, case: str) -> None:
    build, what = CASES[case]
    item_id, line = build(repo)
    result = invoke(["validate", "--json"])
    issues = json.loads(result.output)["issues"]
    fences = [i for i in issues if i["type"] == TYPE]
    assert [i["id"] for i in fences] == [item_id], issues
    issue = fences[0]
    # line, swallows and message are unchanged
    assert issue["line"] == line and issue["swallows"] == what, issue
    assert _expected(item_id, line, what) in _flat(issue["message"]), issue
    assert not [i for i in issues if i["type"] == OLD_TYPE], issues
    assert OLD_TYPE not in result.output, result.output


@pytest.mark.parametrize("case", CASES)
def test_validate_text_label_is_swallowing_fence(repo: Repo, case: str) -> None:
    build, what = CASES[case]
    item_id, line = build(repo)
    out = _flat(invoke(["validate"]).output)
    assert OLD_LABEL not in out, out
    assert LABEL in out, out
    report = out[out.index(LABEL) :]
    assert f"{LABEL}: {_expected(item_id, line, what)}" in report, report


def test_clean_item_has_no_fence_report(repo: Repo) -> None:
    """Control: history, comments and a closed body fence: no report under either name."""
    _ok(["move", "EXP-2", "ready", "--force", "--skip-gates"])
    _ok(["comment", "EXP-2", "--body", "a comment", "--agent", "a"])
    _ok(["update", "EXP-2", "--body", "Intro\n```python\nx = 1\n```\n"])
    result = invoke(["validate", "--json"])
    issues = json.loads(result.output)["issues"]
    assert not [i for i in issues if i["type"] in (TYPE, OLD_TYPE)], issues
    out = _flat(invoke(["validate"]).output)
    assert LABEL not in out and OLD_LABEL not in out, out
