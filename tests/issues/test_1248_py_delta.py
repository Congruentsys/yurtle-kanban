"""Issue #1248 — `scripts/py_delta.py`: which pytest files can a change affect? (fail-closed)

Captain's decisions (2026-10-03): rewrite an equivalent stdlib-only `py_delta.py` HERE from the
documented contract (rachael-lab `docs/py-delta.md`), no private code copied; the subset feeds a
LOCAL fast check only — GitHub CI keeps the full suite on every Python.

These tests are the spec. They pin:

- the CLI: `py_delta.py --config <file> <base> <tip>`, cwd = the repo root;
- the diff: `git diff --no-renames -z --name-only <base> <tip>` (a rename names both sides, a
  non-ASCII path is never quoted);
- the config: `#` comments and blank lines ignored; `glob -> test-glob...`, `glob -> FULL`,
  `glob -> NONE`; the first matching rule wins; `fnmatch.fnmatchcase`, so `*` crosses `/`; test
  globs expand against the TIP tree; anything else on a line is malformed;
- every fail-closed rule (no rule matched, the script or config in use changed — by EQUALS or a
  proper ANCESTOR, e.g. a gitlink — the always-full build/pytest files, a tracked symlink in the
  tip, a test glob matching nothing, control characters / whitespace, bad refs, bad config, an
  empty diff);
- the output contract: the LAST stdout line is exactly `py-delta: subset <sorted unique files>`
  (rc 0), `py-delta: full <why>` (rc 1) or `py-delta: cannot-assess <why>` (rc 2), every `why`
  with its control characters escaped;
- this repo's wiring: `scripts/py-delta.conf` routes on the real tree, and `scripts/check_delta.sh
  [BASE]` (default `origin/main`) prints `check-delta: SUBSET <files>` or `check-delta: FULL
  (<verdict>)`; with `CHECK_DELTA_DRY_RUN=1` it prints the decision and exits 0 without pytest.

The fixture repos are built with git plumbing (`update-index -z --index-info`), so a path may hold
any byte the contract cares about without touching the filesystem.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "py_delta.py"
CONF = ROOT / "scripts" / "py-delta.conf"
GATE = ROOT / "scripts" / "check_delta.sh"
DRY_RUN_ENV = "CHECK_DELTA_DRY_RUN"

SUBSET_RE = re.compile(r"^py-delta: subset( \S+)*$")
FULL_RE = re.compile(r"^py-delta: full \S")
CANNOT_RE = re.compile(r"^py-delta: cannot-assess \S")


def _has_control(text: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in text)


@dataclass
class Result:
    rc: int
    stdout: str
    stderr: str

    @property
    def last(self) -> str:
        lines = self.stdout.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        assert lines, f"no stdout at all (rc={self.rc}); stderr:\n{self.stderr}"
        return lines[-1]

    def subset(self, *files: str) -> None:
        want = "py-delta: subset" + "".join(" " + f for f in files)
        assert (self.rc, self.last) == (0, want), self._show()

    def full(self) -> None:
        assert self.rc == 1 and FULL_RE.match(self.last), self._show()

    def cannot(self) -> None:
        assert self.rc == 2 and CANNOT_RE.match(self.last), self._show()

    def _show(self) -> str:
        return f"rc={self.rc}\nstdout:\n{self.stdout}\nstderr:\n{self.stderr}"


def _script_bytes() -> bytes:
    assert SCRIPT.is_file(), "scripts/py_delta.py does not exist"
    return SCRIPT.read_bytes()


class Repo:
    """A scratch git repo whose index is edited with plumbing; `commit()` snapshots it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "diff.renames", "true")  # what --no-renames must override
        self.git("config", "core.quotePath", "true")  # what -z must override
        self.n = 0

    def git(self, *args: str, input: bytes | None = None) -> bytes:
        proc = subprocess.run(
            ["git", *args], cwd=self.root, input=input, capture_output=True, check=True
        )
        return proc.stdout

    def _index(self, mode: str, sha: str, path: str) -> None:
        self.git(
            "update-index", "-z", "--index-info",
            input=f"{mode} {sha}\t{path}".encode() + b"\0",
        )

    def put(self, path: str, data: bytes | str = b"x\n", *, disk: bool = False) -> None:
        if isinstance(data, str):
            data = data.encode()
        sha = self.git("hash-object", "-w", "--stdin", input=data).decode().strip()
        self._index("100644", sha, path)
        if disk:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def link(self, path: str, target: str) -> None:
        sha = self.git("hash-object", "-w", "--stdin", input=target.encode()).decode().strip()
        self._index("120000", sha, path)

    def gitlink(self, path: str, sha: str) -> None:
        self._index("160000", sha, path)

    def drop(self, path: str) -> None:
        self.git("update-index", "-z", "--force-remove", "--stdin", input=path.encode() + b"\0")

    def config(self, text: str, path: str = "ci/delta.conf") -> None:
        self.put(path, text, disk=True)

    def commit(self) -> str:
        self.n += 1
        self.git("commit", "-q", "--allow-empty", "-m", f"c{self.n}")
        return self.git("rev-parse", "HEAD").decode().strip()

    def run(
        self,
        base: str,
        tip: str,
        *,
        config: str = "ci/delta.conf",
        script: str | os.PathLike[str] = "scripts/py_delta.py",
        cwd: Path | None = None,
    ) -> Result:
        proc = subprocess.run(
            [sys.executable, str(script), "--config", config, base, tip],
            cwd=cwd or self.root, capture_output=True,
        )
        return Result(
            proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """A repo tracking the script under test at `scripts/py_delta.py` (on disk and in the tree)."""
    r = Repo(tmp_path / "repo")
    r.put("scripts/py_delta.py", _script_bytes(), disk=True)
    r.put("tests/test_a.py", "def test_a(): pass\n")
    r.put("tests/test_b.py", "def test_b(): pass\n")
    r.put("tests/test_docs.py", "def test_docs(): pass\n")
    return r


def _change(repo: Repo, conf: str, before: dict[str, str], after: dict[str, str | None]) -> Result:
    """Commit `conf` + `before` as base, then `after` (None = delete) as tip; run base..tip."""
    repo.config(conf)
    for p, d in before.items():
        repo.put(p, d)
    base = repo.commit()
    for p, d in after.items():
        if d is None:
            repo.drop(p)
        else:
            repo.put(p, d)
    tip = repo.commit()
    return repo.run(base, tip)


# --- the script itself ------------------------------------------------------------------------


def test_script_exists() -> None:
    assert SCRIPT.is_file(), "scripts/py_delta.py does not exist"


def test_script_is_generic_stdlib_only() -> None:
    text = _script_bytes().decode("utf-8")
    low = text.lower()
    for word in ("yurtle", "changelog.d", "tests/issues", "py-delta.conf", "rachael"):
        assert word not in low, f"py_delta.py names a repo-specific path/word: {word!r}"
    for node in ast.walk(ast.parse(text)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for name in names:
            top = name.split(".")[0]
            assert top in sys.stdlib_module_names or top == "__future__", (
                f"py_delta.py imports a non-stdlib module: {name}"
            )


# --- routing and the config format ------------------------------------------------------------


def test_route_to_tests(repo: Repo) -> None:
    _change(repo, "docs/* -> tests/test_docs.py\n", {"docs/a.md": "1"}, {"docs/a.md": "2"}).subset(
        "tests/test_docs.py"
    )


def test_none_gives_bare_subset(repo: Repo) -> None:
    res = _change(repo, "docs/* -> NONE\n", {"docs/a.md": "1"}, {"docs/a.md": "2"})
    res.subset()
    assert res.last == "py-delta: subset"


def test_full_route(repo: Repo) -> None:
    _change(repo, "docs/* -> FULL\n", {"docs/a.md": "1"}, {"docs/a.md": "2"}).full()


def test_unmatched_path_is_full(repo: Repo) -> None:
    _change(
        repo, "docs/* -> NONE\n", {"docs/a.md": "1", "src/x.py": "1"},
        {"docs/a.md": "2", "src/x.py": "2"},
    ).full()


def test_comments_and_blank_lines_ignored(repo: Repo) -> None:
    conf = "# routing for the fixture -> FULL\n\n#docs/* -> FULL\n\ndocs/* -> NONE\n\n# end\n"
    _change(repo, conf, {"docs/a.md": "1"}, {"docs/a.md": "2"}).subset()


def test_first_match_wins(repo: Repo) -> None:
    conf = "docs/special.md -> tests/test_a.py\ndocs/* -> FULL\n"
    _change(repo, conf, {"docs/special.md": "1"}, {"docs/special.md": "2"}).subset("tests/test_a.py")


def test_first_match_wins_the_other_way(repo: Repo) -> None:
    conf = "docs/* -> FULL\ndocs/special.md -> tests/test_a.py\n"
    _change(repo, conf, {"docs/special.md": "1"}, {"docs/special.md": "2"}).full()


def test_star_crosses_slash(repo: Repo) -> None:
    _change(repo, "docs/* -> NONE\n", {"docs/a/b/c.md": "1"}, {"docs/a/b/c.md": "2"}).subset()


def test_match_is_case_sensitive(repo: Repo) -> None:
    _change(repo, "docs/* -> NONE\n", {"Docs/a.md": "1"}, {"Docs/a.md": "2"}).full()


def test_several_test_globs_union_sorted_unique(repo: Repo) -> None:
    conf = (
        "docs/* -> tests/test_docs.py tests/test_b.py\n"
        "src/* -> tests/test_b.py tests/test_a.py\n"
        "notes/* -> NONE\n"
    )
    _change(
        repo, conf, {"docs/a.md": "1", "src/x.py": "1", "notes/n.md": "1"},
        {"docs/a.md": "2", "src/x.py": "2", "notes/n.md": "2"},
    ).subset("tests/test_a.py", "tests/test_b.py", "tests/test_docs.py")


def test_test_glob_star_crosses_slash(repo: Repo) -> None:
    repo.put("tests/sub/deep/check_b.py", "def test(): pass\n")
    _change(repo, "src/* -> tests/*check_b.py\n", {"src/x.py": "1"}, {"src/x.py": "2"}).subset(
        "tests/sub/deep/check_b.py"
    )


def test_test_glob_expands_against_tip(repo: Repo) -> None:
    """A test added by the change itself is selected."""
    _change(
        repo, "src/* -> tests/test_new*.py\ntests/* -> NONE\n", {"src/x.py": "1"},
        {"src/x.py": "2", "tests/test_new_x.py": "def test(): pass\n"},
    ).subset("tests/test_new_x.py")


def test_test_glob_ignores_working_tree(repo: Repo) -> None:
    """Only the tip tree counts: an untracked file on disk does not satisfy a test glob."""
    (repo.root / "tests").mkdir(exist_ok=True)
    (repo.root / "tests" / "test_disk_only.py").write_text("def test(): pass\n")
    _change(repo, "src/* -> tests/test_disk_only.py\n", {"src/x.py": "1"}, {"src/x.py": "2"}).full()


def test_test_glob_matching_nothing_is_full(repo: Repo) -> None:
    _change(repo, "docs/* -> tests/test_missing*.py\n", {"docs/a.md": "1"}, {"docs/a.md": "2"}).full()


def test_one_dead_glob_among_live_ones_is_full(repo: Repo) -> None:
    conf = "docs/* -> tests/test_docs.py tests/test_gone.py\n"
    _change(repo, conf, {"docs/a.md": "1"}, {"docs/a.md": "2"}).full()


def test_rename_names_both_sides(repo: Repo) -> None:
    body = "def f():\n    return 42\n" * 20
    conf = "old/* -> tests/test_a.py\nnew/* -> tests/test_b.py\n"
    _change(repo, conf, {"old/mod.py": body}, {"old/mod.py": None, "new/mod.py": body}).subset(
        "tests/test_a.py", "tests/test_b.py"
    )


def test_deleted_path_is_routed(repo: Repo) -> None:
    _change(repo, "docs/* -> FULL\nsrc/* -> NONE\n", {"docs/a.md": "1"}, {"docs/a.md": None}).full()


def test_non_ascii_changed_path_unquoted(repo: Repo) -> None:
    conf = "docs/café.md -> tests/test_docs.py\n"
    _change(repo, conf, {"docs/café.md": "1"}, {"docs/café.md": "2"}).subset("tests/test_docs.py")


def test_non_ascii_test_path_selected(repo: Repo) -> None:
    repo.put("tests/test_café.py", "def test(): pass\n")
    _change(repo, "docs/* -> tests/test_caf*.py\n", {"docs/a.md": "1"}, {"docs/a.md": "2"}).subset(
        "tests/test_café.py"
    )


def test_space_in_changed_path_is_fine(repo: Repo) -> None:
    _change(
        repo, "docs/* -> tests/test_docs.py\n", {"docs/my notes.md": "1"}, {"docs/my notes.md": "2"}
    ).subset("tests/test_docs.py")


def test_space_in_unselected_test_path_is_fine(repo: Repo) -> None:
    repo.put("tests/other dir/test_x.py", "def test(): pass\n")
    _change(repo, "docs/* -> tests/test_a.py\n", {"docs/a.md": "1"}, {"docs/a.md": "2"}).subset(
        "tests/test_a.py"
    )


# --- malformed / missing config, bad refs, empty diff: cannot-assess --------------------------


@pytest.mark.parametrize(
    "bad_line",
    [
        "docs/* -> tests/test_a.py -> tests/test_b.py",  # a second ->
        " -> tests/test_a.py",  # no glob
        "docs/* ->",  # no target
        "docs/* -> FULL tests/test_a.py",  # FULL beside other targets
        "docs/* -> NONE tests/test_a.py",  # NONE beside other targets
        "docs/* -> NONE FULL",
        "docs/*",  # no arrow at all
    ],
)
def test_malformed_line_is_cannot_assess(repo: Repo, bad_line: str) -> None:
    """Malformed anywhere — even after the rule that matches — is cannot-assess."""
    conf = f"docs/* -> NONE\n{bad_line}\n"
    _change(repo, conf, {"docs/a.md": "1"}, {"docs/a.md": "2"}).cannot()


def test_missing_config(repo: Repo) -> None:
    repo.config("docs/* -> NONE\n")
    repo.put("docs/a.md", "1")
    base = repo.commit()
    repo.put("docs/a.md", "2")
    tip = repo.commit()
    repo.run(base, tip, config="ci/nope.conf").cannot()


def test_config_is_a_directory(repo: Repo) -> None:
    repo.put("docs/a.md", "1")
    base = repo.commit()
    repo.put("docs/a.md", "2")
    tip = repo.commit()
    (repo.root / "ci" / "dir.conf").mkdir(parents=True)
    repo.run(base, tip, config="ci/dir.conf").cannot()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads anything")
def test_unreadable_config(repo: Repo) -> None:
    repo.config("docs/* -> NONE\n")
    repo.put("docs/a.md", "1")
    base = repo.commit()
    repo.put("docs/a.md", "2")
    tip = repo.commit()
    conf = repo.root / "ci" / "delta.conf"
    conf.chmod(0)
    try:
        repo.run(base, tip).cannot()
    finally:
        conf.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.parametrize("which", ["base", "tip"])
@pytest.mark.parametrize("bad", ["no-such-ref", "0123456789abcdef0123456789abcdef01234567"])
def test_bad_ref(repo: Repo, which: str, bad: str) -> None:
    repo.config("docs/* -> NONE\n")
    repo.put("docs/a.md", "1")
    base = repo.commit()
    repo.put("docs/a.md", "2")
    tip = repo.commit()
    args = (bad, tip) if which == "base" else (base, bad)
    repo.run(*args).cannot()


def test_not_a_git_repo(repo: Repo, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    (plain / "ci").mkdir(parents=True)
    (plain / "ci" / "delta.conf").write_text("* -> NONE\n")
    repo.run("HEAD~1", "HEAD", script=repo.root / "scripts" / "py_delta.py", cwd=plain).cannot()


def test_same_commit_is_empty_diff(repo: Repo) -> None:
    repo.config("docs/* -> NONE\n")
    repo.put("docs/a.md", "1")
    base = repo.commit()
    repo.run(base, base).cannot()


def test_identical_trees_is_empty_diff(repo: Repo) -> None:
    repo.config("docs/* -> NONE\n")
    repo.put("docs/a.md", "1")
    base = repo.commit()
    tip = repo.commit()  # --allow-empty: same tree
    assert base != tip
    repo.run(base, tip).cannot()


# --- the script and the config in use are always full -----------------------------------------


def test_change_to_script_is_full(repo: Repo) -> None:
    _change(repo, "* -> NONE\n", {}, {"scripts/py_delta.py": _script_bytes().decode() + "\n# x\n"}).full()


def test_change_to_config_in_use_is_full(repo: Repo) -> None:
    repo.config("* -> NONE\n")
    base = repo.commit()
    repo.config("* -> NONE\n# edited\n")
    tip = repo.commit()
    repo.run(base, tip).full()


def test_change_to_another_config_is_not_special(repo: Repo) -> None:
    _change(repo, "* -> NONE\n", {"ci/other.conf": "a"}, {"ci/other.conf": "b"}).subset()


def test_string_prefix_of_script_is_not_an_ancestor(repo: Repo) -> None:
    """`scripts/py` is a string prefix of `scripts/py_delta.py`, not a path ancestor."""
    _change(repo, "* -> NONE\n", {"scripts/py": "1"}, {"scripts/py": "2"}).subset()


def test_gitlink_ancestor_of_script_is_full(repo: Repo) -> None:
    """A submodule pin bump: the gitlink `vendor/x` sits above `vendor/x/scripts/py_delta.py`."""
    repo.config("* -> NONE\n")
    repo.gitlink("vendor/x", "1" * 40)
    base = repo.commit()
    repo.gitlink("vendor/x", "2" * 40)
    tip = repo.commit()
    inner = repo.root / "vendor" / "x" / "scripts"
    inner.mkdir(parents=True)
    (inner / "py_delta.py").write_bytes(_script_bytes())
    repo.run(base, tip, script="vendor/x/scripts/py_delta.py").full()


def test_gitlink_not_above_script_is_routed(repo: Repo) -> None:
    """Control for the above: the same bump, the script elsewhere, routes by the config."""
    repo.config("* -> NONE\n")
    repo.gitlink("vendor/x", "1" * 40)
    base = repo.commit()
    repo.gitlink("vendor/x", "2" * 40)
    tip = repo.commit()
    repo.run(base, tip).subset()


def test_gitlink_ancestor_of_config_is_full(repo: Repo) -> None:
    repo.gitlink("vendor/x", "1" * 40)
    base = repo.commit()
    repo.gitlink("vendor/x", "2" * 40)
    tip = repo.commit()
    conf = repo.root / "vendor" / "x" / "delta.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("* -> NONE\n")
    repo.run(base, tip, config="vendor/x/delta.conf").full()


def test_script_outside_repo_any_py_delta_is_full(repo: Repo, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "py_delta.py"
    outside.parent.mkdir()
    outside.write_bytes(_script_bytes())
    repo.config("* -> NONE\n")
    repo.put("tools/py_delta.py", "1")
    base = repo.commit()
    repo.put("tools/py_delta.py", "2")
    tip = repo.commit()
    repo.run(base, tip, script=outside).full()


def test_script_outside_repo_routes_other_paths(repo: Repo, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "py_delta.py"
    outside.parent.mkdir()
    outside.write_bytes(_script_bytes())
    repo.config("* -> NONE\n")
    repo.put("docs/a.md", "1")
    base = repo.commit()
    repo.put("docs/a.md", "2")
    tip = repo.commit()
    repo.run(base, tip, script=outside).subset()


# --- build / pytest files are always full -----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "Makefile",
        "conftest.py",
        "tests/conftest.py",
        "a/b/c/conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
        "requirements.txt",
        "requirements-dev.txt",
    ],
)
def test_build_and_pytest_files_always_full(repo: Repo, path: str) -> None:
    _change(repo, "* -> NONE\n", {path: "1"}, {path: "2"}).full()


def test_conftest_lookalike_is_routed(repo: Repo) -> None:
    _change(repo, "* -> NONE\n", {"src/my_conftest.py": "1"}, {"src/my_conftest.py": "2"}).subset()


# --- symlinks ---------------------------------------------------------------------------------


def test_symlink_in_tip_is_full(repo: Repo) -> None:
    """Any symlink tracked in the tip, even one the change does not touch."""
    repo.link("docs/link.md", "a.md")
    _change(repo, "docs/* -> NONE\n", {"docs/a.md": "1"}, {"docs/a.md": "2"}).full()


def test_symlink_added_by_change_is_full(repo: Repo) -> None:
    repo.config("* -> NONE\n")
    base = repo.commit()
    repo.link("docs/link.py", "../src/x.py")
    tip = repo.commit()
    repo.run(base, tip).full()


def test_symlink_only_in_base_is_routed(repo: Repo) -> None:
    """The rule reads the TIP tree: a symlink the change removes no longer counts."""
    repo.config("docs/* -> NONE\n")
    repo.link("docs/link.md", "a.md")
    base = repo.commit()
    repo.drop("docs/link.md")
    tip = repo.commit()
    repo.run(base, tip).subset()


# --- control characters and whitespace --------------------------------------------------------

CONTROL = ["\t", "\n", "\r", "\x1b", "\x07", "\x7f"]


@pytest.mark.parametrize("ch", CONTROL, ids=[repr(c) for c in CONTROL])
def test_control_char_in_changed_path_is_cannot_assess(repo: Repo, ch: str) -> None:
    path = f"docs/a{ch}b.md"
    res = _change(repo, "docs/* -> NONE\n", {path: "1"}, {path: "2"})
    res.cannot()
    assert not _has_control(res.last), res._show()


@pytest.mark.parametrize("ch", [" ", *CONTROL], ids=[repr(c) for c in [" ", *CONTROL]])
def test_whitespace_or_control_in_selected_test_is_cannot_assess(repo: Repo, ch: str) -> None:
    repo.put(f"tests/test{ch}x.py", "def test(): pass\n")
    res = _change(repo, "docs/* -> tests/test*x.py\n", {"docs/a.md": "1"}, {"docs/a.md": "2"})
    res.cannot()
    assert not _has_control(res.last), res._show()


def test_full_why_escapes_control_chars(repo: Repo) -> None:
    """A dead test glob holding ESC: the verdict is not a subset and its why is escaped."""
    res = _change(repo, "docs/* -> tests/\x1b[31mgone.py\n", {"docs/a.md": "1"}, {"docs/a.md": "2"})
    assert res.rc in (1, 2), res._show()
    assert FULL_RE.match(res.last) or CANNOT_RE.match(res.last), res._show()
    assert not _has_control(res.last), res._show()


def test_last_line_wins_over_earlier_output(repo: Repo) -> None:
    """Whatever else it prints, the verdict is the last stdout line and matches the contract."""
    res = _change(repo, "docs/* -> tests/test_docs.py\n", {"docs/a.md": "1"}, {"docs/a.md": "2"})
    assert SUBSET_RE.match(res.last), res._show()
    assert res.stdout.endswith("\n"), "the verdict line is newline-terminated"


# --- this repo's wiring: scripts/py-delta.conf ------------------------------------------------


def test_repo_config_exists() -> None:
    assert CONF.is_file(), "scripts/py-delta.conf does not exist"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A scratch clone of THIS repo whose HEAD carries the working copies of the script, the
    config, the gate, and a root conftest that marks any pytest run (for the dry-run test)."""
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    dest = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(ROOT), str(dest)], check=True, capture_output=True)
    for src in (SCRIPT, CONF, GATE):
        assert src.is_file(), f"{src.relative_to(ROOT)} does not exist"
        shutil.copy2(src, dest / "scripts" / src.name)
    (dest / "conftest.py").write_text(
        "import os\n"
        "if os.environ.get('PYDELTA_TEST_MARKER'):\n"
        "    open(os.environ['PYDELTA_TEST_MARKER'], 'w').close()\n"
    )
    _git(dest, "add", "-f", "scripts", "conftest.py")
    _git(dest, "commit", "-q", "--allow-empty", "-m", "base: py-delta under test")
    return dest


def _commit_change(clone: Path, path: str, text: str = "\n# py-delta probe\n") -> tuple[str, str]:
    base = _git(clone, "rev-parse", "HEAD")
    target = clone / path
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(text)
    _git(clone, "add", "-f", "--", path)
    _git(clone, "commit", "-q", "-m", f"probe {path}")
    return base, _git(clone, "rev-parse", "HEAD")


def _run_conf(clone: Path, base: str, tip: str) -> Result:
    proc = subprocess.run(
        [sys.executable, "scripts/py_delta.py", "--config", "scripts/py-delta.conf", base, tip],
        cwd=clone, capture_output=True, text=True,
    )
    return Result(proc.returncode, proc.stdout, proc.stderr)


def _subset_files(res: Result) -> list[str]:
    assert res.rc == 0 and SUBSET_RE.match(res.last), res._show()
    return res.last.split()[2:]


def test_conf_changelog_fragment_runs_its_test(clone: Path) -> None:
    res = _run_conf(clone, *_commit_change(clone, "changelog.d/9999-probe.md", "<!-- section: Fixed -->\n- x\n"))
    assert "tests/issues/test_673_one_section_per_fragment.py" in _subset_files(res)


def test_conf_upgrading_runs_its_test(clone: Path) -> None:
    res = _run_conf(clone, *_commit_change(clone, "UPGRADING.md", "\nprobe\n"))
    assert "tests/issues/test_1233_upgrade_guide.py" in _subset_files(res)


def test_conf_service_is_full(clone: Path) -> None:
    _run_conf(clone, *_commit_change(clone, "src/yurtle_kanban/service.py")).full()


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_service.py",
        "tests/issues/test_673_one_section_per_fragment.py",
        "tests/issues/_bashes.py",
        "tests/issues/test_99999_brand_new.py",
    ],
)
def test_conf_any_test_file_is_full(clone: Path, path: str) -> None:
    _run_conf(clone, *_commit_change(clone, path)).full()


# --- this repo's wiring: scripts/check_delta.sh -----------------------------------------------


def test_gate_exists_and_is_executable() -> None:
    assert GATE.is_file(), "scripts/check_delta.sh does not exist"
    assert os.access(GATE, os.X_OK), "scripts/check_delta.sh is not executable"


DECISION_RE = re.compile(r"^check-delta: (SUBSET|FULL)\b.*$", re.M)


def _gate(clone: Path, tmp_path: Path, *args: str) -> tuple[str, str]:
    """Run the gate in dry-run; return (decision line, all output). Asserts rc 0 and no pytest."""
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    marker = tmp_path / "pytest-ran"
    shim = shims / "pytest"
    shim.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    shim.chmod(0o755)
    env = dict(os.environ)
    env[DRY_RUN_ENV] = "1"
    env["PYDELTA_TEST_MARKER"] = str(marker)
    env["PATH"] = f"{shims}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        ["bash", "scripts/check_delta.sh", *args], cwd=clone, env=env,
        capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"dry run must exit 0 (rc={proc.returncode}):\n{out}"
    assert not marker.exists(), f"dry run ran pytest:\n{out}"
    decisions = DECISION_RE.findall(proc.stdout)
    lines = [m.group(0) for m in DECISION_RE.finditer(proc.stdout)]
    assert len(decisions) == 1, f"want exactly one check-delta decision line on stdout:\n{out}"
    return lines[0], out


def test_gate_subset_on_explicit_base(clone: Path, tmp_path: Path) -> None:
    base, _ = _commit_change(clone, "changelog.d/9999-probe.md", "<!-- section: Fixed -->\n- x\n")
    line, out = _gate(clone, tmp_path, base)
    assert line.startswith("check-delta: SUBSET "), out
    assert "tests/issues/test_673_one_section_per_fragment.py" in line.split(), out


def test_gate_default_base_is_origin_main(clone: Path, tmp_path: Path) -> None:
    base, _ = _commit_change(clone, "changelog.d/9999-probe.md", "<!-- section: Fixed -->\n- x\n")
    _git(clone, "update-ref", "refs/remotes/origin/main", base)
    line, out = _gate(clone, tmp_path)
    assert line.startswith("check-delta: SUBSET "), out
    assert "tests/issues/test_673_one_section_per_fragment.py" in line.split(), out


def test_gate_uses_merge_base(clone: Path, tmp_path: Path) -> None:
    """BASE moved on (a src change) after HEAD forked: only HEAD's own change counts."""
    fork = _git(clone, "rev-parse", "HEAD")
    branch = _git(clone, "rev-parse", "--abbrev-ref", "HEAD")
    _git(clone, "checkout", "-q", "-b", "upstream")
    _commit_change(clone, "src/yurtle_kanban/service.py")
    _git(clone, "checkout", "-q", branch)
    assert _git(clone, "rev-parse", "HEAD") == fork
    _commit_change(clone, "changelog.d/9999-probe.md", "<!-- section: Fixed -->\n- x\n")
    line, out = _gate(clone, tmp_path, "upstream")
    assert line.startswith("check-delta: SUBSET "), out


def test_gate_full_on_source_change(clone: Path, tmp_path: Path) -> None:
    base, _ = _commit_change(clone, "src/yurtle_kanban/service.py")
    line, out = _gate(clone, tmp_path, base)
    assert line.startswith("check-delta: FULL (") and line.endswith(")"), out
    assert "full" in line[len("check-delta: FULL"):], out


def test_gate_full_on_bad_base(clone: Path, tmp_path: Path) -> None:
    _commit_change(clone, "changelog.d/9999-probe.md", "<!-- section: Fixed -->\n- x\n")
    line, out = _gate(clone, tmp_path, "no-such-base-ref")
    assert line.startswith("check-delta: FULL"), out


@pytest.mark.parametrize("dirt", ["modified", "staged", "untracked"])
def test_gate_dirty_tree_is_full(clone: Path, tmp_path: Path, dirt: str) -> None:
    base, _ = _commit_change(clone, "changelog.d/9999-probe.md", "<!-- section: Fixed -->\n- x\n")
    if dirt == "modified":
        with (clone / "README.md").open("a") as f:
            f.write("\ndirt\n")
    elif dirt == "staged":
        (clone / "staged.txt").write_text("dirt\n")
        _git(clone, "add", "staged.txt")
    else:
        (clone / "untracked.txt").write_text("dirt\n")
    line, out = _gate(clone, tmp_path, base)
    assert line.startswith("check-delta: FULL"), out
    assert line.endswith("(cannot-assess dirty working tree)"), out


def test_gate_ignored_file_is_not_dirt(clone: Path, tmp_path: Path) -> None:
    base, _ = _commit_change(clone, "changelog.d/9999-probe.md", "<!-- section: Fixed -->\n- x\n")
    (clone / "__pycache__").mkdir(exist_ok=True)
    (clone / "__pycache__" / "x.pyc").write_bytes(b"\0")
    line, out = _gate(clone, tmp_path, base)
    assert line.startswith("check-delta: SUBSET "), out


# --- driver addition (#1248 r1): every test that names a routed document is in its subset -----
#
# The subsets in scripts/py-delta.conf are hand-maintained, so a new test that reads a document
# could be left off its line and the local check would skip it (r1: test_1247 read CONTRIBUTING.md
# and was missing). Any change adding such a test touches tests/*, which routes FULL, so this guard
# runs wherever it is needed.
#
# LIMIT: it catches DIRECT mentions only — a source line that names the document (`"README.md"`,
# `"docs"`, `.claude`, ...) together with a repo-root anchor (`__file__`, or a module name bound to
# an expression using `__file__` or imported from another test module). It cannot see a document
# read indirectly, e.g. by a script the test runs with its default paths. Helper modules (not
# `test_*.py`) that name a document make every test importing them a required member.

_ANCHOR_DEF = re.compile(r"^([A-Z][A-Z0-9_]*)\s*(?::[^=\n]*)?=[^\n]*__file__", re.M)
_ANCHOR_IMPORT = re.compile(r"^\s*from\s+tests(?:\.\w+)*\s+import\s+\(?([^)\n]+)", re.M)
_TESTS_DIR = ROOT / "tests"


def _routed_documents() -> list[tuple[str, set[str]]]:
    """(path glob, listed test files) for every rule of the real conf that names tests."""
    rules: list[tuple[str, set[str]]] = []
    for raw in CONF.read_text(encoding="utf-8").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        glob, targets = (part.strip() for part in line.split("->"))
        if targets.split() not in (["FULL"], ["NONE"]):
            rules.append((glob, set(targets.split())))
    return rules


def _document_needle(glob: str) -> re.Pattern[str]:
    """How a test names the document: `docs/*` as a `docs` path part, a file by its name."""
    if glob.endswith("/*"):
        top = re.escape(glob.split("/", 1)[0])
        return re.compile(rf"(?<![\w.-]){top}(?=[\"'/])")
    return re.compile(re.escape(glob))


def _anchors(src: str) -> set[str]:
    names = set(_ANCHOR_DEF.findall(src))
    for group in _ANCHOR_IMPORT.findall(src):
        for part in group.split(","):
            name = part.strip().split(" as ")[-1].strip()
            if name.isupper():
                names.add(name)
    return names


def _names_document(src: str, needle: re.Pattern[str]) -> bool:
    anchors = _anchors(src)
    for line in src.splitlines():
        if "tmp" in line or not needle.search(line):
            continue  # a tmp_path fixture file of the same name is not the repo's document
        if "__file__" in line or any(re.search(rf"\b{a}\b", line) for a in anchors):
            return True
    return False


def _module(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def test_every_test_naming_a_routed_document_is_in_its_subset() -> None:
    sources = {
        p: p.read_text(encoding="utf-8")
        for p in sorted(_TESTS_DIR.rglob("*.py"))
        if p.name != Path(__file__).name
    }
    missing: list[str] = []
    for glob, listed in _routed_documents():
        needle = _document_needle(glob)
        for path, src in sources.items():
            if not _names_document(src, needle):
                continue
            readers = [path]
            if not path.name.startswith("test_"):  # a helper: its importers read the document
                mod = re.compile(rf"\b{re.escape(_module(path))}\b|import {path.stem}\b")
                readers = [p for p, s in sources.items() if p.name.startswith("test_")
                           and mod.search(s)]
            for reader in readers:
                rel = reader.relative_to(ROOT).as_posix()
                if rel not in listed:
                    missing.append(f"{glob}: {rel}")
    assert not missing, (
        "tests that name a routed document but are not in its subset in "
        "scripts/py-delta.conf:\n" + "\n".join(sorted(set(missing)))
    )
