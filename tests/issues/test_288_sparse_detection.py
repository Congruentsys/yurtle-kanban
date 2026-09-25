"""Issue #288 — safe_merge.sh sparse detection follow-ups from the review of PR #286 (#276).

The merge is refused in every case; only the hint changes.

Decided behaviour:

1. A cone-mode sparse checkout that keeps only root files (`git sparse-checkout set
   --cone` with no directories) IS a real sparse checkout, although `git sparse-checkout
   list` prints nothing (the sparse-checkout file holds `/*` and `!/*/`). Its
   skip-worktree files get the sparse hint (`git sparse-checkout disable`), not
   "clear the flag".
2. The assume-unchanged hint fires only on a real assume-unchanged entry: a pure-sparse
   worktree whose skip-worktree file PATH contains the text "assume-unchanged" gets the
   sparse hint only.

Controls: the #265 / #276 cases stay as they are (see those files).

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


def _sparse_file(wt: Path) -> Path:
    rel = base._git(wt, "rev-parse", "--git-path", "info/sparse-checkout")
    p = Path(rel)
    return p if p.is_absolute() else wt / p


def _add_subdir_file(sb: Sandbox, rel: str) -> None:
    """Commit a tracked file in a subdirectory on the PR branch (so a cone can omit it)."""
    w = sb.worktree
    target = w / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("a file in a subdirectory\n")
    base._git(w, "add", rel)
    base._git(w, "commit", "-q", "-m", f"add {rel}")
    base._git(w, "push", "-q", "origin", base.BRANCH)
    sb.head_sha = base._git(w, "rev-parse", "HEAD")


def _assert_refused(sb: Sandbox, r: subprocess.CompletedProcess[str]) -> str:
    out = _out(r)
    assert r.returncode != 0, f"merged over a worktree with hidden files:\n{out}"
    assert not sb.merge_calls(), sb.calls()
    assert "NOT MERGING" in out, out
    assert sb.worktree.is_dir(), "worktree removed although it held hidden files"
    return out


# --------------------------------------------------------------- 1. cone, root files only


@needs_tools
class TestConeRootOnly:
    def test_root_only_cone_gets_sparse_hint(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        w = sb.worktree
        _add_subdir_file(sb, "dir/notes.txt")
        base._git(w, "sparse-checkout", "set", "--cone")
        assert base._git(w, "config", "--bool", "core.sparseCheckout") == "true", "setup"
        assert not _sparse_list(w), f"setup: list should be empty: {_sparse_list(w)!r}"
        lines = _sparse_file(w).read_text().split()
        assert "/*" in lines and "!/*/" in lines, f"setup: sparse file {lines!r}"
        assert not (w / "dir" / "notes.txt").exists(), "setup: dir/ should be outside"
        assert "S dir/notes.txt" in _ls_v(sb), f"setup: {_ls_v(sb)!r}"
        ls_before = _ls_v(sb)

        r = sb.run(GREEN, sb.approve())
        out = _assert_refused(sb, r)

        assert SPARSE_HINT in out, f"no sparse hint for a root-only cone:\n{out}"
        assert CLEAR_HINT not in out, f"clear-the-flag hint on a pure sparse cone:\n{out}"
        assert "dir/notes.txt" in out, out
        assert _ls_v(sb) == ls_before, "index flags changed"


# ------------------------------------------------ 2. "assume-unchanged" inside a path name


@needs_tools
class TestAssumeUnchangedInPath:
    def test_path_text_does_not_trigger_clear_hint(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        w = sb.worktree
        rel = "dir/assume-unchanged-notes.txt"
        _add_subdir_file(sb, rel)
        # real patterns (non-empty list) so only the path-text match is under test
        base._git(w, "sparse-checkout", "set", "--no-cone", "/shared.txt", "/feature.txt")
        assert base._git(w, "config", "--bool", "core.sparseCheckout") == "true", "setup"
        assert _sparse_list(w), "setup: worktree should have sparse patterns"
        assert not (w / rel).exists(), "setup: the file should be outside the patterns"
        ls = _ls_v(sb)
        assert f"S {rel}" in ls, f"setup: {ls!r}"
        assert not any(line[:1].islower() for line in ls), f"setup: no real a-u: {ls!r}"
        ls_before = _ls_v(sb)

        r = sb.run(GREEN, sb.approve())
        out = _assert_refused(sb, r)

        assert SPARSE_HINT in out, f"no sparse hint:\n{out}"
        assert CLEAR_HINT not in out, f"clear-the-flag hint from a path name:\n{out}"
        assert rel in out, out
        assert _ls_v(sb) == ls_before, "index flags changed"
