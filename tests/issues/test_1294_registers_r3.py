"""Issue #1294, review round 3 (PR #1295 at f001cd8, verdict CHANGES): partner tests for
the review's findings, under the driver's rulings.

- N2 (MAJOR): ``validate --since REV`` also reads the ``registers:`` config as it was at
  REV (``git show REV:<config path>``) and checks every entry file that existed at REV
  under any of REV's register folders.
  - An entry file present at REV whose path is gone now is an error ("deleted or
    renamed"), EXCEPT a pure move: the same frontmatter id at a new path under the same
    register's CURRENT folder, whose content passes the append-only rule against the REV
    file. That is accepted.
  - A register present at REV and absent from today's config is its own error.
  - Removing ``type: register-entry`` from a REV entry is a non-append change, so it is
    caught.
  Plants: the reviewer's ``relocate.sh`` (folder moved in the config plus ``git mv`` plus
  the text rewritten with a fresh digest) and ``drop.sh`` (the register dropped from the
  config and the type line dropped from its entries) are refused. A pure move (a config
  path change plus ``git mv``, content unchanged, then a legitimate ``set``) and a pure
  rename inside the folder are accepted. Control: an unchanged config with normal verbs
  is accepted.
- m-e: plain ``validate`` reports a second ``kb:allocatedFor``, or a block value that
  differs from the frontmatter ``allocated_for`` (``allocfor.sh``).
"""

from __future__ import annotations

import re

import pytest

from tests.issues.test_1294_registers import (
    DEC_DIR,
    DECISIONS,
    ITEM_ID,
    PATTERN,
    STEPS,
    TEXT1,
    A,
    Reg,
    _import,
    alloc,
    config_text,
    fill,
    flat,
    ok,
    push_files,
    show,
    sync,
    validate,
    yk,
)
from yurtle_kanban.registers import normalise_text, text_digest

pytestmark = pytest.mark.usefixtures("claim_env")

NEW_DIR = "registers/dec2/"


@pytest.fixture
def reg(tmp_path, monkeypatch) -> Reg:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return Reg(tmp_path)


@pytest.fixture
def base(reg, monkeypatch) -> tuple[Reg, str]:
    """D1 filled, D2 reserved; returns (reg, BASE)."""
    alloc(reg, monkeypatch, "One")
    alloc(reg, monkeypatch, "Two")
    ok(fill(reg, monkeypatch, 1, TEXT1))
    return reg, reg.sha()


def refused_since(reg: Reg, mp, rev: str, *needles: str) -> str:
    res = validate(reg, mp, "--since", rev)
    out = flat(res.output)
    assert res.exit_code != 0, f"validate --since passed: {out}"
    assert any(n in out for n in needles), f"none of {needles} named: {out}"
    return out


def moved_config() -> str:
    return config_text(DECISIONS.replace(f"path: {DEC_DIR}", f"path: {NEW_DIR}"))


def set_verb(reg: Reg, mp, n: int, *extra: str) -> None:
    sync(reg.a)
    ok(yk(reg.a, mp, "register", "set", "decisions", str(n), *extra, "--agent", A))


# --- N2: moves, rewrites and drops ---------------------------------------------------------


def test_relocate_and_rewrite_is_refused(base, monkeypatch) -> None:
    """relocate.sh: the folder moves in the config, the file is git-mv'd, its text is
    rewritten with a fresh digest."""
    reg, rev = base
    old_path = reg.entry(1)
    new_path = NEW_DIR + old_path.rsplit("/", 1)[1]
    text = reg.show(old_path)
    rewritten = normalise_text("We decide NOT X.\n")
    new = text.replace(normalise_text(TEXT1), rewritten).replace(
        text_digest(TEXT1), text_digest(rewritten))
    assert new != text and text_digest(rewritten) in new
    files: dict[str, str | None] = {".kanban/config.yaml": moved_config()}
    for path in reg.files(DEC_DIR):  # every entry moves, so nothing is left as a stray
        files[path] = None
        files[NEW_DIR + path.rsplit("/", 1)[1]] = reg.show(path)
    files[new_path] = new
    push_files(reg, files, "move the register and rewrite D1")
    refused_since(reg, monkeypatch, rev, old_path, new_path)


def test_move_out_of_the_register_folder_is_refused(base, monkeypatch) -> None:
    """The config is unchanged; the file moves to a folder no register owns."""
    reg, rev = base
    old_path = reg.entry(1)
    push_files(reg, {old_path: None, "registers/elsewhere/" + old_path.rsplit("/", 1)[1]:
                     reg.show(old_path)}, "move D1 out of its register")
    refused_since(reg, monkeypatch, rev, old_path)


def test_dropping_the_register_and_its_type_lines_is_refused(base, monkeypatch) -> None:
    """drop.sh: the register leaves the config, its entries lose `type: register-entry`."""
    reg, rev = base
    files: dict[str, str | None] = {".kanban/config.yaml": config_text(STEPS)}
    for path in reg.files(DEC_DIR):
        text = reg.show(path)
        assert re.search(r"^type: register-entry\n", text, re.M)
        files[path] = re.sub(r"^type: register-entry\n", "", text, flags=re.M)
    push_files(reg, files, "drop the decisions register")
    refused_since(reg, monkeypatch, rev, "decisions")


def test_dropping_the_register_from_the_config_alone_is_refused(base, monkeypatch) -> None:
    reg, rev = base
    push_files(reg, {".kanban/config.yaml": config_text(STEPS)}, "drop the register only")
    refused_since(reg, monkeypatch, rev, "decisions")


def test_removing_the_type_line_alone_is_refused(base, monkeypatch) -> None:
    reg, rev = base
    path = reg.entry(1)
    push_files(reg, {path: re.sub(r"^type: register-entry\n", "", reg.show(path),
                                  flags=re.M)}, "untype D1")
    refused_since(reg, monkeypatch, rev, path)


def test_a_pure_move_of_the_folder_is_accepted(base, monkeypatch) -> None:
    """The config path changes and the files are git-mv'd unchanged; a later `set` through
    the verb appends to the moved file."""
    reg, rev = base
    files: dict[str, str | None] = {".kanban/config.yaml": moved_config()}
    for path in reg.files(DEC_DIR):
        files[path] = None
        files[NEW_DIR + path.rsplit("/", 1)[1]] = reg.show(path)
    push_files(reg, files, "move the register folder")
    set_verb(reg, monkeypatch, 1, "--status", "decided", "--title", "One, decided")
    assert reg.files(NEW_DIR) and not reg.files(DEC_DIR)
    assert "One, decided" in show(reg, monkeypatch, 1)
    ok(validate(reg, monkeypatch, "--since", rev))
    ok(validate(reg, monkeypatch))


def test_a_pure_rename_inside_the_folder_is_accepted(base, monkeypatch) -> None:
    reg, rev = base
    old_path = reg.entry(1)
    new_path = DEC_DIR + "DEC-001-renamed.md"
    assert new_path != old_path
    push_files(reg, {old_path: None, new_path: reg.show(old_path)}, "rename D1's file")
    set_verb(reg, monkeypatch, 1, "--status", "decided")
    ok(validate(reg, monkeypatch, "--since", rev))


def test_a_move_whose_content_is_not_append_only_is_refused(base, monkeypatch) -> None:
    """A move is pure only when the moved content passes the append-only rule."""
    reg, rev = base
    old_path = reg.entry(1)
    new_path = DEC_DIR + "DEC-001-renamed.md"
    text = re.sub(r"^(title: .*)$", r"\1\ntitle: Rewritten", reg.show(old_path), count=1,
                  flags=re.M)
    push_files(reg, {old_path: None, new_path: text}, "rename and edit D1")
    refused_since(reg, monkeypatch, rev, old_path, new_path)


def test_control_unchanged_config_and_verbs_are_accepted(base, monkeypatch) -> None:
    reg, rev = base
    ok(fill(reg, monkeypatch, 2, "Supersedes one.\n", "--supersedes", "1"))
    set_verb(reg, monkeypatch, 1, "--status", "decided", "--title", "One, decided")
    alloc(reg, monkeypatch, "Three")
    _import(reg, monkeypatch, "## 5 — 2026-10-15 — Five\nFive text.\n", PATTERN)
    ok(validate(reg, monkeypatch, "--since", rev))


def test_control_a_deleted_entry_is_still_refused(base, monkeypatch) -> None:
    reg, rev = base
    path = reg.entry(2)
    push_files(reg, {path: None}, "delete D2")
    refused_since(reg, monkeypatch, rev, path)


# --- m-e: kb:allocatedFor is a once-fact ------------------------------------------------------


def _before_fence(text: str, insert: str) -> str:
    i = text.rfind("\n```\n")
    assert i > 0, text
    return text[: i + 1] + insert + text[i + 1:]


@pytest.fixture
def allocated(reg, monkeypatch) -> Reg:
    alloc(reg, monkeypatch, "One", "decisions", "--by", ITEM_ID)
    alloc(reg, monkeypatch, "Two")  # no --by
    ok(validate(reg, monkeypatch))  # control
    return reg


def test_a_second_allocated_for_is_an_error(allocated, monkeypatch) -> None:
    """allocfor.sh: a second kb:allocatedFor appended in the block."""
    reg = allocated
    path = reg.entry(1)
    push_files(reg, {path: _before_fence(reg.show(path), "<> kb:allocatedFor item:EXP-999 .\n")},
               "restate allocatedFor")
    res = validate(reg, monkeypatch)
    assert res.exit_code != 0 and path in flat(res.output), flat(res.output)


def test_allocated_for_in_the_block_without_frontmatter_is_an_error(allocated,
                                                                     monkeypatch) -> None:
    reg = allocated
    path = reg.entry(2)
    text = reg.show(path)
    assert "allocated_for" not in text
    push_files(reg, {path: _before_fence(text, "<> kb:allocatedFor item:EXP-999 .\n")},
               "add allocatedFor later")
    res = validate(reg, monkeypatch)
    assert res.exit_code != 0 and path in flat(res.output), flat(res.output)


def test_frontmatter_allocated_for_differing_from_the_block_is_an_error(allocated,
                                                                         monkeypatch) -> None:
    reg = allocated
    path = reg.entry(1)
    text = reg.show(path)
    assert re.search(rf"^allocated_for: {ITEM_ID}$", text, re.M), text
    push_files(reg, {path: re.sub(rf"^allocated_for: {ITEM_ID}$", "allocated_for: EXP-002",
                                  text, flags=re.M)}, "frontmatter disagrees")
    res = validate(reg, monkeypatch)
    assert res.exit_code != 0 and path in flat(res.output), flat(res.output)
