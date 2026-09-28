"""Issue #830 — ``_ids_at`` splits ``git grep -z`` records on ``\\n`` only.

``git grep -z -n`` prints one record per line: ``<rev>:<path>\\0<n>\\0<text>\\n``. The
fields are NUL-separated, the records ``\\n``-terminated. ``_ids_at`` split the output
with ``str.splitlines()``, which also breaks on ``\\r``, ``\\v``, ``\\f``, ``\\x1c``-``\\x1e``,
``\\x85``, U+2028 and U+2029 *inside* a matched line. That line then becomes two
"records", with two effects:

(a) an id dropped. PyYAML (the board's frontmatter parser) treats ``\\r``, ``\\x85``,
    U+2028 and U+2029 as line breaks, so ``id: <sep> EXP-007`` loads as
    ``id: EXP-007`` on the board. git grep sees ONE line (it matches ``^id:[[:space:]]``);
    ``splitlines`` cut it into ``id: `` (no value) and `` EXP-007`` (a bogus record).
    The id vanished: the holder check at origin misses it and next-id re-issues it.

(b) another file's frontmatter state poisoned. The tail after the separator is parsed
    as a record of its own: no NULs, so its whole text is taken as the ``<rev>:<path>``
    field. A body ``---`` rule in ``a.md`` whose tail is ``origin/main:<dir>/b.md``
    enters ``b.md`` in the state map as "not in frontmatter" before git reaches
    ``b.md``, so all of ``b.md``'s ids are skipped.

Not reachable: an id credited to the wrong file, or a wrong id. A bogus record has no
NUL (git grep -I skips any file holding one), so its number and text are empty and it
never yields an id; it can only drop ids.

The [steer] on #830 (bucket 1) gives the spec: split on ``\\n`` only. An id value
ending in ``\\r`` (a CRLF file) is still stripped as before.

LONE ``\\r`` (the ``[cr]`` reds): ``_git_run`` runs git with ``text=True``, i.e. universal
newlines, so a lone ``\\r`` (and a CRLF pair) reaches ``_ids_at`` already turned into
``\\n``. Splitting on ``\\n`` alone does NOT turn these green: the ``git grep`` output must
also be read without newline translation (bytes, decoded as UTF-8). That same
translation is why CRLF files work today; with raw output a CRLF record's text ends in
``\\r``, which the id regex (``[^"'\\s]+``) and ``startswith("---")`` already tolerate: the
CRLF controls pin that.

Reds: (a) for each YAML line break ``\\r``/``\\x85``/U+2028/U+2029, both at origin
(``_ids_at``, ``_holder_at``, ``_next_id_number_at``) and at ``HEAD``; (b) the
poisoned-state case.
Controls (green before and after): a plain LF file; a CRLF file, with a bare and a
quoted id; and in (b) the same body rule without the separator.

Reuses the #585/#590/#808 real-git harness.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import _ORIG_RUN, EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.test_634_explicit_ids_on_base import service
from yurtle_kanban import config as config_mod

NOTES = f"{EXP_DIR}/notes.md"  # stem carries no number: only the frontmatter id counts

# the characters `str.splitlines` breaks on that YAML ALSO reads as a line break, so
# the board loads the file; \v, \f and \x1c-\x1e make PyYAML refuse the file outright
YAML_BREAKS = [("cr", "\r"), ("nel", "\x85"), ("u2028", " "), ("u2029", " ")]


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def item(id_line: str, nl: str = "\n", body: str = "# Rival") -> str:
    lines = ["---", id_line, 'title: "Rival"', "type: expedition", "status: backlog", "---",
             "", body, ""]
    return nl.join(lines)


def on_origin(world: World, files: dict[str, str]) -> None:
    """B pushes `files` to origin/main; A fetches, so they sit on origin/main only."""
    b_push(world, files)
    git(world.a, "fetch", "origin")


def board_id(world: World, text: str) -> object:
    """The id the board's own frontmatter parser reads from `text`."""
    fm = service(world)._parse_frontmatter(text)
    return None if fm is None else fm.get("id")


def assert_held_at(world: World, rev: str, path: str, item_id: str, next_num: int) -> None:
    svc = service(world)
    _, ids = svc._ids_at(rev)
    assert (path, item_id) in ids, f"{item_id} in {path} missing from _ids_at({rev!r}): {ids}"
    assert svc._holder_at(rev, item_id) == path, f"{item_id} not held at {rev}"
    assert svc._next_id_number_at(rev, "EXP") == next_num, (
        f"{item_id} not counted at {rev}: it would be re-issued"
    )


# --- (a) a YAML line break between `id:` and its value (RED) ----------------------------------


@pytest.mark.parametrize(("case", "sep"), YAML_BREAKS, ids=[c[0] for c in YAML_BREAKS])
def test_id_after_yaml_break_held_at_origin(world, case, sep) -> None:
    text = item(f"id: {sep} EXP-007")
    assert board_id(world, text) == "EXP-007", "precondition: the board loads EXP-007"
    on_origin(world, {NOTES: text})
    assert_held_at(world, "origin/main", NOTES, "EXP-007", 8)


@pytest.mark.parametrize(("case", "sep"), YAML_BREAKS, ids=[c[0] for c in YAML_BREAKS])
def test_id_after_yaml_break_in_ids_at_head(world, case, sep) -> None:
    on_origin(world, {NOTES: item(f"id: {sep} EXP-007")})
    git(world.a, "reset", "--hard", "origin/main")
    assert_held_at(world, "HEAD", NOTES, "EXP-007", 8)


# --- (b) a bogus record poisons a later file's frontmatter state (RED) ------------------------

A_NOTE = f"{EXP_DIR}/a.md"
B_NOTE = f"{EXP_DIR}/b.md"  # sorts after a.md: git grep reaches it second


@pytest.mark.parametrize(("case", "sep"), YAML_BREAKS, ids=[c[0] for c in YAML_BREAKS])
def test_body_rule_does_not_poison_later_file(world, case, sep) -> None:
    a_text = item("id: EXP-001", body=f"---{sep}origin/main:{B_NOTE}")
    assert board_id(world, a_text) == "EXP-001", "precondition: a.md loads"
    on_origin(world, {A_NOTE: a_text, B_NOTE: item("id: EXP-009")})
    assert_held_at(world, "origin/main", B_NOTE, "EXP-009", 10)
    _, ids = service(world)._ids_at("origin/main")
    assert (A_NOTE, "EXP-001") in ids, ids


def test_control_body_rule_without_separator(world) -> None:
    a_text = item("id: EXP-001", body=f"--- origin/main:{B_NOTE}")
    on_origin(world, {A_NOTE: a_text, B_NOTE: item("id: EXP-009")})
    assert_held_at(world, "origin/main", B_NOTE, "EXP-009", 10)


# --- controls: plain LF and CRLF files (green before and after) -------------------------------


def test_control_plain_lf_file(world) -> None:
    on_origin(world, {NOTES: item("id: EXP-007")})
    assert_held_at(world, "origin/main", NOTES, "EXP-007", 8)


@pytest.mark.parametrize("id_line", ["id: EXP-007", 'id: "EXP-007"', "id: 'EXP-007'"],
                         ids=["bare", "double-quoted", "single-quoted"])
def test_control_crlf_file(world, id_line) -> None:
    """A CRLF file: each id record's text ends in `\\r`, which the id regex stops at."""
    text = item(id_line, nl="\r\n")
    assert board_id(world, text) == "EXP-007", "precondition: the board loads EXP-007"
    on_origin(world, {NOTES: text})
    raw = _ORIG_RUN(["git", "show", f"origin/main:{NOTES}"], cwd=world.a,
                    capture_output=True, check=True).stdout
    assert b"\r\n" in raw, "precondition: the file is CRLF at origin"
    assert_held_at(world, "origin/main", NOTES, "EXP-007", 8)


def test_control_crlf_file_at_head(world) -> None:
    on_origin(world, {NOTES: item("id: EXP-007", nl="\r\n")})
    git(world.a, "reset", "--hard", "origin/main")
    assert_held_at(world, "HEAD", NOTES, "EXP-007", 8)
