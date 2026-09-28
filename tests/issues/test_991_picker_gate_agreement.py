# ruff: noqa: F811  (the borrowed `yk` fixture)
"""Issue #991, r1 F2: the same comment lists fed to BOTH the picker (`my_pr_state`)
and the gate (`safe_merge.sh`, via test_pairit_safe_merge's Sandbox, CI green): the
picker says `ready-to-merge` exactly when the gate merges."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_pairit_safe_merge import GREEN, Sandbox, _git
from tests.test_yk_next_picker import pr, yk  # noqa: F401 (fixture)

OTHER = "0123456789abcdef0123456789abcdef01234567"


def _v(sha: str, word: str) -> str:
    return f"reviewed-at-sha: {sha}\nverdict: {word}\n"


def _fx(head: str, reviewed: str) -> str:
    return f"fixes-at-sha: {head}\nfor-review-at: {reviewed}\n"


CASES = {
    "approve-at-head": lambda h, r: [_v(h, "approve")],
    "changes-at-head": lambda h, r: [_v(h, "changes")],
    "approve-then-stale": lambda h, r: [_v(h, "approve"), _v(OTHER, "changes")],
    "approve-then-upper-stale": lambda h, r: [_v(h, "approve"), _v(OTHER.upper(), "changes")],
    "approve-then-prefix": lambda h, r: [_v(h, "approve"), _v(OTHER[:12], "changes")],
    "approve-then-bad-fixes": lambda h, r: [_v(h, "approve"), _fx(h, "garbage")],
    "approve-then-fixes-other-head": lambda h, r: [_v(h, "approve"), _fx(OTHER, r)],
    "approve-then-lgtm": lambda h, r: [_v(h, "approve"), f"reviewed-at-sha: {h}\nverdict: lgtm"],
    "fixed": lambda h, r: [_v(r, "changes"), _fx(h, r)],
    "fixed-then-unreviewed-fixes": lambda h, r: [_v(r, "changes"), _fx(h, r), _fx(h, OTHER)],
    "fixed-then-stale": lambda h, r: [_v(r, "changes"), _fx(h, r), _v(OTHER, "changes")],
    "fixes-without-verdict": lambda h, r: [_fx(h, r)],
    "fixes-self": lambda h, r: [_v(h, "changes"), _fx(h, h)],
    "prefix-approve": lambda h, r: [_v(h[:12], "approve")],
    "upper-approve": lambda h, r: [_v(h.upper(), "approve")],
    "crlf-approve": lambda h, r: [_v(h, "approve").replace("\n", "\r\n")],
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_picker_and_gate_agree(yk, tmp_path: Path, case: str) -> None:
    sb = Sandbox(tmp_path, conflict=False)
    head, reviewed = sb.head_sha, _git(sb.worktree, "rev-parse", "HEAD~1")
    bodies = CASES[case](head, reviewed)
    gate = sb.run(GREEN, [{"body": b, "association": "MEMBER"} for b in bodies])
    picker = yk.my_pr_state(pr(1, head=head, comments=bodies))
    assert (picker == "ready-to-merge") == (gate.returncode == 0), (
        f"{case}: picker {picker!r}, gate rc {gate.returncode}\n{gate.stdout}{gate.stderr}"
    )
