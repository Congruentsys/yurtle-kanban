"""Issue #177 — pairit conventions that remove the causes of rebase conflicts.

Decided on #143: the test partner writes each issue's tests in a NEW file,
`tests/issues/test_<N>_<slug>.py`; test commits are named `test(#<N>): red` (never a
subject starting with `#`, which a conflicted rebase drops under git's comment char);
the Rebasing section is a plain `git -c core.commentChar=';' rebase origin/main`, with
no "keep both sides" advice, but keeps the post-approval range-diff re-verification.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAIRIT = REPO / ".claude/skills/pairit/SKILL.md"
YK_LOOP = REPO / ".claude/skills/yk-loop/SKILL.md"


def _pairit() -> str:
    return PAIRIT.read_text()


class TestPairitPerIssueTestFiles:
    """The partner brief sends tests to a new per-issue file (#177)."""

    def test_brief_names_per_issue_test_file(self) -> None:
        assert "tests/issues/test_<N>_" in _pairit(), (
            "pairit SKILL.md does not tell the partner to write tests/issues/test_<N>_<slug>.py"
        )

    def test_tests_issues_package_exists(self) -> None:  # control: already on main
        assert (REPO / "tests/issues/__init__.py").is_file()


class TestPairitTestCommitSubject:
    """Test commits are `test(#<N>): red`, never a subject starting with `#` (#177)."""

    def test_names_test_commit_subject(self) -> None:
        assert "test(#<N>): red" in _pairit(), (
            "pairit SKILL.md does not give the partner the subject `test(#<N>): red`"
        )

    def test_no_subject_starting_with_hash(self) -> None:
        text = _pairit()
        assert "#<N>: tests" not in text, "pairit still says to commit as `#<N>: tests (red)`"
        bad = re.findall(r"`#<N>:[^`]*`", text)
        assert not bad, f"pairit still names a commit subject starting with `#<N>:`: {bad}"


class TestPairitRebasing:
    """Rebasing: commentChar-safe plain rebase, no "keep both sides", range-diff kept (#177)."""

    def test_no_keep_both_sides(self) -> None:
        assert "keep both sides" not in _pairit().lower(), (
            "pairit SKILL.md still advises 'keep both sides' of a conflict"
        )

    def test_rebase_uses_comment_char(self) -> None:
        lines = [ln for ln in _pairit().splitlines()
                 if re.search(r"\bgit\b[^`\n]*\brebase\b[^`\n]*origin/main", ln)]
        assert lines, "pairit SKILL.md has no `git ... rebase origin/main` instruction"
        for ln in lines:
            assert "core.commentChar" in ln, (
                f"rebase instruction does not set core.commentChar: {ln.strip()!r}"
            )

    def test_range_diff_reverification_kept(self) -> None:  # control
        assert "git range-diff" in _pairit(), (
            "pairit SKILL.md lost the post-approval `git range-diff` re-verification"
        )


class TestYkLoopConventions:
    """yk-loop carries no old convention either (#177)."""

    def test_yk_loop_no_keep_both_sides(self) -> None:
        assert "keep both sides" not in YK_LOOP.read_text().lower()

    def test_yk_loop_no_old_test_subject(self) -> None:
        assert "#<N>: tests" not in YK_LOOP.read_text()
