"""The pairit rebase helper (#143) must rebase parallel PRs without mangling them.

Two failures seen in the loop (PRs #136 and #138):

1. A rebase that stops on a conflict and then continues reopens the commit message
   with `#` as the comment character. `#77: tests (red)` loses its subject line.
2. When two PRs each append a class to the END of the same test file, git lines up
   the lines the classes share (`@staticmethod` / `def _run`). The conflict then
   splits a class in half, and "keep both sides" gives broken Python.

The fix is `.claude/skills/pairit/rebase_append.sh`. Run with no arguments from a
feature checkout, it rebases the branch onto `origin/main`. It resolves append-only
conflicts as upstream plus the commit's appended text, and puts CHANGELOG entries
above `## [2.2.0]`. It stops (exits non-zero) on any conflict it can't safely
resolve. These tests check that behavior on a throwaway repo with a bare "origin".
Nothing here touches a real remote.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAIRIT_DIR = REPO_ROOT / ".claude" / "skills" / "pairit"
HELPER = PAIRIT_DIR / "rebase_append.sh"
SKILL_MD = PAIRIT_DIR / "SKILL.md"

TESTS_SUBJECT = "#77: tests (red)"
FIX_SUBJECT = "fix: widget handles empty input (#77)"

BASE_TEST_X = """import subprocess


class TestAlpha:
    def test_alpha(self):
        value = 1
        assert value == 1


class TestBeta:
    def test_beta(self):
        assert "beta"
"""

# Main's class and the branch's class share lines (`@staticmethod`, `def _run`,
# its body, blank lines). git lines those up, so the conflict lands mid-class.
MAIN_APPEND = """

class TestMainFeature:
    @staticmethod
    def _run(args):
        return subprocess.run(args, capture_output=True, text=True)

    def test_main_feature(self):
        result = self._run(["true"])
        assert result.returncode == 0
"""

BRANCH_APPEND = """

class TestIssue77:
    @staticmethod
    def _run(args):
        return subprocess.run(args, capture_output=True, text=True)

    def test_issue_77_empty_input(self):
        result = self._run(["false"])
        assert result.returncode == 1
"""

BASE_CHANGELOG = """# Changelog

## [Unreleased]

### Fixed

- Existing entry one (#10)
- Existing entry two (#11)

## [2.2.0] - 2026-08-30

### Added

- Something old (#5)
"""

MAIN_ENTRY = "- Main-side fix for board sorting (#76)"
BRANCH_ENTRY = "- Widget handles empty input (#77)"

CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>", "|||||||")


# --------------------------------------------------------------------------- helpers


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            # Non-interactive: any editor git opens accepts the message as-is. This
            # is how the `#` subject gets silently stripped in real sessions.
            "GIT_EDITOR": "true",
            "GIT_SEQUENCE_EDITOR": "true",
            "GIT_TERMINAL_PROMPT": "0",
            # Isolate from the user's global/system git config.
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return env


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_env(), capture_output=True, text=True, check=check
    )


def _commit_all(cwd: Path, message: str) -> None:
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", message)


def _changelog_with(entry: str) -> str:
    """Base CHANGELOG with `entry` added at the end of the Unreleased/Fixed list."""
    anchor = "- Existing entry two (#11)\n"
    assert anchor in BASE_CHANGELOG
    return BASE_CHANGELOG.replace(anchor, anchor + entry + "\n", 1)


def _build(tmp_path: Path, *, unsafe: bool = False) -> dict[str, object]:
    """Build origin (bare), a main working copy, and a feature checkout.

    With `unsafe=True`, both sides also MODIFY the same existing line in the middle
    of tests/test_x.py. No append-only resolution applies to that.
    """
    origin = tmp_path / "origin.git"
    main_wc = tmp_path / "main_wc"
    feature = tmp_path / "feature"

    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(main_wc))
    _git(main_wc, "remote", "add", "origin", str(origin))

    (main_wc / "tests").mkdir()
    (main_wc / "src").mkdir()
    (main_wc / "tests" / "test_x.py").write_text(BASE_TEST_X)
    (main_wc / "CHANGELOG.md").write_text(BASE_CHANGELOG)
    (main_wc / "src" / "widget.py").write_text("def widget(x):\n    return x\n")
    _commit_all(main_wc, "chore: initial")
    _git(main_wc, "push", "-q", "-u", "origin", "main")

    # Feature checkout branches off the base, before main moves.
    _git(tmp_path, "clone", "-q", str(origin), str(feature))
    _git(feature, "checkout", "-q", "-b", "fix/77-widget")

    branch_test_x = BASE_TEST_X + BRANCH_APPEND
    if unsafe:
        branch_test_x = branch_test_x.replace("        value = 1\n", "        value = 2 - 1\n", 1)
    (feature / "tests" / "test_x.py").write_text(branch_test_x)
    _commit_all(
        feature,
        TESTS_SUBJECT + "\n\nCo-Authored-By: Test Partner <noreply@example.invalid>",
    )

    (feature / "src" / "widget.py").write_text(
        "def widget(x):\n    if not x:\n        return None\n    return x\n"
    )
    (feature / "CHANGELOG.md").write_text(_changelog_with(BRANCH_ENTRY))
    _commit_all(
        feature,
        FIX_SUBJECT + "\n\nCo-Authored-By: Driver <noreply@example.invalid>",
    )
    branch_tip = _git(feature, "rev-parse", "HEAD").stdout.strip()

    # Meanwhile main gets a different class appended and a different CHANGELOG entry.
    main_test_x = BASE_TEST_X + MAIN_APPEND
    if unsafe:
        main_test_x = main_test_x.replace("        value = 1\n", "        value = 3 - 2\n", 1)
    (main_wc / "tests" / "test_x.py").write_text(main_test_x)
    (main_wc / "CHANGELOG.md").write_text(_changelog_with(MAIN_ENTRY))
    _commit_all(main_wc, "fix: board sorting (#76)")
    _git(main_wc, "push", "-q", "origin", "main")

    # Make origin/main current in the feature checkout. The helper may also fetch.
    _git(feature, "fetch", "-q", "origin")

    return {
        "origin": origin,
        "main_wc": main_wc,
        "feature": feature,
        "branch_tip": branch_tip,
        "main_test_x": main_test_x,
        "branch_test_x": branch_test_x,
    }


def _run_helper(cwd: Path) -> subprocess.CompletedProcess[str]:
    assert HELPER.is_file(), f"rebase helper missing: {HELPER.relative_to(REPO_ROOT)}"
    return subprocess.run(
        ["bash", str(HELPER)],
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _rebase_in_progress(repo: Path) -> bool:
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _subjects(repo: Path, rev_range: str) -> list[str]:
    out = _git(repo, "log", "--reverse", "--format=%s", rev_range).stdout
    return [line for line in out.splitlines()]


@pytest.fixture
def rebased(tmp_path: Path) -> dict[str, object]:
    """Build the repo and run the helper. A missing helper is reported by `_ok`
    inside the test (a FAIL, not a fixture ERROR)."""
    repo = _build(tmp_path)
    repo["result"] = _run_helper(repo["feature"]) if HELPER.is_file() else None  # type: ignore[arg-type]
    return repo


def _ok(rebased: dict[str, object]) -> Path:
    """Assert the helper exists and succeeded; return the feature checkout."""
    assert HELPER.is_file(), f"rebase helper missing: {HELPER.relative_to(REPO_ROOT)}"
    result: subprocess.CompletedProcess[str] = rebased["result"]  # type: ignore[assignment]
    assert result.returncode == 0, (
        f"helper failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return rebased["feature"]  # type: ignore[return-value]


# --------------------------------------------------------------------------- append-only


class TestAppendOnlyRebase:
    def test_rebase_completes_onto_origin_main(self, rebased: dict[str, object]) -> None:
        feature = _ok(rebased)
        assert not _rebase_in_progress(feature), "rebase left in progress"
        assert _git(feature, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "fix/77-widget"
        is_ancestor = _git(
            feature, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False
        )
        assert is_ancestor.returncode == 0, "branch does not descend from origin/main"
        assert _git(feature, "status", "--porcelain").stdout.strip() == "", "dirty worktree"

    def test_commit_subjects_preserved_exactly(self, rebased: dict[str, object]) -> None:
        feature = _ok(rebased)
        assert _subjects(feature, "origin/main..HEAD") == [TESTS_SUBJECT, FIX_SUBJECT]

    def test_test_file_is_valid_python_with_both_classes_intact(
        self, rebased: dict[str, object]
    ) -> None:
        feature = _ok(rebased)
        text = (feature / "tests" / "test_x.py").read_text()

        compile(text, "tests/test_x.py", "exec")  # SyntaxError/IndentationError == mangled
        for marker in CONFLICT_MARKERS:
            assert marker not in text, f"conflict marker {marker!r} left in test file"

        # Main's version is kept byte-for-byte, and the branch's class follows it
        # unchanged.
        main_test_x: str = rebased["main_test_x"]  # type: ignore[assignment]
        assert text.startswith(main_test_x), "main's version of the file was altered"
        assert MAIN_APPEND in text, "main's class not intact"
        assert BRANCH_APPEND in text, "branch's class not intact"
        assert text.count("class TestMainFeature:") == 1
        assert text.count("class TestIssue77:") == 1

    def test_tests_commit_alone_is_valid_python(self, rebased: dict[str, object]) -> None:
        """The rewritten `#77: tests (red)` commit must hold a good file too, not just HEAD."""
        feature = _ok(rebased)
        first = _git(feature, "rev-list", "--reverse", "origin/main..HEAD").stdout.split()[0]
        text = _git(feature, "show", f"{first}:tests/test_x.py").stdout
        compile(text, f"{first}:tests/test_x.py", "exec")
        assert MAIN_APPEND in text and BRANCH_APPEND in text

    def test_changelog_has_both_entries_above_release(self, rebased: dict[str, object]) -> None:
        feature = _ok(rebased)
        text = (feature / "CHANGELOG.md").read_text()

        for marker in CONFLICT_MARKERS:
            assert marker not in text, f"conflict marker {marker!r} left in CHANGELOG.md"
        assert text.count(MAIN_ENTRY) == 1, "main's CHANGELOG entry missing/duplicated"
        assert text.count(BRANCH_ENTRY) == 1, "branch's CHANGELOG entry missing/duplicated"
        assert text.count("## [2.2.0] - 2026-08-30") == 1
        release = text.index("## [2.2.0] - 2026-08-30")
        unreleased = text.index("## [Unreleased]")
        for entry in (MAIN_ENTRY, BRANCH_ENTRY):
            pos = text.index(entry)
            assert unreleased < pos < release, f"{entry!r} not in the Unreleased section"
        # The older entries and released sections are untouched.
        assert "- Existing entry one (#10)" in text
        assert text[release:] == BASE_CHANGELOG[BASE_CHANGELOG.index("## [2.2.0]") :]

    def test_fix_commit_content_survives(self, rebased: dict[str, object]) -> None:
        feature = _ok(rebased)
        assert "if not x:" in (feature / "src" / "widget.py").read_text()


# --------------------------------------------------------------------------- unsafe


class TestUnsafeConflictStops:
    def test_mid_file_modification_conflict_is_not_auto_resolved(self, tmp_path: Path) -> None:
        repo = _build(tmp_path, unsafe=True)
        feature: Path = repo["feature"]  # type: ignore[assignment]
        branch_tip: str = repo["branch_tip"]  # type: ignore[assignment]

        result = _run_helper(feature)
        assert result.returncode != 0, (
            "helper claimed success on a conflict it cannot safely resolve:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        in_progress = _rebase_in_progress(feature)
        if not in_progress:
            # Aborted: the branch must be exactly where it was.
            head = _git(feature, "rev-parse", "HEAD").stdout.strip()
            assert head == branch_tip, "rebase neither left stopped nor cleanly aborted"

        # Whether stopped or aborted, no commit between origin/main and the branch
        # ref/HEAD may hold a mangled test file.
        for ref in ("HEAD", "refs/heads/fix/77-widget"):
            commits = _git(feature, "rev-list", f"origin/main..{ref}").stdout.split()
            for sha in commits:
                shown = _git(feature, "show", f"{sha}:tests/test_x.py", check=False)
                if shown.returncode != 0:
                    continue
                for marker in CONFLICT_MARKERS:
                    assert marker not in shown.stdout, f"{sha} committed conflict markers"
                compile(shown.stdout, f"{sha}:tests/test_x.py", "exec")

        # The stopped rebase did not quietly pick one side of the modified line.
        if in_progress:
            unmerged = _git(feature, "diff", "--name-only", "--diff-filter=U").stdout.split()
            assert "tests/test_x.py" in unmerged, "unsafe conflict was resolved and staged"

    def test_branch_ref_untouched_on_unsafe_conflict(self, tmp_path: Path) -> None:
        repo = _build(tmp_path, unsafe=True)
        feature: Path = repo["feature"]  # type: ignore[assignment]
        result = _run_helper(feature)
        assert result.returncode != 0
        tip = _git(feature, "rev-parse", "refs/heads/fix/77-widget").stdout.strip()
        assert tip == repo["branch_tip"], "branch ref was rewritten despite the unsafe conflict"


# --------------------------------------------------------------------------- docs


class TestSkillDocumentsHelper:
    def test_skill_md_mentions_helper(self) -> None:
        text = SKILL_MD.read_text()
        assert "rebase_append.sh" in text, "pairit SKILL.md does not document rebase_append.sh"

    def test_skill_md_mentions_comment_char(self) -> None:
        text = SKILL_MD.read_text()
        assert "core.commentChar" in text, (
            "pairit SKILL.md does not document rebasing with core.commentChar"
        )


# =========================================================================== round 2
#
# Review of PR #163 found shapes where the helper exits 0 (or commits junk) even
# though no rebase happened or the resolution was unsafe. In each case the helper
# must STOP: non-zero exit, rebase left stopped or aborted, nothing mangled
# committed, and the branch ref and its commits not lost. Case 3 may also resolve
# correctly.

Files = dict[str, "str | None"]  # path -> content; None deletes the path

BRANCH = "fix/88-thing"


def _write(wc: Path, files: Files) -> None:
    for rel, content in files.items():
        path = wc / rel
        if content is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


def _scenario(
    tmp_path: Path,
    base: Files,
    branch_commits: list[tuple[str, Files]],
    upstream_commits: list[tuple[str, Files]],
    *,
    branch: str = BRANCH,
) -> dict[str, object]:
    """origin (bare) + main working copy + feature checkout on `branch`.

    `branch_commits` go on `branch` in the feature checkout (`main` means commits on
    a local main). `upstream_commits` are pushed to origin/main afterwards, and the
    feature checkout fetches them.
    """
    origin = tmp_path / "origin.git"
    main_wc = tmp_path / "main_wc"
    feature = tmp_path / "feature"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(main_wc))
    _git(main_wc, "remote", "add", "origin", str(origin))
    _write(main_wc, base)
    _commit_all(main_wc, "chore: initial")
    _git(main_wc, "push", "-q", "-u", "origin", "main")

    _git(tmp_path, "clone", "-q", str(origin), str(feature))
    if branch != "main":
        _git(feature, "checkout", "-q", "-b", branch)
    for subject, files in branch_commits:
        _write(feature, files)
        _commit_all(feature, subject)
    tip = _git(feature, "rev-parse", "HEAD").stdout.strip()

    for subject, files in upstream_commits:
        _write(main_wc, files)
        _commit_all(main_wc, subject)
    _git(main_wc, "push", "-q", "origin", "main")
    _git(feature, "fetch", "-q", "origin")

    return {"feature": feature, "tip": tip, "branch": branch, "tmp": tmp_path}


def _tree_paths(repo: Path, rev: str) -> list[str]:
    out = _git(repo, "ls-tree", "-r", "-z", "--name-only", rev).stdout
    return [p for p in out.split("\0") if p]


def _assert_stopped(
    sc: dict[str, object],
    result: subprocess.CompletedProcess[str],
    check_commit: Callable[[str], None] | None = None,
) -> None:
    """Non-zero exit; rebase stopped or never applied; branch not lost; nothing bad committed."""
    feature: Path = sc["feature"]  # type: ignore[assignment]
    tip: str = sc["tip"]  # type: ignore[assignment]
    branch: str = sc["branch"]  # type: ignore[assignment]
    assert result.returncode != 0, (
        "helper exited 0 on a shape it must stop on:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    ref = _git(feature, "rev-parse", f"refs/heads/{branch}").stdout.strip()
    assert ref == tip, f"branch ref {branch} was rewritten ({tip[:8]} -> {ref[:8]})"
    if _rebase_in_progress(feature):
        if check_commit is not None:
            for sha in _git(feature, "rev-list", "origin/main..HEAD").stdout.split():
                check_commit(sha)
    else:
        head = _git(feature, "rev-parse", "HEAD").stdout.strip()
        assert head == tip, "rebase neither left stopped nor aborted back to the branch tip"


class TestRound2ModifyDelete:
    def test_upstream_deleted_file_branch_appended_to_stops(self, tmp_path: Path) -> None:
        base_t = "import os\n\n\nclass A:\n    pass\n"
        sc = _scenario(
            tmp_path,
            {"t.py": base_t, "README": "x\n"},
            [("#88: tests (red)", {"t.py": base_t + "\n\nclass B(os.PathLike):\n    pass\n"})],
            [("chore: drop t.py (#87)", {"t.py": None})],
        )
        result = _run_helper(sc["feature"])  # type: ignore[arg-type]

        def check(sha: str) -> None:
            shown = _git(sc["feature"], "show", f"{sha}:t.py", check=False)  # type: ignore[arg-type]
            if shown.returncode == 0:
                assert "import os" in shown.stdout, f"{sha} committed a t.py fragment"

        _assert_stopped(sc, result, check)


class TestRound2AddAdd:
    def test_both_sides_create_same_file_stops(self, tmp_path: Path) -> None:
        mine = "import json\n\n\ndef test_new():\n    assert json.loads('1') == 1\n"
        theirs = "import re\n\n\ndef test_new():\n    assert re.match('a', 'a')\n"
        sc = _scenario(
            tmp_path,
            {"README": "x\n"},
            [("#88: tests (red)", {"tests_new.py": mine})],
            [("test: new (#87)", {"tests_new.py": theirs})],
        )
        result = _run_helper(sc["feature"])  # type: ignore[arg-type]

        def check(sha: str) -> None:
            shown = _git(sc["feature"], "show", f"{sha}:tests_new.py", check=False)  # type: ignore[arg-type]
            if shown.returncode == 0:
                assert shown.stdout.count("def test_new") == 1, f"{sha} concatenated both files"

        _assert_stopped(sc, result, check)


JUNK_NAMES = {"my", "file.txt"}


@pytest.mark.parametrize("name", ["my file.txt", "café.txt"])
class TestRound2AwkwardFilenames:
    def test_append_conflict_on_awkward_name_never_creates_junk(
        self, tmp_path: Path, name: str
    ) -> None:
        base = "line one\n"
        sc = _scenario(
            tmp_path,
            {name: base, "README": "x\n"},
            [("#88: append (red)", {name: base + "branch line\n"})],
            [("chore: append (#87)", {name: base + "upstream line\n"})],
        )
        feature: Path = sc["feature"]  # type: ignore[assignment]
        result = _run_helper(feature)

        def is_junk(p: str) -> bool:
            return p in JUNK_NAMES or p.startswith('"') or "\\3" in p

        on_disk = [
            str(p.relative_to(feature))
            for p in feature.rglob("*")
            if ".git" not in p.relative_to(feature).parts
        ]
        index = [p for p in _git(feature, "ls-files", "-z").stdout.split("\0") if p]
        for where, paths in (
            ("worktree", on_disk),
            ("index", index),
            ("HEAD", _tree_paths(feature, "HEAD")),
        ):
            junk = [p for p in paths if is_junk(p)]
            assert not junk, f"junk paths in {where}: {junk!r}"

        if result.returncode == 0:
            assert not _rebase_in_progress(feature)
            ok = _git(feature, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False)
            assert ok.returncode == 0, "exit 0 but branch does not descend from origin/main"
            text = (feature / name).read_text()
            assert text.startswith(base + "upstream line\n"), f"upstream text altered: {text!r}"
            assert text.count("branch line\n") == 1 and text.rstrip("\n").endswith("branch line")
            for marker in CONFLICT_MARKERS:
                assert marker not in text
        else:
            _assert_stopped(sc, result)


class TestRound2NoRebaseHappened:
    def test_dirty_worktree_exits_nonzero(self, tmp_path: Path) -> None:
        sc = _scenario(
            tmp_path,
            {"notes.txt": "a\n", "README": "x\n"},
            [("#88: tests (red)", {"b.txt": "branch\n"})],
            [("chore: up (#87)", {"up.txt": "up\n"})],
        )
        feature: Path = sc["feature"]  # type: ignore[assignment]
        (feature / "notes.txt").write_text("a\nlocal unstaged edit\n")
        result = _run_helper(feature)
        _assert_stopped(sc, result)
        assert (feature / "notes.txt").read_text() == "a\nlocal unstaged edit\n", "dirty edit lost"

    def test_untracked_file_upstream_adds_exits_nonzero(self, tmp_path: Path) -> None:
        sc = _scenario(
            tmp_path,
            {"README": "x\n"},
            [("#88: tests (red)", {"b.txt": "branch\n"})],
            [("chore: add new.txt (#87)", {"new.txt": "upstream\n"})],
        )
        feature: Path = sc["feature"]  # type: ignore[assignment]
        (feature / "new.txt").write_text("my local untracked\n")
        result = _run_helper(feature)
        _assert_stopped(sc, result)
        assert (feature / "new.txt").read_text() == "my local untracked\n", "untracked file lost"


class TestRound2RefusesMain:
    def test_run_on_main_exits_nonzero_and_rewrites_nothing(self, tmp_path: Path) -> None:
        sc = _scenario(
            tmp_path,
            {"README": "x\n"},
            [("local: main commit", {"local.txt": "local\n"})],
            [("chore: up (#87)", {"up.txt": "up\n"})],
            branch="main",
        )
        result = _run_helper(sc["feature"])  # type: ignore[arg-type]
        _assert_stopped(sc, result)


class TestRound2FetchFailure:
    def test_fetch_failure_exits_nonzero(self, tmp_path: Path) -> None:
        sc = _scenario(
            tmp_path,
            {"README": "x\n"},
            [("#88: tests (red)", {"b.txt": "branch\n"})],
            [("chore: up (#87)", {"up.txt": "up\n"})],
        )
        feature: Path = sc["feature"]  # type: ignore[assignment]
        _git(feature, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
        result = _run_helper(feature)
        _assert_stopped(sc, result)


class TestRound2ChangelogEdit:
    def test_changelog_line_edit_is_not_auto_resolved(self, tmp_path: Path) -> None:
        old = "- Existing entry two (#11)"
        new = "- Existing entry two, reworded (#11)"
        sc = _scenario(
            tmp_path,
            {"CHANGELOG.md": BASE_CHANGELOG},
            [("docs: reword #11 entry (#88)", {"CHANGELOG.md": BASE_CHANGELOG.replace(old, new)})],
            [("fix: board sorting (#76)", {"CHANGELOG.md": _changelog_with(MAIN_ENTRY)})],
        )
        result = _run_helper(sc["feature"])  # type: ignore[arg-type]

        def check(sha: str) -> None:
            text = _git(sc["feature"], "show", f"{sha}:CHANGELOG.md").stdout  # type: ignore[arg-type]
            for marker in CONFLICT_MARKERS:
                assert marker not in text, f"{sha} committed conflict markers"
            assert not (old + "\n" in text and new in text), f"{sha} kept both old and edited line"

        _assert_stopped(sc, result, check)
