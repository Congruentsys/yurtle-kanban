"""#952: no test may call `.rglob(...)` on a repo root; the list can't grow again.

`Path.rglob` enters `.git/objects` before any filter runs, and with auto-gc on (#841)
a repack during the walk raises FileNotFoundError (#259, #883, #924, #952). Walks over
a repo root go through `tests/issues/_snapshot.py` (`glob_outside_git`,
`paths_outside_git`, `files_outside_git`), which prune `.git` instead of entering it.

This is a static lint: it AST-scans every `tests/**/*.py` for `<receiver>.rglob(...)`
where the receiver looks like a repo root: a bare name in `ROOT_NAMES`, or an attribute
whose last name is in `ROOT_ATTRS` (`world.a`, `w.b`, `self.root`, `repo.root`). A
subfolder such as `(repo / "work").rglob(...)` or an unrelated `path.rglob(...)` passes.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

ROOT_NAMES = frozenset(
    {"repo", "root", "repo_root", "tmp_path", "clone", "work", "sw", "world_root"}
)
ROOT_ATTRS = frozenset({"a", "b", "root", "repo_root", "repo"})  # `self.repo` (#985)

# The helper itself, and #266's equivalence tests, which call rglob on purpose to
# check glob_outside_git against it.
EXEMPT = frozenset(
    {
        TESTS / "issues" / "_snapshot.py",
        TESTS / "issues" / "test_266_pattern_globs_prune_git.py",
    }
)


def _is_root_receiver(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in ROOT_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in ROOT_ATTRS
    return False


def offenders(source: str) -> list[int]:
    """Line numbers of `<repo-root>.rglob(...)` calls in `source`, and of
    `<repo-root>.glob("**…")`, which walks `.git` just the same (#985)."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and _is_root_receiver(node.func.value)
        and (node.func.attr == "rglob" or node.func.attr == "glob" and _recursive(node))
    )


def _recursive(call: ast.Call) -> bool:
    """A `glob` whose pattern (a string literal or f-string, positional or
    `pattern=`) has `**` anywhere: `*/**/x.md` matches `.git` too (#1011)."""
    first = call.args[0] if call.args else next(
        (k.value for k in call.keywords if k.arg == "pattern"), None
    )
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return "**" in first.value
    if isinstance(first, ast.JoinedStr):
        # a placeholder may be empty: `f"*{x}*"` can be `**` (#1035)
        joined = "".join(str(v.value) for v in first.values if isinstance(v, ast.Constant))
        return "**" in joined
    return False


def _scan(files: list[Path]) -> list[str]:
    return [
        f"{path.relative_to(TESTS.parent)}:{line}"
        for path in files
        for line in offenders(path.read_text(encoding="utf-8"))
    ]


def _test_files() -> list[Path]:
    return sorted(p for p in TESTS.rglob("*.py") if p not in EXEMPT)


def test_no_rglob_on_a_repo_root() -> None:
    found = _scan(_test_files())
    assert not found, (
        "rglob on a repo root walks into .git; use glob_outside_git / paths_outside_git "
        "/ files_outside_git from tests/issues/_snapshot.py:\n  " + "\n  ".join(found)
    )


def test_scanner_flags_root_receivers() -> None:
    src = (
        'repo.rglob("*")\n'
        'world.a.rglob("*.md")\n'
        'self.root.rglob("*.md")\n'
        'tmp_path.rglob("*")\n'
        'next(repo.root.rglob("FEAT-*.md"))\n'
    )
    assert offenders(src) == [1, 2, 3, 4, 5]


def test_scanner_passes_subfolders_and_other_paths() -> None:
    src = (
        '(repo / "x").rglob("*")\n'
        '(world.a / "work").rglob("*.md")\n'
        'path.rglob("*.md")\n'
        'SKILLS_DIR.rglob("SKILL.md")\n'
        'glob_outside_git(repo, "*.md")\n'
    )
    assert offenders(src) == []


def test_lint_is_not_vacuous() -> None:
    files = _test_files()
    assert len(files) > 200, f"only {len(files)} test modules scanned"
    assert TESTS / "issues" / "test_952_no_rglob_on_repo_roots.py" in files
    # The exempt #266 module calls root.rglob on purpose: real source the scanner flags.
    assert _scan([TESTS / "issues" / "test_266_pattern_globs_prune_git.py"])


def test_recursive_glob_and_self_repo_are_flagged() -> None:
    """#985: `repo.glob("**/…")` walks `.git` like rglob; `self.repo` is a root."""
    source = (
        'repo.glob("**/x.md")\n'
        'repo.glob(f"**/{name}")\n'
        'self.repo.rglob("*")\n'
        'repo.glob("x.md")\n'
    )
    assert offenders(source) == [1, 2, 3]


def test_recursive_anywhere_and_keyword_pattern_are_flagged() -> None:
    """`*/**/x.md` and `pattern="**/x"` recurse through `.git` too (#1011)."""
    for src in ('repo.glob("*/**/x.md")', 'repo.glob(pattern="**/x")', 'repo.glob(f"*/**/{n}")'):
        call = ast.parse(src).body[0].value
        assert _recursive(call), src
    assert not _recursive(ast.parse('repo.glob("*.md")').body[0].value)


def test_fstring_star_around_a_placeholder_is_flagged() -> None:
    """`f"*{x}*"` is `**` when `x` is empty (#1035); `f"*{ext}"` never is."""
    assert _recursive(ast.parse('repo.glob(f"*{x}*")').body[0].value)
    assert not _recursive(ast.parse('repo.glob(f"*{ext}")').body[0].value)
