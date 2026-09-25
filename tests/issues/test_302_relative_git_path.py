"""Issue #302 — pin safe_merge.sh's relative `--git-path` arm (from the review of PR #301).

`git rev-parse --git-path info/sparse-checkout` is ABSOLUTE in a linked worktree but
RELATIVE (`.git/info/sparse-checkout`) in the primary checkout. The script prefixes
`$wt/` onto a relative result; without that, `[ -s ... ]` would test the path against
the script's own cwd and miss the sparse-checkout file.

Sandbox variant: the PR branch is checked out in the PRIMARY checkout, as a root-only
cone sparse checkout (#288: `sparse-checkout list` prints nothing, only the file tells)
with a skip-worktree file in a subdirectory. The script runs from a linked worktree on
main, so its cwd is not `$wt`. Decided behaviour: the merge is refused with the sparse
hint (`git sparse-checkout disable`), not "clear the flag", and nothing is removed.

Reuses the #167 harness and the #288 helpers.
"""

from __future__ import annotations

from pathlib import Path

from tests import test_pairit_safe_merge as base
from tests.issues import test_288_sparse_detection as t288


@t288.needs_tools
class TestPrimaryCheckoutRelativeGitPath:
    def test_root_only_cone_in_primary_checkout_gets_sparse_hint(self, tmp_path: Path) -> None:
        sb = base.Sandbox(tmp_path, conflict=False)
        primary = sb.checkout
        runner = tmp_path / "runner-main"
        # move the PR branch into the primary checkout; run the script from main elsewhere
        base._git(primary, "worktree", "remove", "--force", str(sb.worktree))
        base._git(primary, "checkout", "-q", base.BRANCH)
        base._git(primary, "worktree", "add", "-q", str(runner), "main")
        sb.worktree = primary
        sb.checkout = runner

        t288._add_subdir_file(sb, "dir/notes.txt")
        base._git(primary, "sparse-checkout", "set", "--cone")

        rel = base._git(primary, "rev-parse", "--git-path", "info/sparse-checkout")
        assert not Path(rel).is_absolute(), f"setup: --git-path should be relative: {rel!r}"
        assert not (runner / rel).is_file(), f"setup: {rel!r} must not resolve from the cwd"
        wts = base._git(runner, "worktree", "list", "--porcelain")
        assert f"worktree {primary}\nHEAD" in wts, f"setup: {wts!r}"
        assert f"branch refs/heads/{base.BRANCH}" in wts, f"setup: {wts!r}"
        assert base._git(primary, "config", "--bool", "core.sparseCheckout") == "true"
        assert not t288._sparse_list(primary), "setup: list should be empty (root-only cone)"
        lines = t288._sparse_file(primary).read_text().split()
        assert "/*" in lines and "!/*/" in lines, f"setup: sparse file {lines!r}"
        assert not (primary / "dir" / "notes.txt").exists(), "setup: dir/ should be outside"
        assert "S dir/notes.txt" in t288._ls_v(sb), f"setup: {t288._ls_v(sb)!r}"
        ls_before = t288._ls_v(sb)

        r = sb.run(t288.GREEN, sb.approve())
        out = t288._assert_refused(sb, r)

        assert t288.SPARSE_HINT in out, f"relative --git-path not resolved against $wt:\n{out}"
        assert t288.CLEAR_HINT not in out, f"clear-the-flag hint on a pure sparse cone:\n{out}"
        assert "dir/notes.txt" in out, out
        assert base._git(primary, "rev-parse", "--abbrev-ref", "HEAD") == base.BRANCH
        assert t288._ls_v(sb) == ls_before, "index flags changed"
