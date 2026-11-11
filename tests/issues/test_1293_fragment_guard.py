"""Issue #1293: test_1224's static guard accepted a bare ``assert fragment.is_file()``.

test_1224 (#1224, #1226) walks the tests by AST and flags any function that reads a real
``changelog.d/<N>.md`` fragment without surviving its release. It counted ANY
``.exists()`` / ``.is_file()`` call on the fragment as a guard, so
``assert fragment.is_file()`` passed the check. That assert is itself an unguarded read:
the release PR #1292 deleted ``changelog.d/1288.md`` and turned
``test_1288_id_numbering.py::test_changelog_fragment_exists`` red while test_1224 passed
on both trees.

Proposal (the issue): an existence check counts as a guard only when it is a branch
condition (``if``, a ternary, or a ``pytest.skip`` path), never inside an ``assert``.
Done when: test_1224 flags a planted ``assert (ROOT / "changelog.d" / "9999.md").is_file()``
and still accepts the guarded idioms in the current tests.

These tests drive the guard through its public entry, ``unguarded_reads(source, label)``,
on synthetic sources, plus live controls on the real tree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_1224_fragment_reads_survive_release import (
    TESTS,
    unguarded_reads,
)

HEAD = 'from pathlib import Path\nimport os\nimport pytest\nROOT = Path(".")\n'
CONST = HEAD + 'FRAGMENT = ROOT / "changelog.d" / "1207.md"\n'


# --- the bug: an existence check inside an assert guards nothing ---------------------

def test_planted_bare_assert_from_the_issue_is_flagged() -> None:
    # the issue's "Done when", verbatim
    src = HEAD + ('def test_x():\n'
                  '    assert (ROOT / "changelog.d" / "9999.md").is_file()\n')
    assert unguarded_reads(src, "f.py") == ["f.py:test_x"]


def test_bare_assert_is_file_then_read_is_flagged() -> None:
    src = CONST + ("def test_x():\n    assert FRAGMENT.is_file()\n"
                   "    assert FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == ["<src>:test_x"]


def test_bare_assert_exists_then_read_is_flagged() -> None:
    src = CONST + ("def test_x():\n    assert FRAGMENT.exists(), 'missing'\n"
                   "    FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == ["<src>:test_x"]


def test_bare_assert_on_a_local_alias_is_flagged() -> None:
    src = HEAD + ('def test_x():\n    p = ROOT / "changelog.d" / "12.md"\n'
                  '    assert p.is_file(), f"missing {p}"\n    p.read_text()\n')
    assert unguarded_reads(src) == ["<src>:test_x"]


def test_bare_assert_through_os_path_is_flagged() -> None:
    exists = CONST + ("def test_x():\n    assert os.path.exists(FRAGMENT)\n"
                      "    FRAGMENT.read_text()\n")
    isfile = CONST + ("def test_x():\n    assert os.path.isfile(FRAGMENT)\n"
                      "    FRAGMENT.read_text()\n")
    for src in (exists, isfile):
        assert unguarded_reads(src) == ["<src>:test_x"], src


def test_assert_with_the_check_inside_a_boolean_is_flagged() -> None:
    # still an assert: it raises when the fragment is gone
    src = CONST + ("def test_x():\n    assert FRAGMENT.is_file() and FRAGMENT.stat().st_size\n")
    assert unguarded_reads(src) == ["<src>:test_x"]


def test_a_bare_expression_statement_check_is_flagged() -> None:
    # the result is discarded: nothing branches on it
    src = CONST + "def test_x():\n    FRAGMENT.exists()\n    FRAGMENT.read_text()\n"
    assert unguarded_reads(src) == ["<src>:test_x"]


def test_the_real_1292_case_is_flagged() -> None:
    # test_1288's test_changelog_fragment_exists before 956a8c3 (the shape #1292 broke)
    src = HEAD + (
        "def test_changelog_fragment_exists() -> None:\n"
        '    fragment = ROOT / "changelog.d" / "1288.md"\n'
        '    assert fragment.is_file(), f"missing {fragment}"\n'
        '    assert "id_numbering" in fragment.read_text(encoding="utf-8")\n'
    )
    assert unguarded_reads(src, "tests/issues/test_1288_id_numbering.py") == [
        "tests/issues/test_1288_id_numbering.py:test_changelog_fragment_exists"
    ]


# --- branch conditions are still guards ----------------------------------------------

def test_if_is_file_then_read_is_accepted() -> None:
    src = CONST + ("def test_x():\n    if FRAGMENT.is_file():\n"
                   "        FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_if_not_exists_return_is_accepted() -> None:
    src = CONST + ("def test_x():\n    if not FRAGMENT.exists():\n        return\n"
                   "    FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_if_check_in_a_boolean_condition_is_accepted() -> None:
    src = CONST + ("def test_x(flag):\n    if flag and FRAGMENT.is_file():\n"
                   "        FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_elif_check_is_accepted() -> None:
    src = CONST + ("def test_x(flag):\n    if flag:\n        return\n"
                   "    elif FRAGMENT.exists():\n        FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_ternary_is_accepted() -> None:
    src = CONST + ("def test_x():\n"
                   "    t = FRAGMENT.read_text() if FRAGMENT.is_file() "
                   "else (ROOT / 'CHANGELOG.md').read_text()\n"
                   "    assert t\n")
    assert unguarded_reads(src) == []


def test_os_path_in_a_branch_is_accepted() -> None:
    src = CONST + ("def test_x():\n    if os.path.isfile(FRAGMENT):\n"
                   "        FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_pytest_skip_path_is_accepted() -> None:
    src = CONST + ("def test_x():\n    if not FRAGMENT.is_file():\n"
                   "        pytest.skip('released')\n    FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_skipif_on_the_check_is_accepted() -> None:
    src = CONST + ("@pytest.mark.skipif(not FRAGMENT.is_file(), reason='released')\n"
                   "def test_x():\n    assert FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_an_assert_inside_a_guarding_branch_is_accepted() -> None:
    # the assert is redundant but harmless: the if already handles the fragment being gone
    src = CONST + ("def test_x():\n    if FRAGMENT.exists():\n"
                   "        assert FRAGMENT.is_file()\n        FRAGMENT.read_text()\n")
    assert unguarded_reads(src) == []


def test_the_fixed_1288_shape_is_accepted() -> None:
    # test_1288 after 956a8c3
    src = HEAD + (
        "import re\n"
        "def test_changelog_fragment_exists() -> None:\n"
        '    fragment = ROOT / "changelog.d" / "1288.md"\n'
        "    if fragment.exists():\n"
        '        entry = fragment.read_text(encoding="utf-8")\n'
        "    else:\n"
        '        bullets = re.split(r"\\n(?=- )", (ROOT / "CHANGELOG.md").read_text())\n'
        '        found = [b for b in bullets if "#1288" in b]\n'
        "        assert found\n"
        "        entry = found[0]\n"
        '    assert "id_numbering" in entry, entry\n'
    )
    assert unguarded_reads(src) == []


# --- live controls on the real tree ----------------------------------------------------

@pytest.mark.parametrize("name", [
    "test_1207_anchor_categories.py",
    "test_1242_resolution_frontmatter.py",
    "test_1288_id_numbering.py",
])
def test_current_guarded_readers_are_still_accepted(name: str) -> None:
    path = TESTS / "issues" / name
    src = path.read_text(encoding="utf-8")
    assert "changelog.d" in src, name  # the control is not vacuous
    assert unguarded_reads(src, name) == [], name


def test_the_guard_still_passes_on_the_current_tree() -> None:
    self_path = Path(__file__).resolve()
    guard = (TESTS / "issues" / "test_1224_fragment_reads_survive_release.py").resolve()
    offenders: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.resolve() in (self_path, guard):
            continue
        rel = path.relative_to(TESTS.parent).as_posix()
        offenders += unguarded_reads(path.read_text(encoding="utf-8"), rel)
    assert offenders == [], offenders
