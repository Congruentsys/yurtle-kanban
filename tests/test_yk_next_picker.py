"""Tests for the work-loop picker script `.claude/skills/yk-next/yk_next.py` (#108).

The script is loaded by path, never run: running it without --dry-run claims a real issue.
`gh` / `gh_json` are monkeypatched to serve synthetic PRs and issues.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / ".claude/skills/yk-next/yk_next.py"
PAIRIT = REPO / ".claude/skills/pairit/SKILL.md"
ME = "hankh1844"
PEER = "hankh95"


@pytest.fixture
def yk() -> ModuleType:
    spec = importlib.util.spec_from_file_location("yk_next_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pr(number: int, *, author: str = ME, labels: tuple[str, ...] = (), head: str = "a" * 40,
       draft: bool = False, mergeable: str = "MERGEABLE", checks: list | None = None,
       comments: list[str] = (), fixes: tuple[int, ...] = ()) -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "author": {"login": author},
        "labels": [{"name": n} for n in labels],
        "headRefName": f"fix/{number}",
        "headRefOid": head,
        "isDraft": draft,
        "mergeable": mergeable,
        "statusCheckRollup": (
            [{"status": "COMPLETED", "conclusion": "SUCCESS"}] if checks is None else checks
        ),
        "comments": [{"body": b} for b in comments],
        "closingIssuesReferences": [{"number": n} for n in fixes],
    }


def issue(number: int, *, labels: tuple[str, ...] = (), assignees: tuple[str, ...] = (),
          body: str = "", title: str | None = None) -> dict:
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "labels": [{"name": n} for n in labels],
        "assignees": [{"login": a} for a in assignees],
        "body": body,
    }


def verdict(sha: str, v: str) -> str:
    return f"reviewed-at-sha: {sha}\nverdict: {v}\n\nnotes"


def run_main(yk: ModuleType, monkeypatch, capsys, prs: list[dict], issues: list[dict]) -> str:
    def fake_gh(*args: str) -> str:
        if args[:2] == ("api", "user"):
            return ME + "\n"
        raise AssertionError(f"unexpected gh call in dry-run: {args}")

    def fake_gh_json(*args: str):
        if args[0] == "pr":
            return [dict(p) for p in prs]
        if args[0] == "issue" and args[1] == "list":
            return [dict(i) for i in issues]
        raise AssertionError(f"unexpected gh_json call: {args}")

    monkeypatch.setattr(yk, "gh", fake_gh)
    monkeypatch.setattr(yk, "gh_json", fake_gh_json)
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run"])
    yk.main()
    return capsys.readouterr().out


# --- 1. DEPENDS parsing ------------------------------------------------------------------

@pytest.mark.parametrize("body,expected", [
    ("depends on #6, #7, and #8", {6, 7, 8}),
    ("depends on **#12**", {12}),
    ("Blocked-by: #17", {17}),
])
def test_depends_new_forms(yk, body, expected):
    assert yk.depends_on(body) == expected


@pytest.mark.parametrize("body,expected", [
    ("Depends on: #5", {5}),
    ("blocked by #8, #9", {8, 9}),
    ("depends on #6 and #7", {6, 7}),
    ("requires #1 & #2", {1, 2}),
    ("see #99", set()),
    ("", set()),
])
def test_depends_existing_forms_control(yk, body, expected):
    assert yk.depends_on(body) == expected


def test_main_skips_issue_waiting_on_oxford_comma_dep(yk, monkeypatch, capsys):
    issues = [
        issue(5, body="depends on #6, #7, and #8"),
        issue(8, assignees=(PEER,)),  # open dependency, not itself claimable
        issue(9),
    ]
    out = run_main(yk, monkeypatch, capsys, [], issues)
    assert "waits on #8" in out
    assert "WOULD CLAIM ISSUE #9" in out
    assert "WOULD CLAIM ISSUE #5" not in out


# --- 2. pairit merge step tolerates a missing worktree -----------------------------------

def test_pairit_merge_worktree_remove_tolerates_missing_worktree():
    text = PAIRIT.read_text()
    m = re.search(r"\*\*4\. Merge\*\*.*?```bash\n(.*?)```", text, re.S)
    assert m, "pairit SKILL.md step 4 (Merge) code block not found"
    lines = [ln for ln in m.group(1).splitlines() if "git worktree remove" in ln]
    assert lines, "step 4 has no `git worktree remove` command"
    for ln in lines:
        cmd = ln.split("#", 1)[0]
        assert "|| true" in cmd or "2>/dev/null" in cmd, (
            f"worktree remove fails when the worktree is already gone: {ln.strip()!r}"
        )


# --- 3. SKIP reasons printed in full ------------------------------------------------------

def test_held_my_pr_reason_not_truncated(yk, monkeypatch, capsys):
    prs = [pr(20, labels=("needs-decision",))]
    out = run_main(yk, monkeypatch, capsys, prs, [issue(30)])
    line = next(ln for ln in out.splitlines() if "my PR #20" in ln)
    assert "held: needs-decision" in line


def test_candidate_open_pr_reason_not_truncated(yk, monkeypatch, capsys):
    head = "b" * 40
    prs = [pr(40, author=PEER, head=head, comments=[verdict(head, "approve")], fixes=(31,))]
    issues = [issue(31, assignees=(PEER,)), issue(32)]
    out = run_main(yk, monkeypatch, capsys, prs, issues)
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("#31"))
    assert "an open PR fixes it" in line
    assert "WOULD CLAIM ISSUE #32" in out


# --- negative controls --------------------------------------------------------------------

def test_verdict_approve_at_head_ready_to_merge(yk):
    head = "c" * 40
    assert yk.my_pr_state(pr(1, head=head, comments=[verdict(head[:12], "approve")])) \
        == "ready-to-merge"


def test_verdict_stale_sha_needs_review(yk):
    head = "c" * 40
    assert yk.my_pr_state(pr(1, head=head, comments=[verdict("d" * 12, "approve")])) \
        == "needs-review"


def test_verdict_latest_wins(yk):
    head = "c" * 40
    p = pr(1, head=head, comments=[verdict(head, "changes"), verdict(head, "approve")])
    assert yk.verdict_at_head(p) == "approve"
    p = pr(1, head=head, comments=[verdict(head, "approve"), verdict(head, "changes")])
    assert yk.verdict_at_head(p) == "changes"


def test_empty_rollup_is_wait_ci(yk):
    head = "c" * 40
    p = pr(1, head=head, checks=[], comments=[verdict(head, "approve")])
    assert yk.ci_state(p) == "pending"
    assert yk.my_pr_state(p) == "wait-ci"


def test_draft_pr_of_mine_is_parked(yk, monkeypatch, capsys):
    out = run_main(yk, monkeypatch, capsys, [pr(20, draft=True)], [issue(30)])
    assert "RESUME PR" not in out
    assert "WOULD CLAIM ISSUE #30" in out


def test_bug_claimed_before_lower_unlabelled(yk, monkeypatch, capsys):
    out = run_main(yk, monkeypatch, capsys, [], [issue(3), issue(7, labels=("bug",))])
    assert "WOULD CLAIM ISSUE #7" in out


# --- #130: --skip-prs, pairit push/range-diff, bullet-safe DEPENDS, pipelining ------------

YK_LOOP = REPO / ".claude/skills/yk-loop/SKILL.md"


def run_main_args(yk: ModuleType, monkeypatch, capsys, prs: list[dict], issues: list[dict],
                  *flags: str) -> str:
    """Like run_main, but with extra CLI flags; an argparse rejection becomes a test failure."""
    def fake_gh(*args: str) -> str:
        if args[:2] == ("api", "user"):
            return ME + "\n"
        raise AssertionError(f"unexpected gh call in dry-run: {args}")

    def fake_gh_json(*args: str):
        if args[0] == "pr":
            return [dict(p) for p in prs]
        if args[0] == "issue" and args[1] == "list":
            return [dict(i) for i in issues]
        raise AssertionError(f"unexpected gh_json call: {args}")

    monkeypatch.setattr(yk, "gh", fake_gh)
    monkeypatch.setattr(yk, "gh_json", fake_gh_json)
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run", *flags])
    try:
        yk.main()
    except SystemExit as e:
        err = capsys.readouterr().err
        pytest.fail(f"picker rejected flags {flags!r} (SystemExit {e.code}): {err.strip()}")
    return capsys.readouterr().out


class TestSkipPrsAndSkillFixes130:
    """--skip-prs jumps to the claim step with every claim rule applied; pairit step 3 push
    uses --force-with-lease; range-diff is base-anchored (no K); DEPENDS stops at a markdown
    bullet; yk-loop's pipelining uses the picker with --skip-prs (#130)"""

    # 1. --skip-prs -------------------------------------------------------------------------

    def test_skip_prs_claims_issue_while_my_pr_in_review(self, yk, monkeypatch, capsys):
        prs = [pr(20)]  # my PR, green, no verdict -> needs-review
        issues = [issue(3), issue(7, labels=("bug",))]
        out = run_main_args(yk, monkeypatch, capsys, prs, issues, "--skip-prs")
        assert "RESUME PR" not in out
        assert "WOULD CLAIM ISSUE #7" in out  # bug first

    def test_skip_prs_applies_all_claim_rules(self, yk, monkeypatch, capsys):
        head = "e" * 40
        prs = [
            pr(20, fixes=(4,)),  # my PR in review; fixes #4
            pr(21, author=PEER, head=head),  # peer PR with no verdict at head
        ]
        issues = [
            issue(2, labels=("bug", "needs-decision")),  # held
            issue(3, labels=("bug",), body="depends on #10"),  # waits on open #10
            issue(4, labels=("bug",)),  # an open PR fixes it
            issue(5, labels=("bug",), assignees=(PEER,)),  # assigned
            issue(6),  # the claimable one
            issue(10, assignees=(PEER,)),
        ]
        out = run_main_args(yk, monkeypatch, capsys, prs, issues, "--skip-prs")
        assert "RESUME PR" not in out
        assert "REVIEW PR" not in out  # goes straight to the claim step
        assert "WOULD CLAIM ISSUE #6" in out
        for n in (2, 3, 4, 5, 10):
            assert f"WOULD CLAIM ISSUE #{n} " not in out

    def test_skip_prs_does_not_review_peer_pr(self, yk, monkeypatch, capsys):
        head = "f" * 40
        prs = [pr(21, author=PEER, head=head)]
        out = run_main_args(yk, monkeypatch, capsys, prs, [issue(30)], "--skip-prs")
        assert "REVIEW PR" not in out
        assert "WOULD CLAIM ISSUE #30" in out

    def test_skip_prs_nothing_claimable_is_nothing_ready(self, yk, monkeypatch, capsys):
        prs = [pr(20, fixes=(4,))]
        issues = [issue(2, labels=("on-hold",)), issue(4), issue(5, assignees=(PEER,))]
        out = run_main_args(yk, monkeypatch, capsys, prs, issues, "--skip-prs")
        assert "RESUME PR" not in out
        assert "NOTHING READY" in out
        assert "WOULD CLAIM" not in out

    def test_control_without_skip_prs_resumes_my_pr(self, yk, monkeypatch, capsys):
        prs = [pr(20)]
        issues = [issue(3), issue(7, labels=("bug",))]
        out = run_main_args(yk, monkeypatch, capsys, prs, issues)
        assert "RESUME PR #20" in out
        assert "WOULD CLAIM" not in out

    # 2. pairit step 3 push -----------------------------------------------------------------

    def test_pairit_step3_push_uses_force_with_lease(self):
        text = PAIRIT.read_text()
        m = re.search(r"\*\*3\. The review.*?```bash\n(.*?)```", text, re.S)
        assert m, "pairit SKILL.md step 3 code block not found"
        pushes = [ln for ln in m.group(1).splitlines() if re.search(r"\bgit\b.*\bpush\b", ln)]
        assert pushes, "step 3 code block has no git push"
        for ln in pushes:
            assert "--force-with-lease" in ln, f"push after rebase is not lease-forced: {ln!r}"

    # 3. range-diff without K ---------------------------------------------------------------

    def test_pairit_range_diff_has_no_undefined_k(self):
        text = PAIRIT.read_text()
        paras = [p for p in re.split(r"\n\s*\n", text) if "range-diff" in p]
        assert paras, "pairit SKILL.md has no range-diff instruction"
        for p in paras:
            assert not re.search(r"~K\b", p), f"range-diff still uses undefined K: {p!r}"
        assert any("merge-base" in p for p in paras), \
            "range-diff instruction is not base-anchored (no merge-base)"

    # 4. DEPENDS ----------------------------------------------------------------------------

    def test_depends_bullet_on_next_line_does_not_join(self, yk):
        assert yk.depends_on("Depends on #3\n* #4 unrelated") == {3}

    @pytest.mark.parametrize("body,expected", [
        ("depends on #6 #7", {6, 7}),
        ("depends on #6, #7, and #8", {6, 7, 8}),
        ("depends on **#12**", {12}),
    ])
    def test_depends_list_forms_control(self, yk, body, expected):
        assert yk.depends_on(body) == expected

    # 5. yk-loop pipelining -----------------------------------------------------------------

    def test_yk_loop_pipelining_uses_picker_skip_prs(self):
        text = YK_LOOP.read_text()
        m = re.search(r"\*\*Don't idle.*?(?=\n\s*\n\*\*)", text, re.S)
        assert m, "yk-loop SKILL.md pipelining paragraph not found"
        para = m.group(0)
        assert "--skip-prs" in para, "pipelining step does not use the picker with --skip-prs"
        assert not re.search(r"yourself\s*\(`gh issue edit[^`]*--add-assignee", para), \
            "pipelining still claims with a bare `gh issue edit --add-assignee`"
