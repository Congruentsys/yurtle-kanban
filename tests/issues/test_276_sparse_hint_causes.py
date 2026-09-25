"""Issue #276 — safe_merge.sh hidden-edit refusal names EACH cause that is present.

Follow-ups from the review of PR #275 (#265). The merge is refused in every case; only
the hint changes.

Decided behaviour:

* The sparse hint (`git sparse-checkout disable`) appears ONLY when there are `S`/`s`
  entries AND the worktree really has sparse patterns (`git sparse-checkout list`
  non-empty, or a non-empty sparse-checkout file).
* The "clear the flag" hint appears when there are lowercase (assume-unchanged) entries,
  OR `S` entries without real sparse patterns.
* Both hints can appear together.

Cases:

1. Mixed: a sparse worktree (real patterns) plus a hand-set assume-unchanged, modified
   file -> BOTH hints.
2. Legacy: shared-config `core.sparseCheckout=true` in the MAIN clone's config (no
   `extensions.worktreeConfig`); the PR worktree has NO sparse patterns but a hand-set
   skip-worktree file -> "clear the flag" only, not the sparse hint.
3. Controls: pure sparse -> sparse hint only; plain skip-worktree in a non-sparse repo ->
   "clear the flag" only.

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

SPARSE_HINT = "git sparse-checkout disable"
CLEAR_HINT = "clear the flag"

needs_tools = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("jq") is None,
    reason="needs git and jq (the stub gh pipes --jq through jq)",
)


def _out(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


def _ls_v(sb: Sandbox) -> list[str]:
    return base._git(sb.worktree, "ls-files", "-v").splitlines()


def _sparse_list(wt: Path) -> str:
    r = subprocess.run(["git", "sparse-checkout", "list"], cwd=wt, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _assert_refused(sb: Sandbox, r: subprocess.CompletedProcess[str]) -> str:
    out = _out(r)
    assert r.returncode != 0, f"merged over a worktree with hidden files:\n{out}"
    assert not sb.merge_calls(), sb.calls()
    assert "NOT MERGING" in out, out
    assert sb.worktree.is_dir(), "worktree removed although it held hidden files"
    return out


def _make_sparse(sb: Sandbox) -> None:
    w = sb.worktree
    base._git(w, "sparse-checkout", "set", "--no-cone", "/shared.txt")
    assert base._git(w, "config", "--bool", "core.sparseCheckout") == "true", "setup"
    assert _sparse_list(w), "setup: worktree should have sparse patterns"
    assert not (w / "feature.txt").exists(), "setup: feature.txt should be outside"
    assert "S feature.txt" in _ls_v(sb), f"setup: {_ls_v(sb)!r}"


# --------------------------------------------------------------------------- 1. mixed


@needs_tools
class TestMixedSparseAndAssumeUnchanged:
    def test_both_hints(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        w = sb.worktree
        _make_sparse(sb)
        base._git(w, "update-index", "--assume-unchanged", "shared.txt")
        (w / "shared.txt").write_text("line one\nan edit assume-unchanged hides\n")
        assert "h shared.txt" in _ls_v(sb), f"setup: {_ls_v(sb)!r}"
        ls_before = _ls_v(sb)

        r = sb.run(GREEN, sb.approve())
        out = _assert_refused(sb, r)

        assert SPARSE_HINT in out, f"no sparse hint for the S entries:\n{out}"
        assert CLEAR_HINT in out, f"no clear-the-flag hint for the assume-unchanged entry:\n{out}"
        assert "shared.txt" in out and "feature.txt" in out, out
        assert _ls_v(sb) == ls_before, "index flags changed"


# --------------------------------------------------------------------------- 2. legacy


@needs_tools
class TestLegacySharedSparseConfig:
    def test_no_patterns_gets_clear_the_flag_not_sparse(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        w = sb.worktree
        # Legacy: set in the main clone's shared config, seen by every worktree.
        base._git(sb.checkout, "config", "core.sparseCheckout", "true")
        r_ext = subprocess.run(
            ["git", "config", "--bool", "extensions.worktreeConfig"],
            cwd=sb.checkout,
            capture_output=True,
            text=True,
        )
        assert r_ext.stdout.strip() != "true", "setup: no extensions.worktreeConfig"
        assert base._git(w, "config", "--bool", "core.sparseCheckout") == "true", "setup"
        assert not _sparse_list(w), f"setup: worktree has patterns: {_sparse_list(w)!r}"
        # With sparse on, git drops skip-worktree from files still present, so the
        # hand-set flag hides a deletion (absent file) — it survives `git status`.
        base._git(w, "update-index", "--skip-worktree", "feature.txt")
        (w / "feature.txt").unlink()
        base._git(w, "status", "--porcelain")
        assert "S feature.txt" in _ls_v(sb), f"setup: {_ls_v(sb)!r}"

        r = sb.run(GREEN, sb.approve())
        out = _assert_refused(sb, r)

        assert CLEAR_HINT in out, f"no clear-the-flag hint:\n{out}"
        assert SPARSE_HINT not in out, f"sparse hint without sparse patterns:\n{out}"
        assert "skip-worktree" in out and "feature.txt" in out, out
        assert "S feature.txt" in _ls_v(sb), "flag was cleared"


# --------------------------------------------------------------------------- 3. controls


@needs_tools
class TestControls:
    def test_pure_sparse_gets_sparse_hint_only(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        _make_sparse(sb)

        r = sb.run(GREEN, sb.approve())
        out = _assert_refused(sb, r)

        assert SPARSE_HINT in out, f"no sparse hint:\n{out}"
        assert CLEAR_HINT not in out, f"clear-the-flag hint on pure sparse:\n{out}"

    def test_plain_skip_worktree_gets_clear_the_flag_only(self, tmp_path: Path) -> None:
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

        assert CLEAR_HINT in out, f"no clear-the-flag hint:\n{out}"
        assert SPARSE_HINT not in out, f"sparse hint in a non-sparse repo:\n{out}"
