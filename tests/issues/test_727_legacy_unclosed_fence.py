# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#727: a file whose body ALREADY has an unclosed fence (hand-edited, or written
before #720): a body edit is refused rather than deleting the status history;
other edits still work, and `validate` reports the file."""

from __future__ import annotations

from tests.issues.test_576_cli_update_deps import Repo, invoke, repo  # noqa: F401

HISTORY = (
    "\n```yurtle\n@prefix kb: <https://yurtle.dev/kanban/> .\n"
    "<> kb:statusChange [ kb:status kb:ready ; kb:by \"t\" ] .\n```\n"
)


def _legacy(repo: Repo) -> None:
    """A real status history (via `move`), then a hand-edited unclosed fence in the
    body just above it."""
    result = invoke(["move", "EXP-2", "ready", "--force", "--skip-gates"])
    assert result.exit_code == 0, result.output
    path = repo.path("EXP-2")
    text = path.read_text()
    at = text.index("```yurtle\n@prefix kb:")
    path.write_text(text[:at] + "Intro\n```python\nunclosed\n\n" + text[at:])
    repo.commit("legacy unclosed fence")


def test_body_edit_on_legacy_unclosed_fence_is_refused(repo: Repo) -> None:
    _legacy(repo)
    before = repo.snapshot()
    result = invoke(["update", "EXP-2", "--body", "repaired"])
    assert result.exit_code == 1, result.output
    assert "fence" in result.output.lower() and "history" in result.output.lower(), result.output
    assert repo.snapshot() == before


def test_title_edit_on_legacy_file_still_works(repo: Repo) -> None:
    _legacy(repo)
    result = invoke(["update", "EXP-2", "--title", "Renamed"])
    assert result.exit_code == 0, result.output
    assert "kb:statusChange" in repo.path("EXP-2").read_text()


def test_validate_reports_the_unclosed_fence(repo: Repo) -> None:
    _legacy(repo)
    result = invoke(["validate"])
    assert "EXP-2" in result.output and "fence" in result.output.lower(), result.output


def test_body_edit_that_would_swallow_comments_is_refused(repo: Repo) -> None:
    """#727 review: an item with comments but no history yet (never moved) — an
    unclosed fence above `## Comments` must not let a body edit delete them."""
    said = invoke(["comment", "EXP-3", "--body", "precious comment", "--agent", "a"])
    assert said.exit_code == 0, said.output
    path = repo.path("EXP-3")
    text = path.read_text()
    at = text.index("## Comments")
    path.write_text(text[:at] + "```python\nunclosed\n\n" + text[at:])
    repo.commit("legacy unclosed fence above comments")
    before = repo.snapshot()
    result = invoke(["update", "EXP-3", "--body", "repaired"])
    assert result.exit_code == 1, result.output
    assert repo.snapshot() == before
    reported = invoke(["validate"])
    assert "EXP-3" in reported.output and "fence" in reported.output.lower(), reported.output
