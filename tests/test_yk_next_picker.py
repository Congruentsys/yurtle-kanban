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
