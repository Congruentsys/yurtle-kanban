"""Issue #1224: no test reads a real ``changelog.d/<N>.md`` fragment without surviving
its release.

A release assembles the numbered fragments into CHANGELOG.md and deletes them (#1188,
and again test_1207 in v3.2.0). A test that reads a real fragment unconditionally goes
red the moment the release lands. This static guard walks ``tests/**/*.py`` by AST:
any function that builds a path to a real numbered fragment (directly, or through a
module constant such as ``FRAGMENT = ROOT / "changelog.d" / "1207.md"``) must also
handle the fragment being gone — an ``.exists()`` / ``.is_file()`` check, a
``pytest.skip`` / ``pytest.importorskip`` / ``skipif``, or a fallback to CHANGELOG.md.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

NUMBERED_MD = re.compile(r"\d+\.md")
INLINE_FRAGMENT = re.compile(r"(?:.*/)?changelog\.d/\d+\.md")
GUARD_ATTRS = {"exists", "is_file"}
SKIP_ATTRS = {"skip", "importorskip"}


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_dir(s: str) -> bool:
    return s == "changelog.d" or s.endswith("/changelog.d")


def _is_fragment_expr(node: ast.AST, dir_names: frozenset[str] = frozenset()) -> bool:
    """A path to a real numbered fragment: a `\\d+.md` literal beside a `changelog.d`
    literal (or a module constant holding one) in the same expression, or one literal
    naming `changelog.d/<N>.md`."""
    strings = _strings(node)
    if any(INLINE_FRAGMENT.fullmatch(s) for s in strings):
        return True
    has_dir = any(_is_dir(s) for s in strings) or bool(_names(node) & dir_names)
    return has_dir and any(NUMBERED_MD.fullmatch(s) for s in strings)


def _fragment_exprs(scope: ast.AST, dir_names: frozenset[str]) -> bool:
    """True when some statement-level expression under `scope` is a fragment path."""
    for stmt in ast.walk(scope):
        if not isinstance(stmt, ast.stmt):
            continue
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.expr) and _is_fragment_expr(child, dir_names):
                return True
    return False


def _mentions_skipif(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Attribute) and n.attr in {"skipif", "skip"})
        or (isinstance(n, ast.Name) and n.id == "skipif")
        for n in ast.walk(node)
    )


def _guarded(func: ast.AST, changelog_names: set[str]) -> bool:
    for n in ast.walk(func):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in GUARD_ATTRS:
                return True
            if n.func.attr in SKIP_ATTRS and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == "pytest":
                return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and "CHANGELOG.md" in n.value:
            return True
        if isinstance(n, ast.Name) and n.id in changelog_names:
            return True
    return False


def unguarded_reads(source: str, label: str = "<src>") -> list[str]:
    """`label:function` for every function that reaches a real numbered fragment and
    never handles it being gone."""
    tree = ast.parse(source)
    fragment_names: set[str] = set()
    changelog_names: set[str] = set()
    dir_names: set[str] = set()
    module_skip = False
    for stmt in tree.body:
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if "pytestmark" in names and _mentions_skipif(stmt.value):
                module_skip = True
            if _is_fragment_expr(stmt.value, frozenset(dir_names)) \
                    or _names(stmt.value) & fragment_names:
                fragment_names |= names
            elif any(_is_dir(s) for s in _strings(stmt.value)):
                dir_names |= names
            elif any("CHANGELOG.md" in s for s in _strings(stmt.value)):
                changelog_names |= names
    if module_skip:
        return []

    dirs = frozenset(dir_names)
    found: list[str] = []

    def visit(node: ast.AST, skipped: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                child_skipped = skipped or any(
                    _mentions_skipif(d) for d in child.decorator_list)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and not child_skipped:
                    body = ast.Module(body=child.body, type_ignores=[])
                    reaches = bool(_names(body) & fragment_names) or _fragment_exprs(body, dirs)
                    if reaches and not _guarded(body, changelog_names):
                        found.append(f"{label}:{child.name}")
                visit(child, child_skipped)

    visit(tree, False)
    return found


# --- the real repository ------------------------------------------------------------

def test_no_test_reads_a_real_fragment_unguarded() -> None:
    offenders: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.resolve() == SELF:
            continue
        rel = path.relative_to(TESTS.parent).as_posix()
        offenders += unguarded_reads(path.read_text(encoding="utf-8"), rel)
    assert not offenders, (
        "these tests read a real changelog.d/<N>.md fragment without surviving its "
        "release (check .exists(), pytest.skip, or fall back to CHANGELOG.md):\n  "
        + "\n  ".join(offenders)
    )


def test_the_known_fragment_readers_are_seen() -> None:
    # control: the guard really inspects test_1207 and test_381 (it would not pass
    # vacuously if the constant pattern stopped matching)
    for name in ("test_1207_anchor_categories.py", "test_381_theme_wording.py"):
        src = (TESTS / "issues" / name).read_text(encoding="utf-8")
        stripped = re.sub(r"\.exists\(\)", "", src)
        stripped = stripped.replace("CHANGELOG.md", "CHANGELOG").replace("pytest.skip", "print")
        assert unguarded_reads(stripped, name), name


# --- self-tests on synthetic sources ------------------------------------------------

HEAD = 'from pathlib import Path\nimport pytest\nROOT = Path(".")\n'
CONST = HEAD + 'FRAGMENT = ROOT / "changelog.d" / "1207.md"\n'


def test_unguarded_constant_read_is_flagged() -> None:
    src = CONST + "def test_x():\n    assert FRAGMENT.read_text()\n"
    assert unguarded_reads(src, "f.py") == ["f.py:test_x"]


def test_inline_paths_are_flagged() -> None:
    joined = HEAD + 'def test_a():\n    (ROOT / "changelog.d" / "12.md").read_text()\n'
    literal = HEAD + 'def test_b():\n    Path("changelog.d/12.md").read_text()\n'
    method = HEAD + ('class TestC:\n    def test_c(self):\n'
                     '        open(ROOT / "changelog.d/12.md").read()\n')
    assert unguarded_reads(joined) == ["<src>:test_a"]
    assert unguarded_reads(literal) == ["<src>:test_b"]
    assert unguarded_reads(method) == ["<src>:test_c"]


def test_derived_constant_is_tracked() -> None:
    via_dir = (HEAD + 'FRAG_DIR = ROOT / "changelog.d"\nFRAGMENT = FRAG_DIR / "9.md"\n'
               + 'def test_x():\n    FRAGMENT.read_text()\n'
               + 'def test_y():\n    (FRAG_DIR / "9.md").read_text()\n'
               + 'def test_z():\n    (FRAG_DIR / "README.md").read_text()\n')
    alias = CONST + "F2 = FRAGMENT\ndef test_x():\n    F2.read_text()\n"
    assert unguarded_reads(via_dir) == ["<src>:test_x", "<src>:test_y"]
    assert unguarded_reads(alias) == ["<src>:test_x"]


def test_exists_guard_is_accepted() -> None:
    src = CONST + ("def test_x():\n    if FRAGMENT.exists():\n"
                   "        assert FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_is_file_guard_is_accepted() -> None:
    src = CONST + ("def test_x():\n    if FRAGMENT.is_file():\n"
                   "        assert FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_pytest_skip_is_accepted() -> None:
    src = CONST + ('def test_x():\n    if not FRAGMENT.parent.is_dir():\n'
                   '        pytest.skip("released")\n    assert FRAGMENT.read_text()\n')
    assert unguarded_reads(src) == []


def test_changelog_fallback_is_accepted() -> None:
    literal = CONST + ("def test_x():\n    try:\n        t = FRAGMENT.read_text()\n"
                       "    except OSError:\n"
                       "        t = (ROOT / 'CHANGELOG.md').read_text()\n")
    named = CONST + ('CHANGELOG = ROOT / "CHANGELOG.md"\n'
                     "def test_x():\n    try:\n        t = FRAGMENT.read_text()\n"
                     "    except OSError:\n        t = CHANGELOG.read_text()\n")
    assert unguarded_reads(literal) == []
    assert unguarded_reads(named) == []


def test_skipif_decorator_and_pytestmark_are_accepted() -> None:
    deco = CONST + ("@pytest.mark.skipif(not FRAGMENT.parent.is_dir(), reason='r')\n"
                    "def test_x():\n    assert FRAGMENT.read_text()\n")
    cls = CONST + ("@pytest.mark.skipif(True, reason='r')\nclass TestX:\n"
                   "    def test_x(self):\n        assert FRAGMENT.read_text()\n")
    mark = CONST + ("pytestmark = pytest.mark.skipif(True, reason='r')\n"
                    "def test_x():\n    assert FRAGMENT.read_text()\n")
    assert unguarded_reads(deco) == []
    assert unguarded_reads(cls) == []
    assert unguarded_reads(mark) == []


def test_tmp_path_fragments_are_not_flagged() -> None:
    src = HEAD + ('def test_x(tmp_path):\n    d = tmp_path / "changelog.d"\n'
                  '    d.mkdir()\n    (d / "903.md").write_text("x")\n'
                  '    assert (d / "903.md").read_text() == "x"\n'
                  '    setup(tmp_path, {"5.md": "y"})\n')
    assert unguarded_reads(src) == []


def test_non_numbered_fragment_is_not_flagged() -> None:
    src = HEAD + 'def test_x():\n    (ROOT / "changelog.d" / "README.md").read_text()\n'
    assert unguarded_reads(src) == []


# --- #1226: a guard must name the fragment; non-literal fragment paths are seen -----

def test_an_unrelated_exists_is_not_a_guard() -> None:
    # tmp_path.exists() says nothing about the fragment: this still raises on a release
    src = CONST + ("def test_x(tmp_path):\n    assert tmp_path.exists()\n"
                   "    FRAGMENT.read_text()\n")
    other = CONST + ('def test_x():\n    assert (ROOT / "pyproject.toml").is_file()\n'
                     "    FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == ["<src>:test_x"]
    assert unguarded_reads(other) == ["<src>:test_x"]


def test_a_guard_on_the_fragment_alias_parent_or_inline_path_is_accepted() -> None:
    alias = CONST + ("F2 = FRAGMENT\ndef test_x():\n    if F2.exists():\n"
                     "        F2.read_text()\n")
    local = CONST + ("def test_x():\n    f = FRAGMENT\n    if f.is_file():\n"
                     "        f.read_text()\n")
    parent = CONST + ("def test_x():\n    if FRAGMENT.parent.exists():\n"
                      "        FRAGMENT.read_text()\n")
    inline = HEAD + ('def test_x():\n    p = ROOT / "changelog.d" / "12.md"\n'
                     '    if (ROOT / "changelog.d" / "12.md").exists():\n'
                     "        p.read_text()\n")
    for src in (alias, local, parent, inline):
        assert unguarded_reads(src) == [], src


def test_fstring_fragment_paths_are_flagged() -> None:
    literal = HEAD + 'def test_x(n):\n    Path(f"changelog.d/{n}.md").read_text()\n'
    rooted = HEAD + 'def test_x(n):\n    Path(f"{ROOT}/changelog.d/{n}.md").read_text()\n'
    segment = HEAD + 'def test_x(n):\n    (ROOT / "changelog.d" / f"{n}.md").read_text()\n'
    for src in (literal, rooted, segment):
        assert unguarded_reads(src) == ["<src>:test_x"], src


def test_parametrized_fragment_paths_are_flagged() -> None:
    name = HEAD + ('@pytest.mark.parametrize("n", ["1207.md"])\n'
                   'def test_x(n):\n    (ROOT / "changelog.d" / n).read_text()\n')
    sub = HEAD + 'def test_x(ns):\n    (ROOT / "changelog.d" / ns[0]).read_text()\n'
    call = HEAD + 'def test_x(n):\n    (ROOT / "changelog.d" / str(n)).read_text()\n'
    via_dir = HEAD + ('FRAG_DIR = ROOT / "changelog.d"\n'
                      "def test_x(n):\n    (FRAG_DIR / n).read_text()\n")
    const = HEAD + ('N = "1207.md"\nFRAGMENT = ROOT / "changelog.d" / N\n'
                    "def test_x():\n    FRAGMENT.read_text()\n")
    for src in (name, sub, call, via_dir, const):
        assert unguarded_reads(src) == ["<src>:test_x"], src


def test_guarded_non_literal_fragment_paths_are_accepted() -> None:
    fstr = HEAD + ('def test_x(n):\n    p = Path(f"changelog.d/{n}.md")\n'
                   "    if p.exists():\n        p.read_text()\n")
    param = HEAD + ('def test_x(n):\n    p = ROOT / "changelog.d" / n\n'
                    "    if not p.is_file():\n        pytest.skip('released')\n"
                    "    p.read_text()\n")
    inline = HEAD + ('def test_x(n):\n    if (ROOT / "changelog.d" / n).exists():\n'
                     '        (ROOT / "changelog.d" / n).read_text()\n')
    for src in (fstr, param, inline):
        assert unguarded_reads(src) == [], src


def test_temp_and_readme_non_literal_paths_are_not_flagged() -> None:
    tmp = HEAD + ('def test_x(tmp_path, n):\n    (tmp_path / "changelog.d" / n).write_text("x")\n'
                  '    Path(f"{tmp_path}/changelog.d/{n}.md").read_text()\n')
    readme = HEAD + 'def test_x(d):\n    Path(f"{d}/changelog.d/README.md").read_text()\n'
    listing = HEAD + 'def test_x(n):\n    return f"changelog.d/{n}\\n"\n'
    for src in (tmp, readme, listing):
        assert unguarded_reads(src) == [], src
