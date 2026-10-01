# ruff: noqa: F811  (the borrowed `yk` fixture)
"""Issue #1228: tighten #987 so blocking findings get re-reviewed (Captain 2026-10-01).

- `verdict: changes` (blocking findings) at R: the driver fixes them, then the reviewer
  posts a NEW `reviewed-at-sha: <fixed head>` / `verdict: approve` before merge. A
  `fixes-at-sha: <head>` / `for-review-at: <R>` comment after `changes` no longer makes
  the head mergeable.
- `verdict: approve` at R with non-blocking `(follow-up)` findings: the driver may fix
  them and post the fixes comment; that tip merges (R a proper ancestor of the head, as in #987).
- Which verdict at R counts: the latest UNEDITED `reviewed-at-sha: R` comment, and it
  must be `approve`. An edited comment is no verdict.

The gate (`safe_merge.sh`) and the picker (`yk_next.verdict_at_head` / `my_pr_state`)
judge every case alike (#991).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.test_pairit_safe_merge import GREEN, Sandbox, _git, _output
from tests.test_yk_next_picker import pr, yk  # noqa: F401 (fixture)


def _v(sha: str, word: str, note: str = "") -> dict:
    return {"body": f"reviewed-at-sha: {sha}\nverdict: {word}\n\n{note or 'Looks ' + word}."}


def _fx(head: str, reviewed: str) -> dict:
    return {"body": f"fixes-at-sha: {head}\nfor-review-at: {reviewed}\n\nF1 -> {head[:7]}"}


def _edited(c: dict) -> dict:
    return dict(c, edited=True)


FOLLOW_UP = "N1 (follow-up): rename the helper"

# name -> (comments(head, R), merges?)
CASES: dict[str, tuple[Callable[[str, str], list[dict]], bool]] = {
    # (a) blocking findings, fixed, no re-review: refused
    "a-changes-then-fixes": (lambda h, r: [_v(r, "changes"), _fx(h, r)], False),
    # (b) blocking findings, fixed, then a NEW approve at the fixed head: merges
    "b-changes-fixes-then-approve-at-head": (
        lambda h, r: [_v(r, "changes"), _fx(h, r), _v(h, "approve")], True),
    # (c) approve with a follow-up, fixed: the follow-up path still merges
    "c-approve-followup-then-fixes": (
        lambda h, r: [_v(r, "approve", FOLLOW_UP), _fx(h, r)], True),
    # (d) approve at R, then a later changes at R: the latest verdict at R is changes
    "d-approve-then-changes-at-r-then-fixes": (
        lambda h, r: [_v(r, "approve"), _v(r, "changes"), _fx(h, r)], False),
    # (e) changes at R, then a re-review approving the same R: the latest at R is approve
    "e-changes-then-approve-at-r-then-fixes": (
        lambda h, r: [_v(r, "changes"), _v(r, "approve"), _fx(h, r)], True),
    # (f) an edited approve at R is no verdict
    "f-edited-approve-then-fixes": (
        lambda h, r: [_edited(_v(r, "approve")), _fx(h, r)], False),
    # (f2) changes at R, then an EDITED approve at R: the latest UNEDITED one is changes
    "f2-changes-then-edited-approve-then-fixes": (
        lambda h, r: [_v(r, "changes"), _edited(_v(r, "approve")), _fx(h, r)], False),
}


def _picker_pr(head: str, comments: list[dict]) -> dict:
    p = pr(1, head=head, comments=[c["body"] for c in comments])
    for raw, c in zip(comments, p["comments"]):
        c["authorAssociation"] = "MEMBER"
        c["includesCreatedEdit"] = raw.get("edited", False)
    return p


def _setup(tmp_path: Path) -> tuple[Sandbox, str, str]:
    sb = Sandbox(tmp_path, conflict=False)
    return sb, sb.head_sha, _git(sb.worktree, "rev-parse", "HEAD~1")


def _gate(sb: Sandbox, comments: list[dict]):
    return sb.run(GREEN, [dict(c, association="MEMBER") for c in comments])


# --------------------------------------------------------------------------- safe_merge


@pytest.mark.parametrize("case", sorted(CASES))
def test_gate(tmp_path: Path, case: str) -> None:
    build, merges = CASES[case]
    sb, head, r = _setup(tmp_path)
    out = _gate(sb, build(head, r))
    if merges:
        assert out.returncode == 0, _output(out)
        assert len(sb.merge_calls()) == 1, _output(out)
    else:
        assert out.returncode != 0, _output(out)
        assert sb.merge_calls() == [], _output(out)


# --------------------------------------------------------------------------- yk_next


@pytest.mark.parametrize("case", sorted(CASES))
def test_picker_verdict_at_head(yk, case: str) -> None:
    build, merges = CASES[case]
    head, r = "b" * 40, "c" * 40
    found = yk.verdict_at_head(_picker_pr(head, build(head, r)))
    if merges:
        assert found in ("approve", "fixed"), found
    else:
        assert found is None, found


@pytest.mark.parametrize("case", sorted(CASES))
def test_picker_state(yk, case: str) -> None:
    build, merges = CASES[case]
    head, r = "b" * 40, "c" * 40
    state = yk.my_pr_state(_picker_pr(head, build(head, r)))
    if merges:
        assert state == "ready-to-merge", state
    else:
        assert state == "needs-review", state


# --------------------------------------------------------------------------- agreement


@pytest.mark.parametrize("case", sorted(CASES))
def test_picker_and_gate_agree(yk, tmp_path: Path, case: str) -> None:
    """#991's agreement, plus the decided outcome: both merge exactly when #1228 says so."""
    build, merges = CASES[case]
    sb, head, r = _setup(tmp_path)
    comments = build(head, r)
    gate = _gate(sb, comments)
    picker = yk.my_pr_state(_picker_pr(head, comments))
    assert (picker == "ready-to-merge") == (gate.returncode == 0) == merges, (
        f"{case}: expected merges={merges}, picker {picker!r}, gate rc {gate.returncode}\n"
        f"{_output(gate)}"
    )
