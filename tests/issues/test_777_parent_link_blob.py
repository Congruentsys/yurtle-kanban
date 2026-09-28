# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issues #777, #796 and #819 — ``_parent_link_blob``, the parent link on origin.

``KanbanService._parent_link_blob(base, parent_id, child_type, child_id)`` builds the
parent's inverse link for ``create --push`` (#645). Three follow-ups, fixed together
([steer] on #777/#796/#819, bucket 1):

#777 — the relation first. It looked for the parent before checking the child type's
    relation, so a child type with no inverse relation got 'missing' (parent nowhere)
    or ``_CasRefusedError`` (parent local but not on origin), where the local path
    (``_parent_link_edit``, ``parent_link_state``, #766) answers 'no-relation'. The
    CLI can't reach it (all three HDD child types have a relation), so these tests
    call ``_parent_link_blob`` directly with a made-up child type, 'widget'.

#796 — one read. It ran ``_holders_at(base, …)`` and then ``_holder_at(base, …)``,
    each of which runs ``_ids_at`` (ls-tree plus grep over origin's tree). One
    ``_ids_at`` must feed both: exactly one call per parent-link build.

#819 — id-bearing only. With no file whose frontmatter ``id:`` is the parent, the
    push path took an id-less file NAMED after it (the #590/#634 filename fallback in
    ``_holder_at``), e.g. ``research/papers/PAPER-130-OUTLINE.md``, and wrote the
    inverse link into it. The local path calls that parent missing (the board names
    the outline ``PAPER_130_OUTLINE``). So on the push path too the parent is
    'missing': no link, the outline byte-identical, and the create goes ahead as the
    local one does ("is not on any board", exit 0, the child created). The filename
    fallback stays for the explicit-ID collision check (control).

RED today: every #777, #796 and #819 test below (not the ``control`` ones).

Reuses the #585/#645/#674/#754/#788 real-git harness.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import World, git, output_of
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import (
    HYP,
    KINDS,
    PAPER,
    Kind,
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
from yurtle_kanban.service import KanbanService, _CasRefusedError

PARENT = "PAPER-130"
BASE = "origin/main"
WIDGET = "widget"  # a child type with no inverse relation
CHILD = "W-1"

PARENT_ID = {"literature": "IDEA-R-001", "hypothesis": PARENT, "experiment": "H130.1"}
CHILD_ID = {"literature": "LIT-099", "hypothesis": "H130.9", "experiment": "EXP-099"}

OUTLINE = "research/papers/PAPER-130-OUTLINE.md"
MISSING_LINE = "PAPER-130 is not on any board"

# an id-less outline named after the parent, with a turtle block a link would go into
TURTLE = (
    "```turtle\n"
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "\n"
    "<#PAPER-130> a paper:Paper ;\n"
    '    rdfs:label "PAPER-130 outline" .\n'
    "```\n"
)
OUTLINES = {
    "no-frontmatter": f"# PAPER-130 outline\n\nDraft notes.\n\n{TURTLE}",
    "frontmatter-no-id": (
        '---\ntitle: "PAPER-130 outline"\nauthor: someone\n---\n\n'
        f"# Outline\n\nDraft notes.\n\n{TURTLE}"
    ),
}


# --- harness --------------------------------------------------------------------------


def _fresh(world: World) -> KanbanService:
    config_mod._theme_cache.clear()
    return _service(world)


class IdsReads:
    """Count ``_ids_at`` calls, and those made inside each ``_parent_link_blob``."""

    def __init__(self) -> None:
        self.total = 0
        self.per_blob: list[int] = []


def count_ids(monkeypatch: pytest.MonkeyPatch) -> IdsReads:
    reads = IdsReads()
    orig_ids = KanbanService._ids_at
    orig_blob = KanbanService._parent_link_blob

    def ids_at(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        reads.total += 1
        return orig_ids(self, *args, **kwargs)

    def blob(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        before = reads.total
        try:
            return orig_blob(self, *args, **kwargs)
        finally:
            reads.per_blob.append(reads.total - before)

    monkeypatch.setattr(KanbanService, "_ids_at", ids_at)
    monkeypatch.setattr(KanbanService, "_parent_link_blob", blob)
    return reads


def _local_paper(world: World, monkeypatch) -> None:
    """PAPER-130 created and committed locally, never pushed."""
    hdd(world)
    result = invoke(world, monkeypatch, ["paper", "create", "130", "A paper"])
    assert result.exit_code == 0, output_of(result)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-q", "-m", "local paper, not pushed")
    assert PAPER not in world.remote_files()
    git(world.a, "fetch", "-q", "origin")
    config_mod._theme_cache.clear()


def _dup_on_origin(world: World, monkeypatch) -> None:
    """PAPER-130 on origin in two files (the second in another HDD dir)."""
    seed_on_origin(world, monkeypatch, HYP)
    text = world.remote_show(PAPER)
    b_push(world, {"research/ideas/PAPER-130-copy.md": text})
    git(world.a, "fetch", "-q", "origin")
    config_mod._theme_cache.clear()


def _outline_board(world: World, text: str) -> None:
    """An HDD board whose only PAPER-130-named file is the id-less outline, on
    origin AND in A's checkout (so the local and push paths see one board)."""
    hdd(world)
    b_push(world, {OUTLINE: text})
    git(world.a, "pull", "-q", "origin", "main")
    assert (world.a / OUTLINE).read_text() == text
    config_mod._theme_cache.clear()


def _reset(world: World) -> None:
    git(world.a, "reset", "-q", "--hard", "origin/main")
    git(world.a, "clean", "-q", "-fd")
    config_mod._theme_cache.clear()


def _hyp_files(root: Path) -> list[str]:
    d = root / "research" / "hypotheses"
    return sorted(p.name for p in d.glob("*.md")) if d.exists() else []


# --- #777: the relation is checked before the parent is looked up ---------------------


def test_777_no_relation_parent_nowhere(world) -> None:
    hdd(world)
    git(world.a, "fetch", "-q", "origin")
    svc = _fresh(world)
    assert svc.get_item(PARENT) is None
    assert svc._parent_link_blob(BASE, PARENT, WIDGET, CHILD) == ({}, "no-relation"), (
        "a child type with no relation is 'no-relation' whether or not the parent exists "
        "(#766's order), not 'missing'"
    )


def test_777_no_relation_parent_local_not_on_origin(world, monkeypatch) -> None:
    _local_paper(world, monkeypatch)
    svc = _fresh(world)
    assert svc.get_item(PARENT) is not None
    try:
        got = svc._parent_link_blob(BASE, PARENT, WIDGET, CHILD)
    except _CasRefusedError as e:
        pytest.fail(f"a child type with no relation was refused for a local-only parent: {e}")
    assert got == ({}, "no-relation"), got


def test_777_no_relation_parent_duplicated_on_origin(world, monkeypatch) -> None:
    """Relation first: with no relation there is no parent to look up, so origin's
    duplicate of it is never reached (the local `_parent_link_edit` order)."""
    _dup_on_origin(world, monkeypatch)
    svc = _fresh(world)
    try:
        got = svc._parent_link_blob(BASE, PARENT, WIDGET, CHILD)
    except _CasRefusedError as e:
        pytest.fail(f"a child type with no relation looked up the parent: {e}")
    assert got == ({}, "no-relation"), got


def test_777_control_no_relation_parent_on_origin(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    git(world.a, "fetch", "-q", "origin")
    assert _fresh(world)._parent_link_blob(BASE, PARENT, WIDGET, CHILD) == ({}, "no-relation")


def test_777_control_local_path_says_no_relation(world, monkeypatch) -> None:
    """The local answers #777 aligns to (#766): green before and after."""
    _local_paper(world, monkeypatch)
    svc = _fresh(world)
    assert svc.parent_link_state(PARENT, WIDGET, CHILD) == "no-relation"
    assert svc.parent_link_state("PAPER-999", WIDGET, CHILD) == "no-relation"


def test_777_control_known_type_parent_local_only_refused(world, monkeypatch) -> None:
    """A child type WITH a relation still refuses a parent origin lacks (#645)."""
    _local_paper(world, monkeypatch)
    with pytest.raises(_CasRefusedError, match="not on origin"):
        _fresh(world)._parent_link_blob(BASE, PARENT, "hypothesis", "H130.9")


def test_777_control_known_type_parent_nowhere_missing(world) -> None:
    hdd(world)
    git(world.a, "fetch", "-q", "origin")
    assert _fresh(world)._parent_link_blob(BASE, PARENT, "hypothesis", "H130.9") == (
        {}, "missing"
    )


# --- #796: origin's ids are read once per parent-link build ---------------------------


@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_796_one_ids_read_per_blob(world, monkeypatch, kind: Kind) -> None:
    seed_on_origin(world, monkeypatch, kind)
    git(world.a, "fetch", "-q", "origin")
    svc = _fresh(world)
    reads = count_ids(monkeypatch)
    linked, state = svc._parent_link_blob(
        BASE, PARENT_ID[kind.name], kind.name, CHILD_ID[kind.name]
    )
    assert state is None and Path(kind.parent_rel) in linked, (linked, state)
    assert reads.per_blob == [1], (
        f"_parent_link_blob read origin's ids {reads.per_blob[0]} times; one "
        "_ids_at must feed both the holder lookup and the duplicate count"
    )


def test_796_one_ids_read_parent_missing(world, monkeypatch) -> None:
    hdd(world)
    git(world.a, "fetch", "-q", "origin")
    svc = _fresh(world)
    reads = count_ids(monkeypatch)
    assert svc._parent_link_blob(BASE, PARENT, "hypothesis", "H130.9") == ({}, "missing")
    assert reads.per_blob == [1], reads.per_blob


def test_796_one_ids_read_per_build_via_cli(world, monkeypatch) -> None:
    """`hypothesis create --paper 130 --push`: each build's parent link reads once."""
    seed_on_origin(world, monkeypatch, HYP)
    base = world.remote_sha()
    reads = count_ids(monkeypatch)
    result = invoke(world, monkeypatch, list(HYP.argv))
    assert result.exit_code == 0, flat(result)
    assert_one_commit_with_link(world, HYP, base)
    assert reads.per_blob and all(n == 1 for n in reads.per_blob), (
        f"_ids_at calls per parent-link build: {reads.per_blob}"
    )


# --- #819: a parent link goes only into a file whose `id:` is the parent --------------


@pytest.mark.parametrize("text", list(OUTLINES.values()), ids=list(OUTLINES))
def test_819_blob_outline_is_missing(world, text: str) -> None:
    _outline_board(world, text)
    got = _fresh(world)._parent_link_blob(BASE, PARENT, "hypothesis", "H130.9")
    assert got == ({}, "missing"), (
        f"an id-less outline named after {PARENT} was taken as the parent: {got}"
    )


@pytest.mark.parametrize("text", list(OUTLINES.values()), ids=list(OUTLINES))
def test_819_push_matches_local_and_leaves_outline(world, monkeypatch, text: str) -> None:
    _outline_board(world, text)

    # the local path, on the same board: the parent is missing, the child is made
    local = invoke(world, monkeypatch, [a for a in HYP.argv if a != "--push"])
    local_out = flat(local)
    assert local.exit_code == 0, local_out
    assert MISSING_LINE in local_out, local_out
    assert (world.a / OUTLINE).read_text() == text
    assert len(_hyp_files(world.a)) == 1, _hyp_files(world.a)
    _reset(world)

    # the push path must answer the same way
    base = world.remote_sha()
    pushed = invoke(world, monkeypatch, list(HYP.argv))
    out = flat(pushed)
    assert pushed.exit_code == local.exit_code == 0, out
    assert world.remote_show(OUTLINE) == text, (
        f"--push wrote the parent link into the id-less outline {OUTLINE}:\n"
        + git(world.remote, "diff", base, "main", "--", OUTLINE)
    )
    assert MISSING_LINE in out, f"--push did not call {PARENT} missing:\n{out}"
    new = git(world.remote, "rev-list", f"{base}..main").split()
    assert len(new) == 1, f"the create did not land as one commit: {new}"
    files = set(git(world.remote, "show", "--name-only", "--format=", new[0]).split())
    children = [f for f in files if f.startswith(HYP.child_dir) and f.endswith(".md")]
    assert len(children) == 1, files
    assert files <= {children[0], ".kanban/_ID_ALLOCATIONS.json"}, (
        f"the create's commit touched more than the child and the lock: {sorted(files)}"
    )


@pytest.mark.parametrize("text", list(OUTLINES.values()), ids=list(OUTLINES))
def test_819_outline_byte_identical(world, monkeypatch, text: str) -> None:
    _outline_board(world, text)
    before = git(world.remote, "rev-parse", f"main:{OUTLINE}").strip()
    invoke(world, monkeypatch, list(HYP.argv))
    assert git(world.remote, "rev-parse", f"main:{OUTLINE}").strip() == before
    assert (world.a / OUTLINE).read_bytes() == text.encode()


@pytest.mark.parametrize("text", list(OUTLINES.values()), ids=list(OUTLINES))
def test_819_control_local_path_calls_outline_missing(world, text: str) -> None:
    _outline_board(world, text)
    svc = _fresh(world)
    assert svc.parent_link_state(PARENT, "hypothesis", "H130.9") == "missing"


@pytest.mark.parametrize("text", list(OUTLINES.values()), ids=list(OUTLINES))
def test_819_control_explicit_id_named_by_outline_refused(
    world, monkeypatch, text: str
) -> None:
    """The filename fallback stays for the explicit-ID collision check (#590/#634):
    a --push create whose --id an id-less file is named after is refused."""
    _outline_board(world, text)
    base = world.remote_sha()
    result = invoke(
        world, monkeypatch, ["paper", "create", "130", "Another paper", "--push"]
    )
    out = flat(result)
    assert result.exit_code != 0, f"an id named by {OUTLINE} was not refused:\n{out}"
    assert "already taken" in out and OUTLINE in out, out
    assert world.remote_sha() == base
    assert _fresh(world)._holder_at(BASE, PARENT) == OUTLINE


def test_819_control_real_parent_beside_outline_links_real(world, monkeypatch) -> None:
    """With a file whose id: IS the parent, the link goes there, not the outline."""
    seed_on_origin(world, monkeypatch, HYP)
    text = OUTLINES["frontmatter-no-id"]
    b_push(world, {OUTLINE: text})
    git(world.a, "pull", "-q", "origin", "main")
    config_mod._theme_cache.clear()
    base = world.remote_sha()
    result = invoke(world, monkeypatch, list(HYP.argv))
    assert result.exit_code == 0, flat(result)
    assert_one_commit_with_link(world, HYP, base)
    assert world.remote_show(OUTLINE) == text
