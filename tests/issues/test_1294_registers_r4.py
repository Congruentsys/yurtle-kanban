"""Issue #1294, review round 4 (PR #1295 at 7b7499b, verdict CHANGES): partner tests for
the review's findings, under the driver's rulings.

- N3: ``validate --since REV`` judges REV's entries by IDENTITY. For every (register, id)
  at REV, exactly one file in that register's CURRENT folder holds the id, and that file
  must be append-only against the REV content, whether or not the REV path still exists.
  A REV path that is still present but is not the holder is an error ("left behind").
  ``.gitattributes`` must not be able to switch off the stray-entry check (no ``-I``:
  list the tracked files and parse them).
  Plants:
  - the reviewer's ``binattr.sh``: attributes mark the old folder binary, the files are
    copied to a new folder, and one copy is tampered → refused;
  - the same with untampered copies → refused (left behind);
  - copies left behind without attributes → refused;
  - a stray entry under a folder the attributes call binary → refused by plain
    ``validate``.
  Controls: a pure move (old files removed) is still accepted, with or without
  attributes on the new folder.
- N4: an entry path that is a symlink, or any blob whose git mode is not 100644/100755,
  is an error in plain ``validate`` and in ``--since``, both at REV and now.
  Plants (``symlink.sh``):
  - PR1 swaps the entry for a symlink to an identical file → refused;
  - PR2, measured from PR1, rewrites the link's target → refused;
  - PR2, measured from PR1, puts a tampered regular file back → refused (a REV blob that
    is a symlink under a register folder is an error, never a silent skip).
  Control: an executable entry (100755) with unchanged content is accepted.

The driver's threat-model sentence (``--since`` checks ordinary non-append edits in a
change; it is no defence against a committer who subverts git itself) is README work, so
it has no test here.
"""

from __future__ import annotations

import os

import pytest

from tests.issues.test_585_create_push_loop import git
from tests.issues.test_1294_registers import (
    DEC_DIR,
    DECISIONS,
    A,
    Reg,
    alloc,
    config_text,
    fill,
    flat,
    ok,
    show,
    sync,
    validate,
    yk,
)
from yurtle_kanban.registers import text_digest

pytestmark = pytest.mark.usefixtures("claim_env")

NEW_DIR = "registers/dec2/"
X = "We decide X.\n"
NOT_X = "We decide NOT X.\n"


@pytest.fixture
def reg(tmp_path, monkeypatch) -> Reg:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return Reg(tmp_path)


@pytest.fixture
def base(reg, monkeypatch) -> tuple[Reg, str]:
    """D1 filled "We decide X.", D2 reserved (as the probes' `mk`); returns (reg, BASE)."""
    alloc(reg, monkeypatch, "One")
    ok(fill(reg, monkeypatch, 1, X))
    alloc(reg, monkeypatch, "Two")
    return reg, reg.sha()


def tamper(text: str) -> str:
    out = text.replace("We decide X.", "We decide NOT X.").replace(
        text_digest(X), text_digest(NOT_X))
    assert out != text and text_digest(NOT_X) in out, text
    return out


def commit_push(reg: Reg, message: str) -> str:
    git(reg.a, "add", "-A")
    git(reg.a, "commit", "-q", "-m", message)
    git(reg.a, "push", "-q", "origin", "HEAD:refs/heads/main")
    return reg.sha()


def run_validate(reg: Reg, mp, *extra: str):
    """validate in A's tree as it stands (A is in step with origin after commit_push)."""
    return yk(reg.a, mp, "validate", *extra)


def refused_naming(res, *needles: str) -> str:
    out = flat(res.output)
    assert res.exit_code != 0, f"validate passed: {out}"
    assert any(n in out for n in needles), f"none of {needles} named: {out}"
    return out


def moved_config() -> str:
    return config_text(DECISIONS.replace(f"path: {DEC_DIR}", f"path: {NEW_DIR}"))


def copy_folder(reg: Reg, *, remove_old: bool) -> dict[str, str]:
    """Copy every entry file of DEC_DIR to NEW_DIR (keeping or removing the old ones) and
    point the config at NEW_DIR; returns {name: content}."""
    sync(reg.a)
    names = {}
    (reg.a / NEW_DIR).mkdir(parents=True, exist_ok=True)
    for p in sorted((reg.a / DEC_DIR).glob("*.md")):
        names[p.name] = p.read_text()
        (reg.a / NEW_DIR / p.name).write_text(names[p.name])
        if remove_old:
            git(reg.a, "rm", "-q", f"{DEC_DIR}{p.name}")
    (reg.a / ".kanban" / "config.yaml").write_text(moved_config())
    return names


def d1_name(names: dict[str, str]) -> str:
    return next(n for n in names if n.startswith("DEC-001"))


# --- N3: identity, left-behind files, attributes ---------------------------------------------


def test_binattr_left_behind_and_tampered_copy_is_refused(base, monkeypatch) -> None:
    """binattr.sh."""
    reg, rev = base
    (reg.a / ".gitattributes").write_text(f"{DEC_DIR}*.md binary\n")
    names = copy_folder(reg, remove_old=False)
    d1 = d1_name(names)
    (reg.a / NEW_DIR / d1).write_text(tamper(names[d1]))
    commit_push(reg, "move the register, leave the old files, tamper the copy")
    refused_naming(run_validate(reg, monkeypatch, "--since", rev), NEW_DIR + d1, DEC_DIR + d1)


def test_binattr_left_behind_pure_copies_are_refused(base, monkeypatch) -> None:
    reg, rev = base
    (reg.a / ".gitattributes").write_text(f"{DEC_DIR}*.md binary\n")
    names = copy_folder(reg, remove_old=False)
    commit_push(reg, "copy the register, leave the old files")
    refused_naming(run_validate(reg, monkeypatch, "--since", rev), DEC_DIR + d1_name(names))


def test_copies_left_behind_without_attributes_are_refused(base, monkeypatch) -> None:
    reg, rev = base
    names = copy_folder(reg, remove_old=False)
    commit_push(reg, "copy the register, leave the old files")
    refused_naming(run_validate(reg, monkeypatch, "--since", rev), DEC_DIR + d1_name(names))


def test_attributes_do_not_hide_a_stray_entry(base, monkeypatch) -> None:
    reg, _ = base
    sync(reg.a)
    ok(run_validate(reg, monkeypatch))  # control
    stray = "kanban-work/DEC-002-stray.md"
    (reg.a / ".gitattributes").write_text("kanban-work/** binary\n")
    (reg.a / stray).write_text(reg.show(reg.entry(2)))
    commit_push(reg, "a stray entry under a binary-attributed folder")
    refused_naming(run_validate(reg, monkeypatch), stray)


@pytest.mark.parametrize("attrs", [None, f"{NEW_DIR}*.md binary\n"],
                         ids=["no-attributes", "new-folder-binary"])
def test_control_a_pure_move_is_accepted(base, monkeypatch, attrs) -> None:
    reg, rev = base
    if attrs:
        (reg.a / ".gitattributes").write_text(attrs)
    copy_folder(reg, remove_old=True)
    commit_push(reg, "move the register folder")
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "set", "decisions", "1", "--status", "decided",
          "--agent", A))
    assert "decided" in show(reg, monkeypatch, 1).lower()
    ok(validate(reg, monkeypatch, "--since", rev))
    ok(validate(reg, monkeypatch))


# --- N4: symlinks and other non-regular blobs -------------------------------------------------


def _mode(reg: Reg, path: str, rev: str = "main") -> str:
    return git(reg.remote, "ls-tree", rev, "--", path).split()[0]


def symlink_pr1(reg: Reg) -> tuple[str, str]:
    """symlink.sh PR1: D1's file becomes a symlink to an identical DEC-001.txt."""
    sync(reg.a)
    path = reg.entry(1)
    target = DEC_DIR + "DEC-001.txt"
    (reg.a / target).write_text((reg.a / path).read_text())
    (reg.a / path).unlink()
    os.symlink("DEC-001.txt", reg.a / path)
    sha = commit_push(reg, "PR1: symlink")
    assert _mode(reg, path) == "120000"
    return path, sha


def test_symlink_pr1_is_refused(base, monkeypatch) -> None:
    reg, rev = base
    path, _ = symlink_pr1(reg)
    refused_naming(run_validate(reg, monkeypatch), path)
    refused_naming(run_validate(reg, monkeypatch, "--since", rev), path)


def test_symlink_pr2_measured_from_pr1_is_refused(base, monkeypatch) -> None:
    reg, _ = base
    path, pr1 = symlink_pr1(reg)
    target = reg.a / DEC_DIR / "DEC-001.txt"
    target.write_text(tamper(target.read_text()))
    commit_push(reg, "PR2: rewrite the link's target")
    refused_naming(run_validate(reg, monkeypatch, "--since", pr1), path)
    refused_naming(run_validate(reg, monkeypatch), path)


def test_a_symlink_at_rev_replaced_by_a_tampered_file_is_refused(base, monkeypatch) -> None:
    reg, _ = base
    path, pr1 = symlink_pr1(reg)
    text = (reg.a / path).read_text()
    (reg.a / path).unlink()
    (reg.a / path).write_text(tamper(text))
    git(reg.a, "rm", "-q", "--cached", f"{DEC_DIR}DEC-001.txt")
    (reg.a / DEC_DIR / "DEC-001.txt").unlink()
    commit_push(reg, "PR2: a regular, tampered file again")
    assert _mode(reg, path) == "100644"
    refused_naming(run_validate(reg, monkeypatch, "--since", pr1), path)


def test_control_an_executable_entry_is_accepted(base, monkeypatch) -> None:
    reg, rev = base
    sync(reg.a)
    path = reg.entry(1)
    git(reg.a, "update-index", "--chmod=+x", path)
    os.chmod(reg.a / path, 0o755)
    commit_push(reg, "an executable entry")
    assert _mode(reg, path) == "100755"
    ok(run_validate(reg, monkeypatch))
    ok(run_validate(reg, monkeypatch, "--since", rev))
