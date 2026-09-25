"""Issue #229 — safe_merge.sh's dirty-worktree check ignores git config that hides changes.

Follow-up from the review of PR #226 (#208). `git status --porcelain` honours
`status.showUntrackedFiles`: with it set to `no`, an untracked file in the PR worktree
does not show up, and `git worktree remove --force` deletes it — the loss #208 prevents.

Decided behaviour:

1. With `git config status.showUntrackedFiles no` in the sandbox repo (the main clone's
   local config; its worktrees share it), an untracked file in the PR worktree still makes
   the script refuse ("NOT MERGING", "uncommitted changes"); the file survives and
   `gh pr merge` is never called. RED before the fix.
2. Same config and a modified tracked file: refused (control; tracked changes still show).
3. Controls: a clean worktree merges; an ignored `.venv` symlink is still not dirt, even
   with showUntrackedFiles=no.

Not tested: submodule dirtiness (`diff.ignoreSubmodules`, `--ignore-submodules=none`) —
the #167 harness has no submodule support. Also out of scope here: mid-rebase (detached)
worktrees and skip-worktree / assume-unchanged files.

Reuses the #167 harness (stub `gh` on PATH + throwaway git repos with a bare origin).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests import test_pairit_safe_merge as base

GREEN = base.GREEN
Sandbox = base.Sandbox

needs_tools = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("jq") is None,
    reason="needs git and jq (the stub gh pipes --jq through jq)",
)


def _out(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


def _hide_untracked(sb: Sandbox) -> None:
    """`status.showUntrackedFiles no` in the repo config the PR worktree shares."""
    base._git(sb.checkout, "config", "status.showUntrackedFiles", "no")
    assert base._git(sb.worktree, "config", "status.showUntrackedFiles") == "no", (
        "setup: the worktree does not see the repo's config"
    )


def _assert_refused(sb: Sandbox, r: subprocess.CompletedProcess[str], dirty: Path) -> None:
    out = _out(r)
    assert r.returncode != 0, f"merged over uncommitted work in the worktree:\n{out}"
    assert not sb.merge_calls(), sb.calls()
    assert "NOT MERGING" in out, out
    assert "uncommitted changes" in out, out
    assert sb.worktree.is_dir(), "worktree removed although it held uncommitted work"
    assert dirty.exists(), f"{dirty.name} is gone"


# --------------------------------------------------------------------------- 1. refuse


@needs_tools
class TestHiddenUntrackedStillRefuses:
    def test_untracked_file_refused_with_show_untracked_no(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        _hide_untracked(sb)
        dirty = sb.worktree / "notes-untracked.txt"
        dirty.write_text("scratch work, never added\n")
        assert base._git(sb.worktree, "status", "--porcelain") == "", (
            "setup: the config should hide the untracked file from plain status"
        )

        r = sb.run(GREEN, sb.approve())
        _assert_refused(sb, r, dirty)
        assert dirty.read_text() == "scratch work, never added\n"


# --------------------------------------------------------------------------- 2. controls


@needs_tools
class TestControls:
    """Green before and after the fix."""

    def test_modified_tracked_file_refused_with_show_untracked_no(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        _hide_untracked(sb)
        dirty = sb.worktree / "feature.txt"
        dirty.write_text("new feature\nplus an edit nobody committed\n")

        r = sb.run(GREEN, sb.approve())
        _assert_refused(sb, r, dirty)

    def test_clean_worktree_merges_with_show_untracked_no(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        _hide_untracked(sb)
        r = sb.run(GREEN, sb.approve())
        assert r.returncode == 0, _out(r)
        merges = sb.merge_calls()
        assert len(merges) == 1, sb.calls()
        assert merges[0]["worktree_exists_at_merge"] is False, merges[0]
        assert not sb.worktree.exists()

    def test_ignored_venv_symlink_is_not_dirt_with_show_untracked_no(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        _hide_untracked(sb)
        common = Path(
            base._git(sb.worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
        )
        (common / "info").mkdir(exist_ok=True)
        with (common / "info" / "exclude").open("a") as fh:
            fh.write(".venv\n")
        (sb.worktree / ".venv").symlink_to(tmp_path)
        shown = base._git(sb.worktree, "status", "--porcelain", "--untracked-files=normal")
        assert shown == "", "setup: .venv is not ignored"

        r = sb.run(GREEN, sb.approve())
        assert r.returncode == 0, _out(r)
        assert len(sb.merge_calls()) == 1, sb.calls()
