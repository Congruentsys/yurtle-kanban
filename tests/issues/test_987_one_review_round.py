# ruff: noqa: F811  (the borrowed `yk` fixture)
"""Issue #987: pairit's ONE review round (rachael-lab 819d26e, Captain 2026-09-28).

After a `changes` verdict at R, the driver fixes every finding and posts a comment
whose first two lines are `fixes-at-sha: <FIX-SHA>` / `for-review-at: <R>`. A fixed
tip merges without a second review:

- `safe_merge.sh` merges on an approve at the head, OR on a member's verdict at R plus
  a later member fixes comment at the head for R, where R is an ancestor of the head.
  Every other gate (checks, head, conflicts, worktree) is unchanged.
- `yk_next.py` counts a head with such a fixes comment as reviewed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_pairit_safe_merge import GREEN, Sandbox, _git, _output, verdict
from tests.test_yk_next_picker import PEER, pr, run_main, yk  # noqa: F401 (fixture)
from tests.test_yk_next_picker import verdict as picker_verdict


def fixes(head: str, reviewed: str, association: str = "MEMBER") -> dict[str, str]:
    return {
        "body": f"fixes-at-sha: {head}\nfor-review-at: {reviewed}\n\nF1 → {head[:7]}",
        "association": association,
    }


def _reviewed_ancestor(sb: Sandbox) -> str:
    return _git(sb.worktree, "rev-parse", "HEAD~1")


# --------------------------------------------------------------------------- safe_merge


def test_changes_then_fixes_at_head_merges(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    out = sb.run(GREEN, [verdict(r, "changes"), fixes(sb.head_sha, r)])
    assert out.returncode == 0, _output(out)
    assert len(sb.merge_calls()) == 1, _output(out)


def test_fixes_for_a_non_ancestor_review_refuses(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    stranger = _git(sb.checkout, "rev-parse", "origin/main")  # main moved on: not an ancestor
    out = sb.run(GREEN, [verdict(stranger, "changes"), fixes(sb.head_sha, stranger)])
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


def test_fixes_at_an_older_sha_refuses(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    out = sb.run(GREEN, [verdict(r, "changes"), fixes(r, r)])
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


def test_fixes_without_a_verdict_at_the_reviewed_sha_refuses(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    out = sb.run(GREEN, [fixes(sb.head_sha, r)])
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


@pytest.mark.parametrize("who", ["verdict", "fixes"])
def test_non_member_verdict_or_fixes_refuses(tmp_path: Path, who: str) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    comments = [
        verdict(r, "changes", "NONE" if who == "verdict" else "MEMBER"),
        fixes(sb.head_sha, r, "NONE" if who == "fixes" else "MEMBER"),
    ]
    out = sb.run(GREEN, comments)
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


def test_a_later_changes_at_head_overrides_the_fixes(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    out = sb.run(GREEN, [verdict(r, "changes"), fixes(sb.head_sha, r),
                         verdict(sb.head_sha, "changes")])
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


def test_fixed_tip_still_needs_green_checks(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    red = [dict(c, state="FAILURE") if c["name"] == "test (3.11)" else c for c in GREEN]
    out = sb.run(red, [verdict(r, "changes"), fixes(sb.head_sha, r)])
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


def test_control_approve_at_head_still_merges(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    out = sb.run(GREEN)
    assert out.returncode == 0, _output(out)


# --------------------------------------------------------------------------- yk_next

HEAD, R = "b" * 40, "c" * 40
FIXED = [picker_verdict(R, "changes"), f"fixes-at-sha: {HEAD}\nfor-review-at: {R}\n\nF1"]


def test_picker_fixed_head_is_ready_to_merge(yk) -> None:
    assert yk.my_pr_state(pr(1, head=HEAD, comments=FIXED)) == "ready-to-merge"


def test_picker_fixed_head_waits_for_ci(yk) -> None:
    p = pr(1, head=HEAD, comments=FIXED, checks=[{"status": "IN_PROGRESS", "conclusion": ""}])
    assert yk.my_pr_state(p) == "wait-ci"


def test_picker_fixes_without_a_verdict_at_r_still_needs_review(yk) -> None:
    p = pr(1, head=HEAD, comments=[FIXED[1]])
    assert yk.my_pr_state(p) == "needs-review"


def test_picker_does_not_re_review_another_authors_fixed_pr(yk, monkeypatch, capsys) -> None:
    out = run_main(yk, monkeypatch, capsys, [pr(7, author=PEER, head=HEAD, comments=FIXED)], [])
    assert "REVIEW PR #7" not in out, out


# --------------------------------------------------------------------------- r1 findings


def test_changes_at_head_is_never_fixed_by_a_comment_alone(tmp_path: Path) -> None:
    """r1 F1: `changes` at H plus `fixes-at-sha: H / for-review-at: H`, no fix commit."""
    sb = Sandbox(tmp_path, conflict=False)
    out = sb.run(GREEN, [verdict(sb.head_sha, "changes"), fixes(sb.head_sha, sb.head_sha)])
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


def test_a_short_for_review_at_refuses(tmp_path: Path) -> None:
    """r1 follow-up: the reviewed sha is the full sha the verdict names, never a prefix."""
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    out = sb.run(GREEN, [verdict(r[:7] + "0" * 33, "changes"), fixes(sb.head_sha, r[:7])])
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


def test_fixes_posted_before_the_verdict_refuses(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    out = sb.run(GREEN, [fixes(sb.head_sha, r), verdict(r, "changes")])
    assert out.returncode != 0, _output(out)
    assert sb.merge_calls() == []


def test_crlf_bodies_merge(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    r = _reviewed_ancestor(sb)
    comments = [
        {"body": f"reviewed-at-sha: {r}\r\nverdict: changes\r\n", "association": "MEMBER"},
        {"body": f"fixes-at-sha: {sb.head_sha}\r\nfor-review-at: {r}\r\n",
         "association": "MEMBER"},
    ]
    out = sb.run(GREEN, comments)
    assert out.returncode == 0, _output(out)


def test_picker_changes_at_head_is_not_fixed_by_a_comment(yk) -> None:
    comments = [picker_verdict(HEAD, "changes"), f"fixes-at-sha: {HEAD}\nfor-review-at: {HEAD}"]
    assert yk.my_pr_state(pr(1, head=HEAD, comments=comments)) == "changes-requested"


def test_picker_and_gate_agree_on_a_short_verdict_sha(yk) -> None:
    """A verdict posted with a short sha doesn't count as the reviewed R (safe_merge agrees)."""
    comments = [picker_verdict(R[:7], "changes"), f"fixes-at-sha: {HEAD}\nfor-review-at: {R}"]
    assert yk.my_pr_state(pr(1, head=HEAD, comments=comments)) == "needs-review"
