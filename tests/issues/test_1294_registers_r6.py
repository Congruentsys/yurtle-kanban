"""Issue #1294, review round 6 (PR #1295 at 88878a6, verdict APPROVE): partner tests for
the review's minors and notes.

- m-f: an entry blob committed with CRLF at the base must not wedge ``--since``. After an
  empty commit, ``validate --since <that base>`` is rc 0. Today it is refused with "had no
  `## Text` section", because the raw blobs are compared without normalising line
  endings. Control: the PR that converts an entry to CRLF is still refused, measured from
  before it (``crlf2.sh``).
- m-g: ``validate --since`` spawns a bounded number of git processes, not O(entries).
  Spawns are counted in-process by wrapping ``subprocess.run`` and ``subprocess.Popen``
  (no wall-clock assertion), for registers of 20 and 80 imported entries.
  Pinned bound:
  - the spawns ``--since`` adds over plain ``validate`` grow by at most 2 from 20 to 80
    entries;
  - at either size, ``--since`` adds at most 10 spawns per configured register (2
    registers here, so at most 20).
  At 88878a6 it is about 5 ``git cat-file`` spawns per entry.
- NOTE wording:
  - the "is no entry of register 'X'" message names a wrong id as well as a filter;
  - README's registers section says ``--since`` compares REV with HEAD's commit, so
    commit first.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import git
from tests.issues.test_1294_registers import (
    DEC_DIR,
    PATTERN,
    Reg,
    _import,
    alloc,
    fill,
    flat,
    ok,
    sync,
    yk,
)

pytestmark = pytest.mark.usefixtures("claim_env")

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def reg(tmp_path, monkeypatch) -> Reg:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return Reg(tmp_path)


@pytest.fixture
def base(reg, monkeypatch) -> tuple[Reg, str]:
    """D1 filled, D2 reserved; A in step with origin, autocrlf off. Returns (reg, BASE)."""
    alloc(reg, monkeypatch, "One")
    ok(fill(reg, monkeypatch, 1, "We decide X.\n"))
    alloc(reg, monkeypatch, "Two")
    sync(reg.a)
    git(reg.a, "config", "core.autocrlf", "false")
    return reg, git(reg.a, "rev-parse", "HEAD").strip()


def d1(reg: Reg) -> str:
    return DEC_DIR + next(p.name for p in (reg.a / DEC_DIR).glob("DEC-001*.md"))


def to_crlf(reg: Reg) -> str:
    """crlf2.sh (a): convert D1's file to CRLF, commit; returns that commit (B2)."""
    path = reg.a / d1(reg)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    path.write_bytes(raw.replace(b"\n", b"\r\n"))
    git(reg.a, "commit", "-q", "-am", "convert D1 to CRLF")
    assert b"\r\n" in git_blob(reg, f"HEAD:{d1(reg)}")
    return git(reg.a, "rev-parse", "HEAD").strip()


def git_blob(reg: Reg, spec: str) -> bytes:
    return subprocess.run(["git", "cat-file", "blob", spec], cwd=reg.a, capture_output=True,
                          check=True).stdout


# --- m-f: CRLF at the base ---------------------------------------------------------------------


def test_a_crlf_entry_at_the_base_does_not_wedge_since(base, monkeypatch) -> None:
    reg, _ = base
    b2 = to_crlf(reg)
    git(reg.a, "commit", "-q", "--allow-empty", "-m", "no change")
    res = yk(reg.a, monkeypatch, "validate", "--since", b2)
    assert res.exit_code == 0, f"a CRLF entry at the base wedges --since: {flat(res.output)}"


def test_control_the_pr_converting_to_crlf_is_refused(base, monkeypatch) -> None:
    reg, rev = base
    to_crlf(reg)
    res = yk(reg.a, monkeypatch, "validate", "--since", rev)
    out = flat(res.output)
    assert res.exit_code != 0 and d1(reg) in out, out


# --- m-g: bounded git spawns ---------------------------------------------------------------------


class SpawnCounter:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        run, popen = subprocess.run, subprocess.Popen
        counter = self

        def counted_run(*args, **kwargs):
            counter._see(args[0] if args else kwargs.get("args"))
            return run(*args, **kwargs)

        class CountedPopen(popen):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                counter._see(args[0] if args else kwargs.get("args"))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", counted_run)
        monkeypatch.setattr(subprocess, "Popen", CountedPopen)

    def _see(self, cmd) -> None:
        argv0 = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd).split()[0]
        if Path(str(argv0)).name == "git":
            self.count += 1


def _spawns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: int) -> tuple[int, int]:
    """(plain validate spawns, validate --since spawns) for `entries` imported entries."""
    root = tmp_path / f"n{entries}"
    root.mkdir()
    reg = Reg(root)
    body = "".join(f"## {i} — 2025-01-05 — T{i}\nText {i}.\n\n" for i in range(1, entries + 1))
    _import(reg, monkeypatch, body, PATTERN)
    base = reg.sha()
    git(reg.a, "commit", "-q", "--allow-empty", "-m", "no change")
    counts = []
    for argv in (["validate"], ["validate", "--since", base]):
        with monkeypatch.context() as mp:
            counter = SpawnCounter(mp)
            res = yk(reg.a, mp, *argv)
            assert res.exit_code == 0, flat(res.output)
            counts.append(counter.count)
    return counts[0], counts[1]


REGISTERS = 2  # decisions and steps, in the harness config
PER_REGISTER = 10
GROWTH = 2


def test_since_spawns_a_bounded_number_of_git_processes(tmp_path, monkeypatch) -> None:
    plain20, since20 = _spawns(tmp_path, monkeypatch, 20)
    plain80, since80 = _spawns(tmp_path, monkeypatch, 80)
    extra20, extra80 = since20 - plain20, since80 - plain80
    detail = (f"plain/--since spawns: 20 entries {plain20}/{since20}, "
              f"80 entries {plain80}/{since80}")
    assert extra80 - extra20 <= GROWTH, f"--since spawns grow with the entries: {detail}"
    assert extra80 <= PER_REGISTER * REGISTERS, f"--since spawns too many: {detail}"


# --- NOTE wording -----------------------------------------------------------------------------------


def test_no_entry_message_names_a_wrong_id_and_a_filter(base, monkeypatch) -> None:
    reg, rev = base
    stray = reg.a / DEC_DIR / "XYZ-001-wrong-prefix.md"
    stray.write_text((reg.a / d1(reg)).read_text().replace("DEC-001", "XYZ-001"))
    git(reg.a, "add", "-A")
    git(reg.a, "commit", "-q", "-m", "a wrong-prefix file in the folder")
    res = yk(reg.a, monkeypatch, "validate", "--since", rev)
    out = flat(res.output)
    assert res.exit_code != 0, out
    line = next((m for m in re.findall(r"[^.]*is no entry of register[^)]*\)?", out)), "")
    assert line, f"no 'is no entry of register' message: {out}"
    assert re.search(r"wrong id", line, re.I), f"the message does not name a wrong id: {line}"
    assert re.search(r"filter", line, re.I), f"the message does not name a filter: {line}"


def test_readme_says_since_compares_with_heads_commit() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^#{2,3} Numbered registers[ \t]*$(.*?)(?=^#{2,3} )", text, re.S | re.M)
    assert m, "no registers section in README.md"
    section = " ".join(m.group(1).split())
    assert re.search(r"HEAD's commit", section), "README does not say --since reads HEAD's commit"
    assert re.search(r"commit first", section, re.I), "README does not say to commit first"
