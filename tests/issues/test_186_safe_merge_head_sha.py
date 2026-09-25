"""Issue #186 — safe_merge.sh ties the merge to the approved head sha.

Decided behaviour (follow-ups from the PR #185 review of #167):

1. Head binding: read the PR's `headRefOid` once, refuse unless `origin/<branch>` is at
   it ("NOT MERGING #P: origin/<branch> is at X, the PR head is Y"), and merge with
   `gh pr merge <P> --merge --delete-branch --match-head-commit <sha>`.
2. Approval binding: the LATEST comment whose first line starts `reviewed-at-sha:` must
   name the head sha on line 1 and say `verdict: approve` on line 2, else refuse with
   "no approve verdict at <head sha>".
3. A missing `origin/<branch>` is reported as "origin/<branch> not found", never as a
   conflict with origin/main.
4. The check filter runs inside `--jq`, so check names holding a tab or newline neither
   garble the report nor refuse an all-green PR.
5. The tests need `jq` for the stub `gh`: they skip cleanly without it.

Reuses the #167 harness (stub `gh` on PATH + throwaway git repos with a bare origin),
whose stub serves PR comments from `STUB_GH_COMMENTS` (`gh pr view --json comments`
and `gh api .../comments`); `Sandbox.run` defaults to an approve verdict at the head,
and every test here passes its comments explicitly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import test_pairit_safe_merge as base

PR = base.PR
BRANCH = base.BRANCH
GREEN = base.GREEN

needs_tools = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("jq") is None,
    reason="needs git and jq (the stub gh pipes --jq through jq)",
)

verdict = base.verdict
Sandbox = base.Sandbox


def _out(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


def _push_new_head(sb: Sandbox) -> str:
    """Push one more commit to origin/<branch> behind the stub's back; return its sha."""
    (sb.worktree / "late.txt").write_text("pushed after the checks were read\n")
    base._git(sb.worktree, "add", "-A")
    base._git(sb.worktree, "commit", "-q", "-m", "late push")
    base._git(sb.worktree, "push", "-q", "origin", BRANCH)
    return base._git(sb.worktree, "rev-parse", "HEAD")


# --------------------------------------------------------------------------- controls


@needs_tools
class TestControls:
    """Green before and after the fix."""

    def test_all_green_and_approved_at_head_merges(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run(GREEN, sb.approve())
        assert r.returncode == 0, _out(r)
        assert len(sb.merge_calls()) == 1, sb.calls()

    def test_failure_check_refuses(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run(base._with("test (3.10)", "FAILURE"), sb.approve())
        assert r.returncode != 0, _out(r)
        assert "test (3.10)" in _out(r)
        assert not sb.merge_calls(), sb.calls()


# --------------------------------------------------------------------------- 1. head


@needs_tools
class TestHeadBinding:
    def test_merge_passes_match_head_commit_with_the_head_sha(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run(GREEN, sb.approve())
        assert r.returncode == 0, _out(r)
        merges = sb.merge_calls()
        assert len(merges) == 1, sb.calls()
        argv = merges[0]["argv"]
        assert "--merge" in argv and "--delete-branch" in argv, argv
        flat = " ".join(argv)
        assert (
            f"--match-head-commit {sb.head_sha}" in flat
            or f"--match-head-commit={sb.head_sha}" in flat
        ), f"merge not bound to head {sb.head_sha}: {argv}"

    def test_origin_branch_moved_past_the_pr_head_refuses(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        pr_head = sb.head_sha
        moved = _push_new_head(sb)
        r = sb.run(GREEN, [verdict(pr_head, "approve")], head_sha=pr_head)
        out = _out(r)
        assert r.returncode != 0, f"merged although origin/{BRANCH} moved:\n{out}"
        assert not sb.merge_calls(), sb.calls()
        assert f"origin/{BRANCH} is at" in out, out
        assert "the PR head is" in out, out
        assert moved[:7] in out and pr_head[:7] in out, out
        assert sb.worktree.exists(), "worktree removed although the merge was refused"


# --------------------------------------------------------------------------- 2. approval


def _refused_for_no_approve(sb: Sandbox, r: subprocess.CompletedProcess[str]) -> None:
    out = _out(r)
    assert r.returncode != 0, f"merged without an approve verdict at head:\n{out}"
    assert not sb.merge_calls(), sb.calls()
    assert f"no approve verdict at {sb.head_sha}" in out, out
    assert sb.worktree.exists(), "worktree removed although the merge was refused"


@needs_tools
class TestApprovalBinding:
    def test_no_verdict_comment_refuses(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        chatter = [{"body": "LGTM, nice work"}, {"body": "verdict: approve"}]
        _refused_for_no_approve(sb, sb.run(GREEN, chatter))

    def test_latest_verdict_is_changes_at_head_refuses(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        _refused_for_no_approve(sb, sb.run(GREEN, [verdict(sb.head_sha, "changes")]))

    def test_approve_at_an_older_sha_refuses(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        older = base._git(sb.checkout, "rev-parse", "origin/main~1")
        assert older != sb.head_sha
        _refused_for_no_approve(sb, sb.run(GREEN, [verdict(older, "approve")]))

    def test_approve_then_newer_changes_at_head_refuses(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        comments = [verdict(sb.head_sha, "approve"), verdict(sb.head_sha, "changes")]
        _refused_for_no_approve(sb, sb.run(GREEN, comments))

    def test_changes_then_newer_approve_at_head_merges(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        comments = [
            verdict(sb.head_sha, "changes"),
            {"body": "fixed, please re-review"},
            verdict(sb.head_sha, "approve"),
        ]
        r = sb.run(GREEN, comments)
        assert r.returncode == 0, _out(r)
        assert len(sb.merge_calls()) == 1, sb.calls()


# --------------------------------------------------------------------------- 3. missing ref


@needs_tools
class TestMissingBranchRef:
    def _assert_not_found(
        self, sb: Sandbox, r: subprocess.CompletedProcess[str], branch: str
    ) -> None:
        out = _out(r)
        assert r.returncode != 0, out
        assert not sb.merge_calls(), sb.calls()
        assert f"origin/{branch} not found" in out, out
        assert not re.search(r"conflicts with origin/main", out), out

    def test_deleted_branch(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        base._git(sb.checkout, "push", "-q", "origin", "--delete", BRANCH)
        r = sb.run(GREEN, sb.approve())
        self._assert_not_found(sb, r, BRANCH)

    def test_fork_branch_absent_from_origin(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run(GREEN, sb.approve(), branch="contributor-fork-branch")
        self._assert_not_found(sb, r, "contributor-fork-branch")

    def test_empty_head_ref_name(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run(GREEN, sb.approve(), branch="")
        self._assert_not_found(sb, r, "")


# --------------------------------------------------------------------------- 4. filter


@needs_tools
class TestExactCheckFilter:
    @pytest.mark.parametrize("name", ["lint\tstrict", "docs\nbuild"], ids=["tab", "newline"])
    def test_green_check_with_odd_name_merges(self, tmp_path: Path, name: str) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run([*GREEN, {"name": name, "state": "SUCCESS"}], sb.approve())
        assert r.returncode == 0, f"all-green PR refused:\n{_out(r)}"
        assert len(sb.merge_calls()) == 1, sb.calls()

    @pytest.mark.parametrize("name", ["lint\tstrict", "docs\nbuild"], ids=["tab", "newline"])
    def test_failing_check_with_odd_name_refuses_with_state(
        self, tmp_path: Path, name: str
    ) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run([*GREEN, {"name": name, "state": "FAILURE"}], sb.approve())
        out = _out(r)
        assert r.returncode != 0, out
        assert not sb.merge_calls(), sb.calls()
        assert "FAILURE" in out, out
        assert re.split(r"[\t\n]", name)[0] in out, out


# --------------------------------------------------------------------------- 5. jq


class TestJqSkip:
    def test_existing_safe_merge_tests_skip_without_jq(self) -> None:
        src = Path(base.__file__).read_text()
        marks = re.findall(r"^pytestmark\s*=.*?(?=^\S)", src, re.M | re.S)
        assert any(re.search(r"which\(\s*[\"']jq[\"']\s*\)", m) for m in marks), (
            "tests/test_pairit_safe_merge.py has no module-level skip when jq is missing"
        )


# --------------------------------------------------------------------------- round 2 (PR #200)


def _stage_late_commit(sb: Sandbox) -> str:
    """Make a commit on the PR branch WITHOUT pushing it; the worktree goes back to the head."""
    (sb.worktree / "late.txt").write_text("pushed while the checks were being read\n")
    base._git(sb.worktree, "add", "-A")
    base._git(sb.worktree, "commit", "-q", "-m", "late push")
    new = base._git(sb.worktree, "rev-parse", "HEAD")
    base._git(sb.worktree, "reset", "-q", "--hard", sb.head_sha)
    return new


@needs_tools
class TestHeadReadBeforeChecks:
    """Review of PR #200, blocking: the head must be read BEFORE the checks.

    Here a commit lands on origin/<branch> while `gh pr checks` runs, and from then on
    `gh pr view` serves the new sha, which even carries an approve. The checks that were
    read belong to the OLD head, so nothing may merge at the new sha: pinning the merge
    to the old sha, or refusing, are both fine.
    """

    def test_no_merge_at_a_head_that_landed_during_the_checks(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        old = sb.head_sha
        new = _stage_late_commit(sb)
        head_file = tmp_path / "served-head"
        hook = (
            f"git -C {sb.checkout} push -q origin {new}:refs/heads/{BRANCH}"
            f" && echo {new} > {head_file}"
        )
        comments = [verdict(old, "approve"), verdict(new, "approve")]
        r = sb.run(
            GREEN,
            comments,
            extra_env={"STUB_GH_ON_CHECKS": hook, "STUB_GH_HEAD_FILE": str(head_file)},
        )
        assert head_file.exists(), f"the checks hook never ran: {sb.calls()}\n{_out(r)}"
        for call in sb.merge_calls():
            flat = " ".join(call["argv"])
            assert new not in flat, f"merged at {new}, whose checks were never read: {flat}"
            assert f"--match-head-commit {old}" in flat or f"--match-head-commit={old}" in flat, (
                f"merge not pinned to the head whose checks were read ({old}): {flat}"
            )


@needs_tools
class TestStaleTrackingRef:
    """Review of PR #200: a branch deleted on origin must not resolve via a stale ref."""

    def test_branch_gone_on_origin_but_tracking_ref_left_refuses(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        base._git(sb.origin, "update-ref", "-d", f"refs/heads/{BRANCH}")
        stale = base._git(sb.checkout, "rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}")
        assert stale == sb.head_sha, "setup: the stale tracking ref should still be there"
        r = sb.run(GREEN, sb.approve())
        out = _out(r)
        assert r.returncode != 0, f"merged a branch origin no longer has:\n{out}"
        assert not sb.merge_calls(), sb.calls()
        assert f"origin/{BRANCH} not found" in out, out


@needs_tools
class TestVerdictAuthor:
    """Review of PR #200: the repo is public, so a stranger's verdict counts for nothing."""

    @pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR"])
    def test_strangers_approve_alone_refuses(self, tmp_path: Path, association: str) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run(GREEN, [verdict(sb.head_sha, "approve", association)])
        _refused_for_no_approve(sb, r)

    @pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR"])
    def test_strangers_approve_cannot_override_members_changes(
        self, tmp_path: Path, association: str
    ) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        comments = [
            verdict(sb.head_sha, "changes"),
            verdict(sb.head_sha, "approve", association),
        ]
        _refused_for_no_approve(sb, sb.run(GREEN, comments))
