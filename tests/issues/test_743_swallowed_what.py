# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#743: the unclosed-fence message names what the fence swallows.

`validate` (text and `--json`) and the body-edit refusal (`_replace_body`, reached
through `update --body`) name what follows the fence line:

- "the status history and comments": the canonical history opener AND a real
  `## Comments` line come after it;
- "the status history": only the history does;
- "the comments": only a `## Comments` line does, including refusal 1 (a body
  ending in an unclosed fence that quotes `## Comments`, no real comments).

Both messages keep the fence line number. The `swallowed_fence_line` docstring says
"normally only an unclosed fence" (refusal 2 is a CLOSED fence quoting the opener).
"""

from __future__ import annotations

import json

import pytest

from tests.issues.test_576_cli_update_deps import Repo, _flat, invoke, repo  # noqa: F401
from yurtle_kanban.service import KanbanService

FENCE = "```python\nunclosed\n\n"
BOTH = "the status history and comments"
HISTORY = "the status history"
COMMENTS = "the comments"


def _ok(args: list[str]) -> None:
    result = invoke(args)
    assert result.exit_code == 0, result.output


def _insert_fence(repo: Repo, item_id: str, before: str) -> int:
    """Hand-insert an unclosed fence just above the first `before` in the item file;
    return the fence's 1-based file line."""
    path = repo.path(item_id)
    text = path.read_text()
    at = text.index(before)
    path.write_text(text[:at] + FENCE + text[at:])
    repo.commit(f"hand-made unclosed fence in {item_id}")
    return text[:at].count("\n") + 1


def _history_only(repo: Repo) -> tuple[str, int]:
    _ok(["move", "EXP-2", "ready", "--force", "--skip-gates"])
    return "EXP-2", _insert_fence(repo, "EXP-2", "```yurtle\n@prefix kb:")


def _comments_only(repo: Repo) -> tuple[str, int]:
    _ok(["comment", "EXP-3", "--body", "precious comment", "--agent", "a"])
    return "EXP-3", _insert_fence(repo, "EXP-3", "## Comments")


def _both(repo: Repo) -> tuple[str, int]:
    _ok(["move", "EXP-2", "ready", "--force", "--skip-gates"])
    _ok(["comment", "EXP-2", "--body", "precious comment", "--agent", "a"])
    text = repo.path("EXP-2").read_text()
    assert text.index("```yurtle\n@prefix kb:") < text.index("## Comments"), text
    return "EXP-2", _insert_fence(repo, "EXP-2", "```yurtle\n@prefix kb:")


def _quoted_comments(repo: Repo) -> tuple[str, int]:
    """Refusal 1: the body ends in an unclosed fence that quotes `## Comments`; no
    real comments, no history."""
    path = repo.path("EXP-4")
    text = path.read_text()
    assert "## Comments" not in text and "kb:statusChange" not in text, text
    path.write_text(text + "\n```text\n## Comments\n")
    repo.commit("unclosed fence quoting ## Comments")
    return "EXP-4", text.count("\n") + 2


CASES = {
    "history-only": (_history_only, HISTORY, ("comment",)),
    "comments-only": (_comments_only, COMMENTS, ("history",)),
    "both": (_both, BOTH, ()),
    "quoted-comments": (_quoted_comments, COMMENTS, ("history",)),
}


def _check(message: str, want: str, absent: tuple[str, ...], line: int) -> None:
    flat = " ".join(message.split())
    assert want in flat, f"{want!r} not in: {flat}"
    for word in absent:
        assert word not in flat.lower(), f"{word!r} should not be in: {flat}"
    assert f"line {line}" in flat, f"'line {line}' not in: {flat}"


@pytest.mark.parametrize("case", CASES)
def test_validate_json_message_names_what_is_swallowed(repo: Repo, case: str) -> None:
    build, want, absent = CASES[case]
    item_id, line = build(repo)
    result = invoke(["validate", "--json"])
    issues = [i for i in json.loads(result.output)["issues"] if i["type"] == "swallowing_fence"]
    assert [i["id"] for i in issues] == [item_id], issues
    assert issues[0]["line"] == line, issues
    _check(issues[0]["message"], want, absent, line)


@pytest.mark.parametrize("case", CASES)
def test_validate_text_names_what_is_swallowed(repo: Repo, case: str) -> None:
    build, want, absent = CASES[case]
    item_id, line = build(repo)
    result = invoke(["validate"])
    out = _flat(result.output)
    assert "SWALLOWING FENCE" in out and item_id in out, out
    # only the SWALLOWING FENCE report, not the rest of validate's output
    report = out[out.index("SWALLOWING FENCE") :]
    _check(report, want, absent, line)


@pytest.mark.parametrize("case", CASES)
def test_body_edit_refusal_names_what_is_swallowed(repo: Repo, case: str) -> None:
    build, want, absent = CASES[case]
    item_id, line = build(repo)
    before = repo.snapshot()
    result = invoke(["update", item_id, "--body", "repaired"])
    assert result.exit_code == 1, result.output
    assert repo.snapshot() == before
    _check(result.output, want, absent, line)


@pytest.mark.parametrize("case", CASES)
def test_service_replace_body_names_what_is_swallowed(repo: Repo, case: str) -> None:
    build, want, absent = CASES[case]
    item_id, line = build(repo)
    content = repo.path(item_id).read_text()
    with pytest.raises(ValueError) as refused:
        repo.service()._replace_body(content, "repaired")
    _check(str(refused.value), want, absent, line)


def test_clean_item_has_no_swallowing_fence_issue(repo: Repo) -> None:
    """Control: history and comments with every fence closed are not reported."""
    _ok(["move", "EXP-2", "ready", "--force", "--skip-gates"])
    _ok(["comment", "EXP-2", "--body", "a comment", "--agent", "a"])
    result = invoke(["validate", "--json"])
    issues = json.loads(result.output)["issues"]
    assert not [i for i in issues if i["type"] == "swallowing_fence"], issues
    _ok(["update", "EXP-2", "--body", "repaired"])


def test_docstring_says_normally_only_an_unclosed_fence() -> None:
    doc = " ".join((KanbanService.swallowed_fence_line.__doc__ or "").split())
    assert "normally only an unclosed fence" in doc, doc
