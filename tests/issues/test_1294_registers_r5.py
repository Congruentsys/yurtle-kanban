"""Issue #1294, review round 5 (PR #1295 at 3385110, verdict CHANGES): partner tests for
N5, under the driver's rulings.

N5: entries stored through a clean/smudge filter (git-lfs, ``lfs.sh``). ``--since`` read
REV as the raw blob (a pointer, which parses as no entry, so the entry was skipped) and
read the current side from the smudged working tree. Two ordinary PRs then rewrite D1's
text, and each passes its own gate.

Rulings:
(a) ``validate --since REV`` reads BOTH sides from git objects (the REV blobs and the
    HEAD blobs), never the working tree;
(b) any ``.md`` under one of REV's register folders whose REV blob does not parse as an
    entry is an error, never a silent skip.

The filter is planted twice:
- without git-lfs, as a clean/smudge filter in ``.git/config`` plus ``.gitattributes``.
  Its clean step stores a pointer-like blob (a header line plus base64), which is the
  shape lfs gives the object store;
- with git-lfs itself, as the reviewer ran it, when ``git lfs`` is installed (a local
  ``file://`` store, no server).

Plants: PR1 (the entries go through the filter) is refused, and PR2 (D1's text rewritten
in the smudged file, measured from PR1) is refused.

Controls:
- normal verbs, committed, are accepted;
- an uncommitted non-append edit in the working tree is not ``--since``'s concern: it
  passes ``--since`` and is refused once committed;
- an uncommitted text edit is still reported by plain ``validate`` as a digest mismatch.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

import pytest

from tests.issues.test_585_create_push_loop import git
from tests.issues.test_1294_registers import (
    DEC_DIR,
    A,
    Reg,
    alloc,
    fill,
    flat,
    ok,
    sync,
    yk,
)
from yurtle_kanban.registers import text_digest

pytestmark = pytest.mark.usefixtures("claim_env")

X = "We decide X.\n"
NOT_X = "We decide NOT X.\n"

CLEAN = r"""
import base64, sys
data = sys.stdin.buffer.read()
if data.startswith(b"pointer v1\n"):
    sys.stdout.buffer.write(data)
else:
    sys.stdout.buffer.write(b"pointer v1\n" + base64.b64encode(data) + b"\n")
"""
SMUDGE = r"""
import base64, sys
data = sys.stdin.buffer.read()
if data.startswith(b"pointer v1\n"):
    sys.stdout.buffer.write(base64.b64decode(data[len(b"pointer v1\n"):].strip()))
else:
    sys.stdout.buffer.write(data)
"""


@pytest.fixture
def reg(tmp_path, monkeypatch) -> Reg:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return Reg(tmp_path)


@pytest.fixture
def base(reg, monkeypatch) -> tuple[Reg, str]:
    """D1 filled "We decide X.", D2 reserved; A in step with origin. Returns (reg, BASE).
    The PRs below are local commits in A: `validate --since` reads A's repository."""
    alloc(reg, monkeypatch, "One")
    ok(fill(reg, monkeypatch, 1, X))
    alloc(reg, monkeypatch, "Two")
    sync(reg.a)
    return reg, git(reg.a, "rev-parse", "HEAD").strip()


def tamper(text: str) -> str:
    out = text.replace("We decide X.", "We decide NOT X.").replace(
        text_digest(X), text_digest(NOT_X))
    assert out != text and text_digest(NOT_X) in out, text
    return out


def commit(reg: Reg, message: str) -> str:
    git(reg.a, "add", "-A")
    git(reg.a, "commit", "-q", "-m", message)
    return git(reg.a, "rev-parse", "HEAD").strip()


def d1(reg: Reg) -> str:
    return next(p.name for p in (reg.a / DEC_DIR).glob("DEC-001*.md"))


def refused_naming(res, *needles: str) -> str:
    out = flat(res.output)
    assert res.exit_code != 0, f"validate passed: {out}"
    assert any(n in out for n in needles), f"none of {needles} named: {out}"
    return out


def plant_filter(reg: Reg) -> None:
    """A clean/smudge filter (lfs's shape, without lfs) on the register folder."""
    scripts = reg.tmp / "filter"
    scripts.mkdir()
    (scripts / "clean.py").write_text(CLEAN)
    (scripts / "smudge.py").write_text(SMUDGE)
    git(reg.a, "config", "filter.ptr.clean", f'"{sys.executable}" "{scripts / "clean.py"}"')
    git(reg.a, "config", "filter.ptr.smudge", f'"{sys.executable}" "{scripts / "smudge.py"}"')
    git(reg.a, "config", "filter.ptr.required", "true")
    (reg.a / ".gitattributes").write_text(f"{DEC_DIR}*.md filter=ptr\n")
    git(reg.a, "add", ".gitattributes")
    git(reg.a, "add", "--renormalize", DEC_DIR)


def plant_lfs(reg: Reg) -> None:
    git(reg.a, "lfs", "install", "--local", "--skip-repo")  # no hooks: hooksPath is /dev/null
    git(reg.a, "config", "lfs.url", f"file://{reg.tmp / 'lfs-store'}")
    git(reg.a, "lfs", "track", f"{DEC_DIR}*.md")
    git(reg.a, "add", ".gitattributes")
    git(reg.a, "add", "--renormalize", DEC_DIR)


def _has_lfs() -> bool:
    if shutil.which("git-lfs") is None:
        return False
    return subprocess.run(["git", "lfs", "version"], capture_output=True).returncode == 0


PLANTS = [
    pytest.param(plant_filter, id="clean-smudge-filter"),
    pytest.param(plant_lfs, id="git-lfs",
                 marks=pytest.mark.skipif(not _has_lfs(), reason="git-lfs not installed")),
]


def pr1(reg: Reg, plant) -> str:
    plant(reg)
    sha = commit(reg, "PR1: store the entries through a filter")
    name = d1(reg)
    blob = git(reg.a, "show", f"HEAD:{DEC_DIR}{name}")
    assert "We decide X." not in blob, "the plant stored the entry as text, not a pointer"
    assert "We decide X." in (reg.a / DEC_DIR / name).read_text(), "the tree is not smudged"
    return sha


# --- N5 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("plant", PLANTS)
def test_pr1_entries_through_a_filter_is_refused(base, monkeypatch, plant) -> None:
    reg, rev = base
    ok(yk(reg.a, monkeypatch, "validate", "--since", rev))  # control: nothing changed yet
    pr1(reg, plant)
    refused_naming(yk(reg.a, monkeypatch, "validate", "--since", rev), DEC_DIR)


@pytest.mark.parametrize("plant", PLANTS)
def test_pr2_measured_from_pr1_is_refused(base, monkeypatch, plant) -> None:
    reg, _ = base
    rev = pr1(reg, plant)
    path = reg.a / DEC_DIR / d1(reg)
    path.write_text(tamper(path.read_text()))
    commit(reg, "PR2: rewrite D1 through the filter")
    refused_naming(yk(reg.a, monkeypatch, "validate", "--since", rev), DEC_DIR + d1(reg))


# --- controls -----------------------------------------------------------------------------------


def test_control_committed_verbs_are_accepted(base, monkeypatch) -> None:
    reg, rev = base
    ok(fill(reg, monkeypatch, 2, "Supersedes one.\n", "--supersedes", "1"))
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "set", "decisions", "1", "--status", "decided",
          "--title", "One, decided", "--agent", A))
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "validate", "--since", rev))


def test_uncommitted_edit_is_not_since_s_concern(base, monkeypatch) -> None:
    """--since compares REV with HEAD's blobs: a working-tree edit that only --since would
    catch (an inserted frontmatter line) passes until it is committed."""
    reg, rev = base
    path = reg.a / DEC_DIR / d1(reg)
    text, n = re.subn(r"^(created: .*)$", r"\1\ncreated: 1999-01-01", path.read_text(),
                      count=1, flags=re.M)
    assert n == 1
    path.write_text(text)
    ok(yk(reg.a, monkeypatch, "validate", "--since", rev))
    commit(reg, "commit the inserted created: line")
    refused_naming(yk(reg.a, monkeypatch, "validate", "--since", rev), DEC_DIR + d1(reg))


def test_control_plain_validate_still_reports_an_uncommitted_digest_mismatch(
        base, monkeypatch) -> None:
    reg, _ = base
    path = reg.a / DEC_DIR / d1(reg)
    path.write_text(path.read_text().replace("We decide X.", "We decide Y."))
    res = yk(reg.a, monkeypatch, "validate")
    out = flat(res.output)
    assert res.exit_code != 0 and re.search(r"digest", out, re.I), out
