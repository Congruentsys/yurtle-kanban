"""Issue #247 — safe_merge.sh misses a mid-rebase PR worktree and hidden local edits.

Follow-ups from the reviews of PRs #226 and #244 (#208, #229).

Decided behaviour:

1. A PR worktree in the middle of a rebase of the PR branch (detached HEAD; its
   `rebase-merge/head-name` or `rebase-apply/head-name` is `refs/heads/<branch>`) is found,
   and the script refuses: the output says "NOT MERGING", "in the middle of a rebase" (or
   "mid-rebase") and the worktree path; the worktree and its rebase state are untouched;
   `gh pr merge` is never called. Built here by rebasing the PR branch onto a local branch
   that adds `feature.txt` too, so `git rebase` stops on the add/add conflict (merge and
   apply backends), and by `git rebase --exec false`, which stops with a clean tree.
2. Hidden local edits: a tracked file in the PR worktree marked `--skip-worktree` (or,
   separately, `--assume-unchanged`) whose content was modified is refused: "NOT MERGING",
   the flag kind ("skip-worktree" / "assume-unchanged") and the file name; nothing is
   removed; no merge.
3. Controls: a clean worktree merges. A `--skip-worktree` flag on an UNmodified file is
   still refused with the same message (the flag itself hides whether it is modified;
   conservative).

Not tested: submodule dirtiness (`--ignore-submodules=none`) — the #167 harness cannot
build submodules.

Reuses the #167 harness (stub `gh` on PATH + throwaway git repos with a bare origin).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import test_pairit_safe_merge as base

GREEN = base.GREEN
Sandbox = base.Sandbox
BRANCH = base.BRANCH

needs_tools = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("jq") is None,
    reason="needs git and jq (the stub gh pipes --jq through jq)",
)

REBASE_MSG = re.compile(r"in the middle of a rebase|mid-rebase", re.I)


def _out(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


def _git_rc(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _git_dir(sb: Sandbox) -> Path:
    return Path(base._git(sb.worktree, "rev-parse", "--path-format=absolute", "--git-dir"))


def _conflicting_onto(sb: Sandbox) -> str:
    """A local branch off origin/main that adds feature.txt with other content."""
    c = sb.checkout
    _git_rc(c, "fetch", "-q", "origin")
    base._git(c, "checkout", "-q", "-b", "onto-conflict", "origin/main")
    (c / "feature.txt").write_text("a different feature\n")
    base._git(c, "add", "feature.txt")
    base._git(c, "commit", "-q", "-m", "conflicts with the PR")
    base._git(c, "checkout", "-q", "main")
    return "onto-conflict"


def _rebase_conflict_merge(sb: Sandbox) -> str:
    onto = _conflicting_onto(sb)
    r = _git_rc(sb.worktree, "rebase", "--merge", onto)
    assert r.returncode != 0, f"setup: the rebase should stop on a conflict:\n{r.stdout}"
    return "rebase-merge"


def _rebase_conflict_apply(sb: Sandbox) -> str:
    onto = _conflicting_onto(sb)
    r = _git_rc(sb.worktree, "rebase", "--apply", onto)
    assert r.returncode != 0, f"setup: the rebase should stop on a conflict:\n{r.stdout}"
    return "rebase-apply"


def _rebase_exec_stop(sb: Sandbox) -> str:
    """Stops after replaying the PR commit, with a clean tree (rebase-merge)."""
    r = _git_rc(sb.worktree, "rebase", "--exec", "false", "origin/main")
    assert r.returncode != 0, f"setup: the rebase should stop at the exec:\n{r.stdout}"
    return "rebase-merge"


REBASES = {
    "conflict-merge-backend": _rebase_conflict_merge,
    "conflict-apply-backend": _rebase_conflict_apply,
    "exec-stop-clean-tree": _rebase_exec_stop,
}


# --------------------------------------------------------------------------- 1. mid-rebase


@needs_tools
class TestMidRebaseWorktreeRefuses:
    @pytest.mark.parametrize("kind", list(REBASES))
    def test_mid_rebase_worktree_is_found_and_refused(self, tmp_path: Path, kind: str) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        state = REBASES[kind](sb)
        gd = _git_dir(sb)
        head_name = gd / state / "head-name"
        assert head_name.read_text().strip() == f"refs/heads/{BRANCH}", "setup: head-name"
        assert _git_rc(sb.worktree, "symbolic-ref", "-q", "HEAD").returncode != 0, (
            "setup: HEAD should be detached mid-rebase"
        )
        porcelain = base._git(sb.checkout, "worktree", "list", "--porcelain")
        assert f"branch refs/heads/{BRANCH}" not in porcelain, "setup: branch still listed"
        status = base._git(sb.worktree, "status", "--porcelain")
        detached_at = base._git(sb.worktree, "rev-parse", "HEAD")

        r = sb.run(GREEN, sb.approve())
        out = _out(r)

        assert r.returncode != 0, f"merged although the PR worktree is mid-rebase:\n{out}"
        assert not sb.merge_calls(), sb.calls()
        assert "NOT MERGING" in out, out
        assert REBASE_MSG.search(out), f"refusal does not say it is mid-rebase:\n{out}"
        assert str(sb.worktree) in out, f"refusal does not name the worktree:\n{out}"
        assert sb.worktree.is_dir(), "worktree removed although it was mid-rebase"
        assert (gd / state).is_dir(), f"the {state} state was removed"
        assert head_name.read_text().strip() == f"refs/heads/{BRANCH}"
        assert base._git(sb.worktree, "rev-parse", "HEAD") == detached_at, "HEAD moved"
        assert base._git(sb.worktree, "status", "--porcelain") == status, "status changed"


# --------------------------------------------------------------------------- 2. hidden edits


FLAGS = {"skip-worktree": "--skip-worktree", "assume-unchanged": "--assume-unchanged"}


def _hide(sb: Sandbox, flag: str, *, modify: bool) -> Path:
    path = sb.worktree / "feature.txt"
    base._git(sb.worktree, "update-index", FLAGS[flag], "feature.txt")
    if modify:
        path.write_text("new feature\nplus an edit the index flag hides\n")
    shown = base._git(
        sb.worktree,
        "status",
        "--porcelain",
        "--untracked-files=normal",
        "--ignore-submodules=none",
    )
    assert shown == "", f"setup: {flag} should hide the file from status, got {shown!r}"
    return path


def _assert_hidden_refused(
    sb: Sandbox, r: subprocess.CompletedProcess[str], flag: str, path: Path, content: str
) -> None:
    out = _out(r)
    assert r.returncode != 0, f"merged over a {flag} file in the worktree:\n{out}"
    assert not sb.merge_calls(), sb.calls()
    assert "NOT MERGING" in out, out
    assert flag in out, f"refusal does not name the {flag} flag:\n{out}"
    assert path.name in out, f"refusal does not name {path.name}:\n{out}"
    assert sb.worktree.is_dir(), f"worktree removed although it held a {flag} file"
    assert path.read_text() == content, f"{path.name} was altered"


@needs_tools
class TestHiddenEditsRefuse:
    @pytest.mark.parametrize("flag", list(FLAGS))
    def test_modified_flagged_file_is_refused(self, tmp_path: Path, flag: str) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        path = _hide(sb, flag, modify=True)
        content = path.read_text()

        r = sb.run(GREEN, sb.approve())
        _assert_hidden_refused(sb, r, flag, path, content)

    def test_unmodified_skip_worktree_file_is_still_refused(self, tmp_path: Path) -> None:
        """Decided: the flag itself hides whether the file is modified — refuse."""
        sb = Sandbox(tmp_path, conflict=False)
        path = _hide(sb, "skip-worktree", modify=False)
        content = path.read_text()

        r = sb.run(GREEN, sb.approve())
        _assert_hidden_refused(sb, r, "skip-worktree", path, content)


# --------------------------------------------------------------------------- 3. controls


@needs_tools
class TestControls:
    """Green before and after the fix."""

    def test_clean_worktree_is_removed_then_merged(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run(GREEN, sb.approve())
        assert r.returncode == 0, _out(r)
        merges = sb.merge_calls()
        assert len(merges) == 1, sb.calls()
        assert merges[0]["worktree_exists_at_merge"] is False, merges[0]
        assert not sb.worktree.exists()
