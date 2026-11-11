"""Issue #1294, review round 1 (PR #1295 at 65e298a, verdict CHANGES): partner tests for
the review's findings, under the driver's rulings.

- M1: under ``validate --since REV`` an entry file must be byte-identical to REV except
  for insertions inside its yurtle block, before the closing fence, plus ONE insertion
  under ``## Text`` only when the entry had no text and no digest at REV. Plain
  ``validate`` flags an entry with more than one ``kb:textDigest`` event, and an event
  status that is not a configured status, ``reserved``, or a ``void`` set by import
  (``void`` on a filled entry is an error).
- M2: with ``index:`` configured, ``alloc``, ``fill``, ``set`` and ``import`` regenerate
  the index in the SAME origin commit; a fresh clone then validates (rc 0).
- m1: zero-padded cites (``DEC-007``, ``D02``) are cites of 7 / 2 under the default
  pattern and a pattern derived from ``cite_form``; an unregistered padded cite
  (``DEC-099``) is reported.
- m2: a scanned file that is not UTF-8 is reported by ``validate`` (naming the file) and
  by ``cites`` (a warning on stderr), never skipped silently.
- m3: refused configs: ``path: .``, ``path: ..``, two registers sharing a path, an
  ``index:`` inside a register folder.
- m4: a ``type: register-entry`` file outside every register folder is reported.
- m5: ``id_prefix`` is refused only when it equals a prefix of a type the CONFIGURED
  boards use: ``DIR`` with no board defining ``directive`` is accepted; ``EXP`` with the
  nautical board is still refused.
- m6: a re-import whose already-filled number has DIFFERENT text is refused, naming the
  number, and writes nothing; the same text is a no-op.

The plants reuse the reviewer's probe scenarios (``append.sh``, ``append2.sh``,
``cites.sh``, ``cfg.py``) and write the entry format the implementation writes at
65e298a (a ``<> kb:statusChange [ … ] .`` event inside the ```` ```yurtle ```` block). The
harness is round 0's (``test_1294_registers.py``).
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from tests.issues.test_574_sync_and_push import commit_files
from tests.issues.test_585_create_push_loop import git
from tests.issues.test_1294_registers import (
    BOARDS,
    DEC_DIR,
    DECISIONS,
    INDEX,
    OLD_LIST,
    PATTERN,
    STEPS,
    TEXT1,
    A,
    Reg,
    _import,
    _with_index,
    alloc,
    config_text,
    fill,
    flat,
    ok,
    push_files,
    refused,
    show,
    sync,
    validate,
    yk,
)
from yurtle_kanban.cli import main
from yurtle_kanban.registers import text_digest

pytestmark = pytest.mark.usefixtures("claim_env")


@pytest.fixture
def reg(tmp_path, monkeypatch) -> Reg:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return Reg(tmp_path)


def event(status: str, digest: str | None = None) -> str:
    digest_line = f'    kb:textDigest "{digest}" ;\n' if digest else ""
    return (
        "<> kb:statusChange [\n"
        f"    kb:status kb:{status} ;\n{digest_line}"
        '    kb:at "2026-11-11T09:00:00+00:00"^^xsd:dateTime ;\n'
        '    kb:by "planter" ;\n'
        "  ] .\n"
    )


def before_fence(text: str, insert: str) -> str:
    """`insert` just before the yurtle block's closing fence (the file's last fence)."""
    i = text.rfind("```\n")
    assert i > 0, text
    return text[:i] + insert + text[i:]


def rc_names(res, *needles: str) -> str:
    out = flat(res.output)
    assert res.exit_code != 0, f"validate passed: {out}"
    for needle in needles:
        assert needle in out, f"{needle!r} not named: {out}"
    return out


# --- M1: validate --since --------------------------------------------------------------


@pytest.fixture
def filled(reg, monkeypatch) -> tuple[Reg, str, str]:
    """D1 filled with TEXT1, D2 reserved; returns (reg, BASE sha, D1's path)."""
    alloc(reg, monkeypatch, "First")
    alloc(reg, monkeypatch, "Second")
    ok(fill(reg, monkeypatch, 1, TEXT1))
    return reg, reg.sha(), reg.entry(1)


def _text_appended_with_second_digest(text: str) -> str:
    new_text = TEXT1 + "And also Z, retroactively.\n"
    text = text.replace("And its second line.\n",
                        "And its second line.\nAnd also Z, retroactively.\n", 1)
    return before_fence(text, event("open", text_digest(new_text)))


def _text_prepended_with_second_digest(text: str) -> str:
    new_text = "A retroactive preface.\n" + TEXT1
    text = text.replace("The verbatim ruling text, line one.\n",
                        "A retroactive preface.\nThe verbatim ruling text, line one.\n", 1)
    return before_fence(text, event("open", text_digest(new_text)))


def _created_inserted(text: str) -> str:
    out, n = re.subn(r"^(created: .*)$", r"\1\ncreated: 1999-01-01", text, count=1, flags=re.M)
    assert n == 1, text
    return out


def _title_inserted(text: str) -> str:
    out, n = re.subn(r"^(title: .*)$", r"\1\ntitle: Rewritten title", text, count=1, flags=re.M)
    assert n == 1, text
    return out


def _void_event_inserted(text: str) -> str:
    return before_fence(text, event("void"))


def _line_after_fence(text: str) -> str:
    return text + "A note appended after the yurtle block.\n"


SINCE_PLANTS = {
    "text-appended-plus-second-digest": _text_appended_with_second_digest,
    "text-prepended-plus-second-digest": _text_prepended_with_second_digest,
    "created-line-inserted": _created_inserted,
    "title-line-inserted": _title_inserted,
    "void-event-on-filled-entry": _void_event_inserted,
    "line-after-closing-fence": _line_after_fence,
}


@pytest.mark.parametrize("plant", list(SINCE_PLANTS), ids=list(SINCE_PLANTS))
def test_since_refuses_insertions_outside_the_block(filled, monkeypatch, plant) -> None:
    reg, base, path = filled
    ok(validate(reg, monkeypatch, "--since", base))  # control: nothing changed yet
    push_files(reg, {path: SINCE_PLANTS[plant](reg.show(path))}, f"plant {plant}")
    rc_names(validate(reg, monkeypatch, "--since", base), path)


def test_since_accepts_a_real_set_and_fill(filled, monkeypatch) -> None:
    reg, base, _ = filled
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "set", "decisions", "1", "--status", "decided",
          "--title", "Renamed", "--agent", A))
    ok(fill(reg, monkeypatch, 2, "Second ruling, filled after BASE.\n"))
    ok(validate(reg, monkeypatch, "--since", base))


def test_plain_validate_refuses_a_second_digest_event(filled, monkeypatch) -> None:
    reg, _, path = filled
    ok(validate(reg, monkeypatch))  # control
    text = reg.show(path)
    push_files(reg, {path: before_fence(text, event("open", text_digest(TEXT1)))},
               "a second, matching digest event")
    rc_names(validate(reg, monkeypatch), path)


@pytest.mark.parametrize("status", ["bogus", "void"])
def test_plain_validate_refuses_a_bad_event_status(filled, monkeypatch, status) -> None:
    reg, _, path = filled
    push_files(reg, {path: before_fence(reg.show(path), event(status))}, f"{status} event")
    rc_names(validate(reg, monkeypatch), path)


def test_control_import_voids_and_reserved_entries_validate(reg, monkeypatch) -> None:
    _import(reg, monkeypatch, OLD_LIST, PATTERN)  # D3 becomes void
    alloc(reg, monkeypatch, "Reserved fifth")
    assert "void" in show(reg, monkeypatch, 3).lower()
    ok(validate(reg, monkeypatch))


# --- M2: the index rides every pushing verb's commit -------------------------------------


def _fresh_validate(reg: Reg, monkeypatch, tag: str) -> None:
    fresh = reg.tmp / f"fresh-{tag}"
    git(reg.tmp, "clone", "-q", str(reg.remote), str(fresh))
    res = yk(fresh, monkeypatch, "validate")
    assert res.exit_code == 0, f"{tag}: a fresh clone does not validate: {flat(res.output)}"


def _one_commit_with_index(reg: Reg, base: str, tag: str) -> None:
    tip = reg.sha()
    assert git(reg.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        f"{tag}: want one new commit on origin"
    )
    files = commit_files(reg.remote, tip)
    assert any(f.startswith(DEC_DIR) for f in files), f"{tag}: no entry in {files}"
    assert INDEX in files, f"{tag}: the commit did not regenerate {INDEX}: {files}"


def test_every_pushing_verb_regenerates_the_index(reg, monkeypatch) -> None:
    _with_index(reg)
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "index", "decisions", "--write"))
    git(reg.a, "add", "-A")
    git(reg.a, "commit", "-q", "-m", "commit the generated index")
    git(reg.a, "push", "-q", "origin", "HEAD:refs/heads/main")
    _fresh_validate(reg, monkeypatch, "seed")  # control

    base = reg.sha()
    alloc(reg, monkeypatch, "First")
    _one_commit_with_index(reg, base, "alloc")
    _fresh_validate(reg, monkeypatch, "alloc")

    base = reg.sha()
    ok(fill(reg, monkeypatch, 1, TEXT1))
    _one_commit_with_index(reg, base, "fill")
    _fresh_validate(reg, monkeypatch, "fill")

    base = reg.sha()
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "set", "decisions", "1", "--status", "decided",
          "--title", "Renamed", "--agent", A))
    _one_commit_with_index(reg, base, "set")
    _fresh_validate(reg, monkeypatch, "set")

    base = reg.sha()
    src = reg.tmp / "later.md"
    src.write_text("## 2 — 2026-10-13 — Second rule\nSecond text.\n\n"
                   "## 4 — 2026-10-15 — Fourth rule\nFourth text.\n")
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "import", "decisions", "--from", str(src),
          "--pattern", PATTERN, "--agent", A))
    _one_commit_with_index(reg, base, "import")
    _fresh_validate(reg, monkeypatch, "import")


# --- m1: zero-padded cites ------------------------------------------------------------------


PADDED = f"""\
  - name: decisions
    path: {DEC_DIR}
    id_prefix: DEC
    cite_form: "D{{n}}"
    scan: ["docs/**"]
  - name: rulings
    path: registers/rulings/
    id_prefix: RUL
    scan: ["docs/**"]
"""
RULINGS = """\
# Rulings

## 1 — 2026-10-01 — One
One.

## 2 — 2026-10-02 — Two
Two.

## 7 — 2026-10-07 — Seven
Seven.
"""


@pytest.fixture
def padded(reg, monkeypatch) -> Reg:
    push_files(reg, {".kanban/config.yaml": BOARDS + "registers:\n" + PADDED},
               "derived cite patterns")
    alloc(reg, monkeypatch, "One")
    alloc(reg, monkeypatch, "Two")
    _import(reg, monkeypatch, RULINGS, PATTERN, "rulings")  # RUL 1, 2, 7 (3-6 void)
    return reg


def test_padded_cites_are_cites(padded, monkeypatch) -> None:
    reg = padded
    push_files(reg, {"docs/p.md": "Padded D02 here.\nAnd the id form RUL-007 there.\n"},
               "padded cites")
    sync(reg.a)
    out = ok(yk(reg.a, monkeypatch, "register", "cites", "decisions", "2"))
    assert "docs/p.md" in out, f"D02 is not read as a cite of D2 (derived pattern): {out}"
    out = ok(yk(reg.a, monkeypatch, "register", "cites", "rulings", "7"))
    assert "docs/p.md" in out, f"RUL-007 is not read as a cite of 7 (default pattern): {out}"
    ok(validate(reg, monkeypatch))  # control: both exist


@pytest.mark.parametrize("cite,needle", [("RUL-099", "99"), ("D099", "99")])
def test_an_unregistered_padded_cite_is_reported(padded, monkeypatch, cite, needle) -> None:
    reg = padded
    ok(validate(reg, monkeypatch))  # control
    push_files(reg, {"docs/q.md": f"Per {cite}, we stop.\n"}, "padded dangling cite")
    rc_names(validate(reg, monkeypatch), "docs/q.md", needle)


# --- m2: undecodable scanned files ----------------------------------------------------------


def test_a_non_utf8_scanned_file_is_reported(reg, monkeypatch) -> None:
    alloc(reg, monkeypatch, "First")
    ok(validate(reg, monkeypatch))  # control
    sync(reg.a)
    (reg.a / "docs" / "bin.md").write_bytes(b"\xff\xfe bad utf8 D99\n")
    git(reg.a, "add", "-A")
    git(reg.a, "commit", "-q", "-m", "a binary doc")
    git(reg.a, "push", "-q", "origin", "HEAD:refs/heads/main")
    rc_names(validate(reg, monkeypatch), "docs/bin.md")
    sync(reg.a)
    monkeypatch.chdir(reg.a)
    res = CliRunner().invoke(main, ["register", "cites", "decisions", "1"])
    assert "docs/bin.md" in flat(res.stderr), (
        f"cites skipped an undecodable file silently: stdout={res.stdout!r} "
        f"stderr={res.stderr!r}"
    )


# --- m3: refused config shapes --------------------------------------------------------------


BAD_PATHS = [
    ("path-dot", config_text(DECISIONS.replace(f"path: {DEC_DIR}", "path: .")), "path"),
    ("path-dotdot", config_text(DECISIONS.replace(f"path: {DEC_DIR}", "path: ..")), "path"),
    ("shared-path", BOARDS + "registers:\n" + DECISIONS
     + STEPS.replace("path: kanban-work/registers/steps/", f"path: {DEC_DIR}"), "path"),
    ("index-inside-register",
     config_text(DECISIONS.replace("    sequence: integer\n",
                                   f"    sequence: integer\n    index: {DEC_DIR}INDEX.md\n")),
     "index"),
]


@pytest.mark.parametrize("case,text,key", BAD_PATHS, ids=[c[0] for c in BAD_PATHS])
def test_bad_register_paths_are_refused(reg, monkeypatch, case, text, key) -> None:
    ok(yk(reg.a, monkeypatch, "register", "list", "decisions"))  # control: the seed config
    (reg.a / ".kanban" / "config.yaml").write_text(text)
    out = refused(yk(reg.a, monkeypatch, "register", "list", "decisions"))
    assert key in out, f"{case}: the refusal does not name {key!r}: {out}"
    res = yk(reg.a, monkeypatch, "validate")
    assert res.exit_code != 0 and key in flat(res.output), f"{case}: {flat(res.output)}"


# --- m4: an entry file outside every register folder ----------------------------------------


@pytest.mark.parametrize("where", ["kanban-work/DEC-002-stray.md", "docs/DEC-002-stray.md"])
def test_a_stray_entry_file_is_reported(reg, monkeypatch, where) -> None:
    alloc(reg, monkeypatch, "First")
    alloc(reg, monkeypatch, "Second")
    ok(validate(reg, monkeypatch))  # control
    stray = reg.show(reg.entry(2))
    assert re.search(r"^type: register-entry$", stray, re.M), stray
    push_files(reg, {where: stray}, "a stray entry copy")
    rc_names(validate(reg, monkeypatch), where)


# --- m5: id_prefix is refused only against the configured boards' types ---------------------


def test_a_prefix_no_configured_board_uses_is_accepted(reg, monkeypatch) -> None:
    push_files(reg, {".kanban/config.yaml": config_text(
        DECISIONS.replace("id_prefix: DEC", "id_prefix: DIR"))}, "a directives register")
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "list", "decisions"))
    out = alloc(reg, monkeypatch, "First directive")
    assert re.search(r"(?<![A-Za-z0-9])D1(?![0-9])", out), out
    files = reg.files(DEC_DIR)
    assert len(files) == 1 and "DIR-" in files[0], files
    ok(validate(reg, monkeypatch))


def test_control_a_prefix_the_nautical_board_uses_is_still_refused(reg, monkeypatch) -> None:
    (reg.a / ".kanban" / "config.yaml").write_text(
        config_text(DECISIONS.replace("id_prefix: DEC", "id_prefix: EXP")))
    out = refused(yk(reg.a, monkeypatch, "register", "list", "decisions"))
    assert "id_prefix" in out, out


# --- m6: a re-import with changed text ------------------------------------------------------------


def test_reimport_with_changed_text_is_refused(reg, monkeypatch) -> None:
    _import(reg, monkeypatch, OLD_LIST, PATTERN)
    tip = reg.sha()
    changed = OLD_LIST.replace("Do the first thing.", "Do another thing.")
    changed += "\n## 5 — 2026-10-16 — Fifth rule\nFifth text.\n"
    src = reg.tmp / "changed.md"
    src.write_text(changed)
    sync(reg.a)
    out = refused(yk(reg.a, monkeypatch, "register", "import", "decisions", "--from", str(src),
                     "--pattern", PATTERN, "--agent", A))
    assert re.search(r"(?<![A-Za-z0-9])(D1|DEC-0*1)(?![0-9])", out), out
    assert reg.sha() == tip, "a refused import wrote to origin"
    assert sorted(reg.entries()) == [1, 2, 3, 4], "D5 was written by a refused import"
    assert not git(reg.a, "status", "--porcelain").strip()
    assert "Do the first thing." in show(reg, monkeypatch, 1)


def test_control_reimport_with_the_same_text_is_a_noop(reg, monkeypatch) -> None:
    _import(reg, monkeypatch, OLD_LIST, PATTERN)
    tip = reg.sha()
    src = reg.tmp / "same.md"
    src.write_text(OLD_LIST)
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "import", "decisions", "--from", str(src),
          "--pattern", PATTERN, "--agent", A))
    assert reg.sha() == tip

