"""Issue #265 — safe_merge.sh hidden-edit check: sparse checkout hint; test the `s` tag.

Follow-ups from the review of PR #263 (#247).

Decided behaviour (conservative):

1. A sparse-checkout PR worktree (`core.sparseCheckout` on; files outside the patterns are
   skip-worktree and absent) is STILL refused — no relaxation — but the refusal mentions
   "sparse checkout" and `git sparse-checkout disable`, not only "(clear the flag)". No
   merge; the worktree and its sparse state are untouched. Built with
   `git sparse-checkout set --no-cone /shared.txt` (the harness repo has only root files,
   which cone mode always keeps), so `feature.txt` becomes skip-worktree.
2. The combined `s` tag of `git ls-files -v`: a file marked BOTH --skip-worktree and
   --assume-unchanged is refused, and one output line names
   "skip-worktree + assume-unchanged" together with the file.
3. Control: a non-sparse worktree with a plain --skip-worktree file keeps the old
   "(clear the flag)" hint and says nothing about sparse checkout.

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

needs_tools = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("jq") is None,
    reason="needs git and jq (the stub gh pipes --jq through jq)",
)

SPARSE_MSG = re.compile(r"sparse[- ]checkout", re.I)


def _out(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


def _ls_v(sb: Sandbox) -> str:
    return base._git(sb.worktree, "ls-files", "-v")


def _assert_refused(sb: Sandbox, r: subprocess.CompletedProcess[str]) -> str:
    out = _out(r)
    assert r.returncode != 0, f"merged over a worktree with hidden files:\n{out}"
    assert not sb.merge_calls(), sb.calls()
    assert "NOT MERGING" in out, out
    assert sb.worktree.is_dir(), "worktree removed although it held hidden files"
    return out


# --------------------------------------------------------------------------- 1. sparse


@needs_tools
class TestSparseWorktreeHint:
    def test_sparse_worktree_refused_with_sparse_hint(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        w = sb.worktree
        base._git(w, "sparse-checkout", "set", "--no-cone", "/shared.txt")
        assert base._git(w, "config", "--bool", "core.sparseCheckout") == "true", "setup"
        assert not (w / "feature.txt").exists(), "setup: feature.txt should be outside"
        assert "S feature.txt" in _ls_v(sb).splitlines(), f"setup: {_ls_v(sb)!r}"
        ls_before = _ls_v(sb)
        patterns = base._git(w, "sparse-checkout", "list")
        head = base._git(w, "rev-parse", "HEAD")

        r = sb.run(GREEN, sb.approve())
        out = _assert_refused(sb, r)

        assert SPARSE_MSG.search(out), f"refusal does not mention sparse checkout:\n{out}"
        assert "git sparse-checkout disable" in out, f"no `disable` hint:\n{out}"
        assert base._git(w, "config", "--bool", "core.sparseCheckout") == "true"
        assert base._git(w, "sparse-checkout", "list") == patterns, "patterns changed"
        assert _ls_v(sb) == ls_before, "index flags changed"
        assert base._git(w, "rev-parse", "HEAD") == head, "HEAD moved"
        assert (w / "shared.txt").is_file(), "shared.txt removed"


# --------------------------------------------------------------------------- 2. `s` tag


@needs_tools
class TestCombinedFlags:
    def test_skip_worktree_and_assume_unchanged_is_refused(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        w = sb.worktree
        path = w / "feature.txt"
        base._git(w, "update-index", "--skip-worktree", "feature.txt")
        base._git(w, "update-index", "--assume-unchanged", "feature.txt")
        path.write_text("new feature\nplus an edit both flags hide\n")
        assert "s feature.txt" in _ls_v(sb).splitlines(), f"setup: {_ls_v(sb)!r}"
        content = path.read_text()

        r = sb.run(GREEN, sb.approve())
        out = _assert_refused(sb, r)

        lines = [ln for ln in out.splitlines() if "skip-worktree + assume-unchanged" in ln]
        assert any("feature.txt" in ln for ln in lines), (
            f"no line names 'skip-worktree + assume-unchanged' and feature.txt:\n{out}"
        )
        assert path.read_text() == content, "feature.txt was altered"
        assert "s feature.txt" in _ls_v(sb).splitlines(), "flags were cleared"


# --------------------------------------------------------------------------- 3. control


@needs_tools
class TestNonSparseControl:
    def test_plain_skip_worktree_keeps_clear_the_flag_hint(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        w = sb.worktree
        base._git(w, "update-index", "--skip-worktree", "feature.txt")
        r_cfg = subprocess.run(
            ["git", "config", "--bool", "core.sparseCheckout"],
            cwd=w,
            capture_output=True,
            text=True,
        )
        assert r_cfg.stdout.strip() != "true", "setup: worktree should not be sparse"

        r = sb.run(GREEN, sb.approve())
        out = _assert_refused(sb, r)

        assert "(clear the flag)" in out, f"old hint missing:\n{out}"
        assert "skip-worktree" in out and "feature.txt" in out, out
        assert not SPARSE_MSG.search(out), f"non-sparse refusal mentions sparse:\n{out}"
