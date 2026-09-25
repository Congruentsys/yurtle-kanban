"""Issue #208 — safe_merge.sh never throws away uncommitted work in the PR's worktree.

Follow-ups from round 2 of the review of PR #200 (#186).

Decided behaviour:

1. If the PR's worktree (found by branch) has uncommitted changes — a modified tracked
   file, a staged change, or an untracked file that is not ignored (i.e.
   `git -C "$wt" status --porcelain` is non-empty) — the script refuses BEFORE removing
   anything: the output says "NOT MERGING", "uncommitted changes" and the worktree path;
   the worktree directory and its dirty file still exist; `gh pr merge` is never called.
2. A clean worktree is removed, then the PR is merged (the existing behaviour; control).
   An ignored file (like the `.venv` symlink every pairit worktree has) is not dirt.
3. pairit's SKILL says the verdict comment's first line/bytes are `reviewed-at-sha:`, with
   no leading whitespace (or BOM), in the paragraph that states the verdict format; and
   step 4 mentions the refusal on uncommitted changes.

The ABA check-run item of the issue is accepted as negligible: no test.

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
SKILL_MD = base.SKILL_MD

needs_tools = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("jq") is None,
    reason="needs git and jq (the stub gh pipes --jq through jq)",
)


def _out(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


def _dirty_modified(sb: Sandbox) -> Path:
    path = sb.worktree / "feature.txt"
    path.write_text("new feature\nplus an edit nobody committed\n")
    return path


def _dirty_staged(sb: Sandbox) -> Path:
    path = sb.worktree / "staged.txt"
    path.write_text("staged but never committed\n")
    base._git(sb.worktree, "add", "staged.txt")
    return path


def _dirty_untracked(sb: Sandbox) -> Path:
    path = sb.worktree / "notes-untracked.txt"
    path.write_text("scratch work, never added\n")
    return path


DIRT = {"modified": _dirty_modified, "staged": _dirty_staged, "untracked": _dirty_untracked}


# --------------------------------------------------------------------------- 1. refuse


@needs_tools
class TestDirtyWorktreeRefuses:
    @pytest.mark.parametrize("kind", list(DIRT))
    def test_dirty_worktree_refuses_before_removing_anything(
        self, tmp_path: Path, kind: str
    ) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        dirty = DIRT[kind](sb)
        status = base._git(sb.worktree, "status", "--porcelain")
        assert status, f"setup: the {kind} change should make the worktree dirty"

        r = sb.run(GREEN, sb.approve())
        out = _out(r)

        assert r.returncode != 0, f"merged over the {kind} change in the worktree:\n{out}"
        assert not sb.merge_calls(), sb.calls()
        assert "NOT MERGING" in out, out
        assert "uncommitted changes" in out, out
        assert str(sb.worktree) in out, f"refusal does not name the worktree:\n{out}"
        assert sb.worktree.is_dir(), f"worktree removed although it held a {kind} change"
        assert dirty.exists(), f"the {kind} file is gone"
        assert base._git(sb.worktree, "status", "--porcelain") == status, (
            "the worktree's uncommitted changes were altered"
        )


# --------------------------------------------------------------------------- 2. controls


@needs_tools
class TestCleanWorktreeControls:
    """Green before and after the fix."""

    def test_clean_worktree_is_removed_then_merged(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        r = sb.run(GREEN, sb.approve())
        assert r.returncode == 0, _out(r)
        merges = sb.merge_calls()
        assert len(merges) == 1, sb.calls()
        assert merges[0]["worktree_exists_at_merge"] is False, merges[0]
        assert not sb.worktree.exists()

    def test_only_an_ignored_file_is_not_dirt(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        common = Path(
            base._git(sb.worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
        )
        (common / "info").mkdir(exist_ok=True)
        with (common / "info" / "exclude").open("a") as fh:
            fh.write(".venv\n")
        (sb.worktree / ".venv").symlink_to(tmp_path)
        assert base._git(sb.worktree, "status", "--porcelain") == "", "setup: not ignored"

        r = sb.run(GREEN, sb.approve())
        assert r.returncode == 0, _out(r)
        assert len(sb.merge_calls()) == 1, sb.calls()


# --------------------------------------------------------------------------- 3. docs


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


class TestSkillDocs:
    def test_verdict_paragraph_says_first_line_with_no_leading_whitespace(self) -> None:
        paras = [p for p in _paragraphs(SKILL_MD.read_text()) if "reviewed-at-sha" in p]
        assert paras, "SKILL.md never mentions reviewed-at-sha"
        ok = [
            p
            for p in paras
            if re.search(r"first (line|bytes)", p, re.I) and re.search(r"no leading", p, re.I)
        ]
        assert ok, (
            "no SKILL.md paragraph about reviewed-at-sha says it is the first line/bytes"
            " of the comment AND that leading whitespace is not accepted ('no leading')"
        )

    def test_step4_mentions_the_uncommitted_changes_refusal(self) -> None:
        step4 = base.TestPairitSkillUsesSafeMerge._step4()
        assert "uncommitted" in step4, "pairit step 4 does not mention the dirty-worktree refusal"
