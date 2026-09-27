# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issues #788 and #792 — ``_holder_at`` picks the ITEM, not a file named like it.

``KanbanService._holder_at(rev, id)`` returns the first ``.md`` file under the work
paths at ``rev`` whose FILENAME holds the ID (stem equal to it, starting with ID + '-',
or ``_stem_holds``), and only then looks at frontmatter ``id:``. A lookalike that sorts
ahead of the real item wins even when it has no ``id:`` (a draft or outline) or its
``id:`` names another item. ``--push`` then writes the parent's inverse link into the
lookalike — or finds "no turtle block" and writes none (nusy-product-team:
``research/Paper132-.../PAPER-132.md`` sorts ahead of the real paper).

#792: ``_holder_at`` upper-cases the stem before ``_stem_holds``, so ``ß-12-x.md``
arrives as ``SS-12-X`` and holds ``SS-12`` — undoing #775's head-slice fix.

Decided spec ([steer] on #788, #792):
- ``_holder_at`` prefers the file whose frontmatter ``id:`` is the ID (case-folded,
  ``_id_key``-equal).
- A filename-only match counts only for a file with NO frontmatter ``id:``; never for
  one whose ``id:`` names another item.
- The stem reaches ``_stem_holds`` raw: ``ß-12-x.md`` does not hold ``SS-12``.

The seeded parent is ``research/papers/PAPER-130-A-paper.md``. Git lists trees in byte
order, so the #754 lookalikes under ``research/papers/`` (``PAPER-130-OUTLINE.md``,
``Paper130-x/PAPER-130.md``) sort AFTER it and today's first-match already lands on the
real file. To reproduce the nusy-product-team ordering the lookalikes here live in
work dirs that sort BEFORE ``papers/`` (``ideas/``, ``literature/``, ``hypotheses/``);
``_place`` asserts that ordering so the tests cannot go vacuous. The #754 paths are
included too (in "everything") and must stay untouched.

RED today:
- ``_holder_at("origin/main", "PAPER-130")`` (and ``paper-130``) returns a lookalike.
- ``hypothesis create --paper 130 --push`` does not put the link in the real paper.
- A file named ``EXP-042-x.md`` whose ``id:`` is EXP-043 holds EXP-042.
- #792: ``ß-12-x.md`` (no ``id:``) holds ``SS-12``.

Controls (green before and after): a filename-only holder with no ``id:`` still holds
(#590/#634); the file whose ``id:`` is the ID holds it under an unrelated name; an
ASCII ``SS-12-x.md`` still holds ``ss-12``; nothing holds an ID no file has.

Harnesses: the #585/#645/#754 real-git World, ``b_push``, ``seed_on_origin``.
"""

from __future__ import annotations

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import (
    KINDS,
    PAPER,
    assert_one_commit_with_link,
    hdd,
    seed_on_origin,
)
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    flat,
    world,
)
from tests.issues.test_754_duplicate_parent_and_epic import _service
from yurtle_kanban import config as config_mod
from yurtle_kanban.service import KanbanService

HYP = next(k for k in KINDS if k.name == "hypothesis")
PARENT = "PAPER-130"

NO_FRONTMATTER = "# PAPER-130 outline\n\nDraft notes for the paper.\n"
FRONTMATTER_NO_ID = '---\ntitle: "PAPER-130 outline"\nauthor: someone\n---\n\n# Outline\n'
OTHER_ITEM = (
    '---\nid: H130.9\ntitle: "Another hypothesis"\ntype: hypothesis\nstatus: backlog\n'
    "paper: PAPER-130\n---\n\n# Another hypothesis\n"
)

# lookalikes that git lists BEFORE research/papers/PAPER-130-A-paper.md
BEFORE = {
    "draft-dir-no-id": {"research/ideas/Paper130-x/PAPER-130.md": NO_FRONTMATTER},
    "outline-frontmatter-no-id": {"research/literature/PAPER-130-OUTLINE.md": FRONTMATTER_NO_ID},
    "other-id": {"research/hypotheses/paper-130-foo.md": OTHER_ITEM},
}
# the #754 round-3 paths (they sort after the real paper)
AFTER_754 = {
    "research/papers/PAPER-130-OUTLINE.md": NO_FRONTMATTER,
    "research/papers/Paper130-x/PAPER-130.md": NO_FRONTMATTER,
}
EVERYTHING = {**{k: v for s in BEFORE.values() for k, v in s.items()}, **AFTER_754}
SETS = {**BEFORE, "everything": EVERYTHING}


def _place(world: World, monkeypatch, files: dict[str, str]) -> None:
    """Seed the real PAPER-130 on origin, then a rival pushes the lookalikes."""
    seed_on_origin(world, monkeypatch, HYP)
    b_push(world, files)
    listed = world.remote_files()
    for rel in files:
        assert rel in listed, listed
    before = [rel for rel in files if rel not in AFTER_754]
    assert before, "no lookalike sorts before the real paper: the test is vacuous"
    for rel in before:
        assert listed.index(rel) < listed.index(PAPER), (rel, listed)
    git(world.a, "fetch", "-q", "origin")
    # sanity: the board itself sees no duplicate of the parent
    dups = {k.upper() for k in _service(world).duplicate_ids}
    assert PARENT not in dups, f"the fixture made a real duplicate: {dups}"
    config_mod._theme_cache.clear()


def _board_with(world: World, files: dict[str, str]) -> KanbanService:
    """An HDD board on origin holding just `files` (no seeded parent), fetched."""
    hdd(world)
    b_push(world, files)
    git(world.a, "fetch", "-q", "origin")
    return _service(world)


# --- (1) unit: the real item wins over a lookalike that sorts first -----------------


@pytest.mark.parametrize("item_id", [PARENT, PARENT.lower()], ids=["exact", "lower"])
@pytest.mark.parametrize("files", list(SETS.values()), ids=list(SETS))
def test_holder_at_is_the_item(world, monkeypatch, files: dict[str, str], item_id: str) -> None:
    _place(world, monkeypatch, files)
    held = _service(world)._holder_at("origin/main", item_id)
    assert held == PAPER, f"_holder_at({item_id!r}) picked a lookalike: {held}"


# --- (2) --push links the child into the REAL paper, lookalikes untouched -----------


@pytest.mark.parametrize("files", list(SETS.values()), ids=list(SETS))
def test_push_links_into_real_paper(world, monkeypatch, files: dict[str, str]) -> None:
    _place(world, monkeypatch, files)
    base = world.remote_sha()
    result = invoke(world, monkeypatch, list(HYP.argv))
    out = flat(result)
    assert result.exit_code == 0, out
    assert "no turtle block" not in out, f"--push read a lookalike as the parent:\n{out}"
    # one commit: the child, the REAL paper's link, the allocation lock
    assert_one_commit_with_link(world, HYP, base)
    for rel, text in files.items():
        assert world.remote_show(rel) == text, f"--push rewrote lookalike {rel}"


# --- (3) a file named for the ID whose id: is another item does not hold it ---------


def test_named_for_id_but_other_id_does_not_hold(world) -> None:
    rel = "research/experiments/EXP-042-x.md"
    svc = _board_with(world, {rel: "---\nid: EXP-043\ntitle: X\ntype: experiment\n---\n\n# X\n"})
    assert svc._holder_at("origin/main", "EXP-042") is None
    assert svc._holder_at("origin/main", "exp-042") is None


def test_control_other_id_holds_its_own_id(world) -> None:
    rel = "research/experiments/EXP-042-x.md"
    svc = _board_with(world, {rel: "---\nid: EXP-043\ntitle: X\ntype: experiment\n---\n\n# X\n"})
    assert svc._holder_at("origin/main", "EXP-043") == rel


# --- (4) #792: the stem reaches _stem_holds raw -------------------------------------


def _hdd_service(world: World) -> KanbanService:
    hdd(world)
    return _service(world)


def _names_only(svc: KanbanService, monkeypatch, names: list[str]) -> KanbanService:
    """`svc` whose `_ids_at` lists exactly `names`, none with a frontmatter id.

    ``git ls-tree`` C-quotes a non-ASCII path (``"research/ideas/\\303\\237-12-x.md"``,
    core.quotePath), so ``_ids_at`` drops it for not ending in ``.md`` and a real-git
    ``ß-12-x.md`` never reaches the stem check. This pins the stem rule itself."""
    monkeypatch.setattr(svc, "_ids_at", lambda rev: (list(names), []))
    return svc


@pytest.mark.parametrize("item_id", ["SS-12", "ss-12"])
def test_792_eszett_stem_does_not_hold(world, monkeypatch, item_id: str) -> None:
    svc = _names_only(_hdd_service(world), monkeypatch, ["research/ideas/ß-12-x.md"])
    assert svc._holder_at("origin/main", item_id) is None, (
        "the upper-cased stem 'SS-12-X' held SS-12; the raw stem 'ß-12-x' does not (#775)"
    )


@pytest.mark.parametrize("item_id", ["SS-12", "ss-12"])
def test_792_eszett_stem_real_git(world, item_id: str) -> None:
    """Real git: green today only because the quoted name is dropped (see
    `_names_only`); must stay None whatever lists it."""
    svc = _board_with(world, {"research/ideas/ß-12-x.md": NO_FRONTMATTER})
    assert svc._holder_at("origin/main", item_id) is None


def test_792_control_ascii_ss_stem_holds_names_only(world, monkeypatch) -> None:
    rel = "research/ideas/SS-12-x.md"
    svc = _names_only(_hdd_service(world), monkeypatch, [rel])
    assert svc._holder_at("origin/main", "ss-12") == rel


def test_792_control_ascii_ss_stem_holds(world) -> None:
    rel = "research/ideas/SS-12-x.md"
    svc = _board_with(world, {rel: NO_FRONTMATTER})
    assert svc._holder_at("origin/main", "ss-12") == rel


# --- controls: filename-only holders (#590/#634) and frontmatter holders ------------


@pytest.mark.parametrize(
    "text", [NO_FRONTMATTER, FRONTMATTER_NO_ID], ids=["no-frontmatter", "frontmatter-no-id"]
)
@pytest.mark.parametrize("item_id", ["EXP-007", "EXP-7", "exp-007"])
def test_control_filename_only_holder_without_id(world, text: str, item_id: str) -> None:
    rel = "research/experiments/EXP-007-x.md"
    svc = _board_with(world, {rel: text})
    assert svc._holder_at("origin/main", item_id) == rel


def test_control_frontmatter_id_under_unrelated_name(world) -> None:
    rel = "research/measures/notes.md"
    svc = _board_with(world, {rel: "---\nid: EXP-007\ntitle: N\n---\n\n# N\n"})
    assert svc._holder_at("origin/main", "EXP-007") == rel
    assert svc._holder_at("origin/main", "exp-7") == rel


def test_control_nothing_holds_absent_id(world) -> None:
    svc = _board_with(world, {"research/experiments/EXP-007-x.md": NO_FRONTMATTER})
    assert svc._holder_at("origin/main", "EXP-008") is None
