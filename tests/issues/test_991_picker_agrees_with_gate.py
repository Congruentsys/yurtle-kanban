# ruff: noqa: F811  (the borrowed `yk` fixture)
"""Issue #991: the picker (`yk_next.py`) judges a PR's verdict by safe_merge.sh's
rule, so it never reports `ready-to-merge` for a PR the gate refuses (yk-loop
then re-picks it until its third-pick stop):

- the LATEST decisive comment (a verdict or a fixes comment) decides, even when it
  names another sha: a late verdict of an old sha leaves the head unreviewed;
- shas match exactly: full 40-hex, as GitHub gives them (no prefix, no case fold);
- only the repo's own people count (OWNER / MEMBER / COLLABORATOR).
"""

from __future__ import annotations

from tests.test_yk_next_picker import pr, yk  # noqa: F401 (fixture)

HEAD, R, R2 = "b" * 40, "c" * 40, "d" * 40


def v(sha: str, word: str) -> str:
    return f"reviewed-at-sha: {sha}\nverdict: {word}\n"


def fx(head: str, reviewed: str) -> str:
    return f"fixes-at-sha: {head}\nfor-review-at: {reviewed}\n"


def test_a_late_stale_verdict_after_fixes_unreviews_the_head(yk) -> None:
    p = pr(1, head=HEAD, comments=[v(R, "changes"), fx(HEAD, R), v(R2, "changes")])
    assert yk.my_pr_state(p) == "needs-review"


def test_a_late_stale_verdict_after_approve_unreviews_the_head(yk) -> None:
    p = pr(1, head=HEAD, comments=[v(HEAD, "approve"), v(R2, "approve")])
    assert yk.my_pr_state(p) == "needs-review"


def test_a_prefix_verdict_is_not_a_verdict_at_the_head(yk) -> None:
    assert yk.my_pr_state(pr(1, head=HEAD, comments=[v(HEAD[:12], "approve")])) == "needs-review"


def test_an_uppercase_sha_is_not_the_head(yk) -> None:
    assert yk.my_pr_state(pr(1, head=HEAD, comments=[v(HEAD.upper(), "approve")])) \
        == "needs-review"


def test_an_uppercase_fixes_comment_does_not_count(yk) -> None:
    p = pr(1, head=HEAD, comments=[v(R, "changes"), fx(HEAD.upper(), R.upper())])
    assert yk.my_pr_state(p) == "changes-requested" or yk.my_pr_state(p) == "needs-review"
    assert yk.my_pr_state(p) != "ready-to-merge"


def test_a_non_member_verdict_does_not_count(yk) -> None:
    p = pr(1, head=HEAD, comments=[v(HEAD, "approve")])
    p["comments"][0]["authorAssociation"] = "NONE"
    assert yk.my_pr_state(p) == "needs-review"


def test_control_member_approve_at_head_is_ready(yk) -> None:
    p = pr(1, head=HEAD, comments=[v(HEAD, "approve")])
    p["comments"][0]["authorAssociation"] = "MEMBER"
    assert yk.my_pr_state(p) == "ready-to-merge"


def test_control_fixed_tip_is_ready(yk) -> None:
    assert yk.my_pr_state(pr(1, head=HEAD, comments=[v(R, "changes"), fx(HEAD, R)])) \
        == "ready-to-merge"
