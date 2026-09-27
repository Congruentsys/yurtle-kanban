# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #754, round 3 — only an item's own frontmatter ``id:`` makes a file a holder.

Round 2 added ``_holders_at(rev, id)``; ``_parent_link_blob`` refuses a parent that
origin holds in more than one file. But ``_holders_at`` also counts FILENAME matches
(a stem equal to the ID, or starting with ID + '-', case-folded), so ``--push`` refuses
valid creates when origin has non-item files named after the parent — drafts and
outlines such as ``PAPER-130-OUTLINE.md`` or ``research/papers/Paper130-x/PAPER-130.md`` — or
a file named ``paper-130-foo.md`` whose frontmatter ``id:`` is a different item.
nusy-product-team has 34 PAPER-* items in this state (round-2 review of PR #778).

The board's rule: an item's ID is its frontmatter ``id:``; a file with no ``id:`` gets
``stem.upper().replace('-', '_')``, which never equals a dashed ID. ``duplicate_ids``
counts IDs parsed that way. The decided fix: a duplicate on origin is counted only
among files whose frontmatter ``id:`` is the parent's ID (case-folded,
``_id_key``-equal), so ``_holders_at`` agrees with ``duplicate_ids``.

(1) Origin holds the real PAPER-130 plus name-only non-item files: ``hypothesis create
    --paper 130 --push`` succeeds — not refused, child created and pushed in one commit.
(2) Origin holds the real PAPER-130 plus ``paper-130-foo.md`` with ``id: H130.9``: the
    same create succeeds.
(3) Unit: with those files on origin, ``_holders_at("origin/main", "PAPER-130")`` is
    exactly the real parent's file.

The parent-link CONTENT is deliberately not asserted: ``_holder_at``'s first-match
lookup may still pick a non-item file for the link (filed separately).

Controls (green before and after): two files whose frontmatter ``id:`` is PAPER-130
(exact or case-folded), under names that do NOT match the ID, still refuse.

Harnesses: the #585/#645/#764 real-git World, ``b_push``, ``seed_on_origin``.
"""

from __future__ import annotations

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import (
    KINDS,
    PAPER,
    commits_since,
    seed_on_origin,
    touched,
)
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    flat,
    world,
)
from tests.issues.test_754_duplicate_parent_and_epic import _service
from yurtle_kanban import config as config_mod

HYP = next(k for k in KINDS if k.name == "hypothesis")
PARENT = "PAPER-130"

NO_FRONTMATTER = "# PAPER-130 outline\n\nDraft notes for the paper.\n"
FRONTMATTER_NO_ID = '---\ntitle: "PAPER-130 outline"\nauthor: someone\n---\n\n# Outline\n'
OTHER_ITEM = (
    '---\nid: H130.9\ntitle: "Another hypothesis"\ntype: hypothesis\nstatus: backlog\n'
    "paper: PAPER-130\n---\n\n# Another hypothesis\n"
)

# name-only non-item files: each set is (id, files)
NAME_ONLY = {
    "outline-no-frontmatter": {"research/papers/PAPER-130-OUTLINE.md": NO_FRONTMATTER},
    "outline-frontmatter-no-id": {"research/papers/PAPER-130-OUTLINE.md": FRONTMATTER_NO_ID},
    "draft-dir-no-id": {"research/papers/Paper130-x/PAPER-130.md": NO_FRONTMATTER},
    "all-name-only": {
        "research/papers/PAPER-130-OUTLINE.md": NO_FRONTMATTER,
        "research/papers/PAPER-130-CRITICAL-REVIEW.md": FRONTMATTER_NO_ID,
        "research/papers/Paper130-x/PAPER-130.md": NO_FRONTMATTER,
    },
}
OTHER_ID = {"research/hypotheses/paper-130-foo.md": OTHER_ITEM}
EVERYTHING = {**NAME_ONLY["all-name-only"], **OTHER_ID}

# the non-item files reach origin from a rival (A behind), or A has them too
WHERE = ["origin-only", "both-boards"]


def _place(world: World, monkeypatch, files: dict[str, str], where: str) -> None:
    """Seed the real PAPER-130 on origin, then a rival pushes `files`; with
    'both-boards' A pulls them as well."""
    seed_on_origin(world, monkeypatch, HYP)
    b_push(world, files)
    remote = world.remote_files()
    for rel in files:
        assert rel in remote, remote
    if where == "both-boards":
        git(world.a, "pull", "-q", "--ff-only", "origin", "main")
        for rel in files:
            assert (world.a / rel).exists(), rel
    # sanity: the board itself sees no duplicate of the parent
    dups = {k.upper() for k in _service(world).duplicate_ids}
    assert PARENT not in dups, f"the fixture made a real duplicate: {dups}"
    config_mod._theme_cache.clear()


def _assert_created_and_pushed(world: World, base: str, result) -> None:
    out = flat(result)
    assert "is on more than one board" not in out, (
        f"--push refused a parent that only ONE item holds:\n{out}"
    )
    assert result.exit_code == 0, out
    new = commits_since(world, base)
    assert len(new) == 1, f"expected ONE commit on origin/main, got {len(new)}:\n{out}"
    files = touched(world, new[0])
    children = [p for p in files if p.startswith(HYP.child_dir) and p.endswith(".md")]
    assert len(children) == 1, f"no single child file in the commit: {sorted(files)}"


# --- (1) name-only non-item files do not make the parent a duplicate ------------------


@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize("files", list(NAME_ONLY.values()), ids=list(NAME_ONLY))
def test_push_ignores_name_only_non_item_files(
    world, monkeypatch, files: dict[str, str], where: str
) -> None:
    _place(world, monkeypatch, files, where)
    base = world.remote_sha()
    result = invoke(world, monkeypatch, list(HYP.argv))
    _assert_created_and_pushed(world, base, result)


# --- (2) a filename match whose own id is another item is not a holder ----------------


@pytest.mark.parametrize("where", WHERE)
def test_push_ignores_file_named_for_parent_holding_other_id(
    world, monkeypatch, where: str
) -> None:
    _place(world, monkeypatch, OTHER_ID, where)
    base = world.remote_sha()
    result = invoke(world, monkeypatch, list(HYP.argv))
    _assert_created_and_pushed(world, base, result)


@pytest.mark.parametrize("where", WHERE)
def test_push_ignores_all_lookalikes_together(world, monkeypatch, where: str) -> None:
    _place(world, monkeypatch, EVERYTHING, where)
    base = world.remote_sha()
    result = invoke(world, monkeypatch, list(HYP.argv))
    _assert_created_and_pushed(world, base, result)


# --- (3) unit: _holders_at agrees with the board --------------------------------------


@pytest.mark.parametrize(
    "files",
    [*NAME_ONLY.values(), OTHER_ID, EVERYTHING],
    ids=[*NAME_ONLY, "other-id", "everything"],
)
def test_holders_at_is_only_the_item(world, monkeypatch, files: dict[str, str]) -> None:
    _place(world, monkeypatch, files, "origin-only")
    git(world.a, "fetch", "-q", "origin")
    svc = _service(world)
    assert svc._holders_at("origin/main", PARENT) == [PAPER]
    assert svc._holders_at("origin/main", PARENT.lower()) == [PAPER]


# --- controls: a real duplicate (by frontmatter id) still refuses ---------------------


@pytest.mark.parametrize("case_folded", [False, True], ids=["exact-case", "case-folded"])
def test_control_real_duplicate_under_unrelated_name_refuses(
    world, monkeypatch, case_folded: bool
) -> None:
    """The copy's filename does not match the ID: only its frontmatter id does."""
    seed_on_origin(world, monkeypatch, HYP)
    text = (world.a / PAPER).read_text()
    assert f"id: {PARENT}" in text, text
    if case_folded:
        text = text.replace(f"id: {PARENT}", f"id: {PARENT.lower()}", 1)
    copy = "research/measures/notes-on-a-paper.md"
    b_push(world, {copy: text})
    config_mod._theme_cache.clear()
    base, head = world.remote_sha(), git(world.a, "rev-parse", "HEAD")

    result = invoke(world, monkeypatch, list(HYP.argv))
    out = flat(result)

    assert result.exit_code != 0, f"--push linked one copy of a duplicated parent:\n{out}"
    assert "Traceback" not in out, out
    assert "is on more than one board" in out, out
    assert PAPER in out and copy in out, out
    assert world.remote_sha() == base, "a refused create pushed"
    assert git(world.a, "rev-parse", "HEAD") == head, "a refused create committed"


def _real_dup_holders(
    world: World, monkeypatch, case_folded: bool, extra: dict[str, str]
) -> tuple[list[str], str]:
    seed_on_origin(world, monkeypatch, HYP)
    text = (world.a / PAPER).read_text()
    if case_folded:
        text = text.replace(f"id: {PARENT}", f"id: {PARENT.lower()}", 1)
    copy = "research/measures/notes-on-a-paper.md"
    b_push(world, {copy: text, **extra})
    git(world.a, "fetch", "-q", "origin")
    config_mod._theme_cache.clear()
    return _service(world)._holders_at("origin/main", PARENT), copy


@pytest.mark.parametrize("case_folded", [False, True], ids=["exact-case", "case-folded"])
def test_control_holders_at_counts_real_duplicate(
    world, monkeypatch, case_folded: bool
) -> None:
    holders, copy = _real_dup_holders(world, monkeypatch, case_folded, {})
    assert sorted(holders) == sorted([PAPER, copy])


@pytest.mark.parametrize("case_folded", [False, True], ids=["exact-case", "case-folded"])
def test_holders_at_real_duplicate_among_lookalikes_is_just_the_two_items(
    world, monkeypatch, case_folded: bool
) -> None:
    """A real duplicate is still reported, naming only the two items, not lookalikes."""
    holders, copy = _real_dup_holders(world, monkeypatch, case_folded, EVERYTHING)
    assert sorted(holders) == sorted([PAPER, copy])
