# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #754 — a duplicated parent, or a duplicated epic/voyage item, is refused
before anything is written (the follow-up to #742).

Since #742, update, move, comment and rank refuse an item whose ID (case-folded) is
on more than one board, through `service.refuse_duplicate(item, action)`:
"<ID> is on more than one board (<files>): <action> to it is ambiguous; fix the
duplicate ID first". Two writers still wrote to ONE copy:

(1) The parent link of an HDD child create — `literature create --idea`,
    `hypothesis create --paper`, `experiment create --hypothesis` — both locally
    (hdd `_update_parent` -> `service.link_parent`) and with `--push`
    (`create_item_and_push(parent=...)` -> `_parent_link_edit` / `_parent_link_blob`).
(2) `epic_commands._update_item_related`: `epic add`, `voyage add`, and
    `<group> create --items`.

Decided behaviour ([steer] on #754, bucket 1):

(1) A child create whose parent is duplicated (exact-case or case-folded) is refused
    BEFORE anything is written: no child file, no commit, no push, no
    `_ID_ALLOCATIONS.json` change, a non-zero exit. The message says
    "is on more than one board" and "a parent link", and names both files.
(2) `epic add` / `voyage add` on a duplicated item: exit 1, nothing written, the
    message says "is on more than one board" and "a link". `create --items A,B`
    with A duplicated: the epic is created, A is NOT linked (a warning with the same
    message), B IS linked.

Controls: a non-duplicated parent still links; a non-duplicated item is still added to
an epic; the #742 refusals still hold (their own module).

Harnesses: the #645/#674/#750 real-git World (bare origin, clone A) for the parent
links, and the #742/#576 two-board repo (nautical `work/` + hdd `research/`) for the
epic/voyage links, with EXP-4's duplicate on the research board.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _flat,
    _git,
    _ok,
    _refused,
    repo,
)
from tests.issues.test_576_cli_update_deps import (
    invoke as invoke_repo,
)
from tests.issues.test_585_create_push_loop import World, git, porcelain
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import (
    KINDS,
    Kind,
    frontmatter_id,
    links,
    seed_on_origin,
    turtle_block,
)
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    flat,
    world,
)
from tests.issues.test_742_duplicate_refused_on_every_write import (
    _dup_rel,
    _write_dup,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

PARENT_ID = {
    "literature": "IDEA-R-001",
    "hypothesis": "PAPER-130",
    "experiment": "H130.1",
}
PARENT_NEEDLES = ("is on more than one board", "a parent link")
LINK_NEEDLES = ("is on more than one board", "a link")
ALLOC = ".kanban/_ID_ALLOCATIONS.json"


# --- (1) parent-link harness ----------------------------------------------------------


def _service(world: World) -> KanbanService:
    config_mod._theme_cache.clear()
    svc = KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)
    svc.scan()
    return svc


def _dup_parent(world: World, monkeypatch, kind: Kind, *, case_folded: bool) -> str:
    """Seed the kind's parent on origin, then add a second copy of it (id lower-cased
    when case_folded) in another HDD directory, committed AND pushed so the local
    checkout and origin/main agree. Returns the copy's repo-relative path."""
    seed_on_origin(world, monkeypatch, kind)
    parent_id = PARENT_ID[kind.name]
    src = world.a / kind.parent_rel
    # another directory of the same board: the ID, not the path, is what collides
    other = "measures" if "/measures/" not in kind.parent_rel else "papers"
    dest = (
        world.a / "research" / other / f"{parent_id.lower() if case_folded else parent_id}-copy.md"
    )
    shutil.copyfile(src, dest)
    if case_folded:
        text = dest.read_text()
        assert f"id: {parent_id}" in text, text
        dest.write_text(text.replace(f"id: {parent_id}", f"id: {parent_id.lower()}", 1))
    git(world.a, "add", "-A")
    git(world.a, "commit", "-q", "-m", f"duplicate {parent_id}")
    git(world.a, "push", "-q", "origin", "main")
    rel = dest.relative_to(world.a).as_posix()
    # sanity: the scan sees the duplicate (as #742's refusals do)
    dups = _service(world).duplicate_ids
    assert parent_id.upper() in {k.upper() for k in dups}, (
        f"the fixture's copy of {parent_id} is not a duplicate to the scan: {dups}"
    )
    config_mod._theme_cache.clear()
    return rel


def _tree(world: World) -> dict[str, bytes]:
    """Every file in clone A outside .git (item files, config, _ID_ALLOCATIONS.json)."""
    return {
        p.relative_to(world.a).as_posix(): p.read_bytes()
        for p in world.a.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(world.a).parts
    }


def _has(needle: str, text: str) -> bool:
    """`needle` in `text`, also when rich wrapped a long path mid-word."""
    return needle in text or "".join(needle.split()) in "".join(text.split())


def _argv(kind: Kind, push: bool) -> list[str]:
    return list(kind.argv) if push else [a for a in kind.argv if a != "--push"]


PARENT_CASES = [
    pytest.param(
        kind,
        push,
        folded,
        id=f"{kind.name}-{'push' if push else 'local'}-{'case-folded' if folded else 'exact-case'}",
    )
    for kind in KINDS
    for push in (False, True)
    for folded in (False, True)
]


@pytest.mark.parametrize(("kind", "push", "case_folded"), PARENT_CASES)
def test_child_create_refuses_duplicated_parent(
    world, monkeypatch, kind: Kind, push: bool, case_folded: bool
) -> None:
    dup_rel = _dup_parent(world, monkeypatch, kind, case_folded=case_folded)
    before, head, remote = _tree(world), git(world.a, "rev-parse", "HEAD"), world.remote_sha()

    result = invoke(world, monkeypatch, _argv(kind, push))
    out = flat(result)

    assert result.exit_code != 0, f"a create under a duplicated parent was not refused:\n{out}"
    assert "Traceback" not in out, out
    for needle in (*PARENT_NEEDLES, kind.parent_rel, dup_rel):
        assert _has(needle, out), f"{needle!r} not in output:\n{out}"
    assert PARENT_ID[kind.name].upper() in out.upper(), out
    after = _tree(world)
    new = sorted(set(after) - set(before))
    assert not new, f"files written by a refused create: {new}"
    assert after == before, "a refused create changed a file (parent, copy or allocation)"
    assert after.get(ALLOC) == before.get(ALLOC), "the refused create allocated an ID"
    assert git(world.a, "rev-parse", "HEAD") == head, "a refused create committed"
    assert world.remote_sha() == remote, "a refused create pushed"
    assert porcelain(world.a) == [], porcelain(world.a)


@pytest.mark.parametrize("kind", KINDS, ids=repr)
@pytest.mark.parametrize("case_folded", [False, True], ids=["exact-case", "case-folded"])
def test_link_parent_refuses_duplicated_parent(world, monkeypatch, kind, case_folded) -> None:
    """The service writer itself refuses (raises), writing neither copy."""
    dup_rel = _dup_parent(world, monkeypatch, kind, case_folded=case_folded)
    before = _tree(world)
    svc = _service(world)
    with pytest.raises(ValueError) as exc:
        svc.link_parent(PARENT_ID[kind.name], kind.name, "CHILD-754")
    msg = " ".join(str(exc.value).split())
    for needle in (*PARENT_NEEDLES, kind.parent_rel, dup_rel):
        assert _has(needle, msg), f"{needle!r} not in {msg!r}"
    assert _tree(world) == before, "link_parent wrote a copy of the duplicated parent"


# controls: a parent that is NOT duplicated still gets its link (green before and after)


@pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_control_non_duplicated_parent_still_links(world, monkeypatch, kind, push) -> None:
    seed_on_origin(world, monkeypatch, kind)
    remote = world.remote_sha()
    result = invoke(world, monkeypatch, _argv(kind, push))
    out = flat(result)
    assert result.exit_code == 0, out
    assert f"Updated {PARENT_ID[kind.name]} with inverse reference" in out, out
    if push:
        assert world.remote_sha() != remote, "the --push create did not land"
        block = turtle_block(world.remote_show(kind.parent_rel))
    else:
        block = turtle_block((world.a / kind.parent_rel).read_text())
    children = sorted((world.a / kind.child_dir).rglob("*.md"))
    assert children, f"no child file under {kind.child_dir}:\n{out}"
    child_id = frontmatter_id(children[0].read_text())
    assert links(block, kind, child_id), f"{kind.parent_rel} does not link {child_id}:\n{block}"


# --- (2) epic / voyage links on the #742 two-board repo -------------------------------

ITEM = "EXP-4"  # duplicated on the research board by #742's _write_dup
VOYAGE = "VOY-001"


def _voyage(repo: Repo) -> None:
    """A voyage on the nautical board (hand-written, committed)."""
    path = repo.root / "work" / "voyages" / f"{VOYAGE}-a-voyage.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: {VOYAGE}\ntitle: "A voyage"\ntype: voyage\nstatus: ready\n'
        "priority: medium\nrelated: []\n---\n\n# A voyage\n",
        encoding="utf-8",
    )
    repo.commit("voyage")
    ids = {i.id for i in repo.service().get_items()}
    assert VOYAGE in ids, ids


@pytest.fixture(params=[False, True], ids=["exact-case", "case-folded"])
def dup(request: pytest.FixtureRequest, repo: Repo) -> Path:
    """EXP-4 duplicated on the research board, plus a voyage; committed (clean tree)."""
    path = _write_dup(repo, case_folded=request.param)
    repo.commit("duplicate EXP-4")
    _voyage(repo)
    return path


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_group_add_refuses_duplicated_item(repo: Repo, dup: Path, group: str) -> None:
    _refused(
        repo,
        [group, "add", VOYAGE, ITEM],
        ITEM,
        repo.rel(ITEM),
        _dup_rel(repo, dup),
        *LINK_NEEDLES,
    )


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_group_add_refuses_case_folded_copy_by_its_own_id(repo: Repo, group: str) -> None:
    """`exp-4` names the case-folded copy exactly; it is still the duplicated ID."""
    copy = _write_dup(repo, case_folded=True)
    repo.commit("duplicate EXP-4 as exp-4")
    _voyage(repo)
    _refused(
        repo,
        [group, "add", VOYAGE, ITEM.lower()],
        repo.rel(ITEM),
        _dup_rel(repo, copy),
        *LINK_NEEDLES,
    )


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_group_create_items_skips_duplicated_item(repo: Repo, dup: Path, group: str) -> None:
    orig = repo.path(ITEM).read_bytes()
    copy = dup.read_bytes()
    result = invoke_repo([group, "create", "New voyage 754", "--items", f"{ITEM},EXP-5"])
    out = _flat(result.output)
    assert result.exit_code == 0, f"exit {result.exit_code}: {result.exception!r}\n{out}"
    assert "Created" in out, out
    new = [
        p
        for p in (repo.root / "work").rglob("*.md")
        if "New voyage 754" in p.read_text(encoding="utf-8")
    ]
    assert len(new) == 1, f"the {group} was not created: {new}\n{out}"
    # the duplicated item: warned, neither copy linked
    for needle in (ITEM, repo.rel(ITEM), _dup_rel(repo, dup), *LINK_NEEDLES):
        assert _has(needle, out), f"{needle!r} not in output: {out}"
    assert repo.path(ITEM).read_bytes() == orig, f"{ITEM}'s board copy was linked"
    assert dup.read_bytes() == copy, f"{ITEM}'s duplicate copy was linked"
    assert f"Linked {ITEM}" not in out, out
    # the other item: linked
    related = repo.fm("EXP-5").get("related") or []
    assert len(related) == 1 and str(related[0]).startswith("VOY-"), related
    assert "Linked EXP-5" in out, out


# controls (green before and after)


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_control_group_add_non_duplicated_item(repo: Repo, dup: Path, group: str) -> None:
    result = _ok([group, "add", VOYAGE, "EXP-5"])
    assert f"Linked EXP-5 → {VOYAGE}" in _flat(result.output), result.output
    assert repo.fm("EXP-5").get("related") == [VOYAGE], repo.fm("EXP-5")


def test_control_update_still_refuses_duplicated_id(repo: Repo, dup: Path) -> None:
    _refused(
        repo,
        ["update", ITEM, "--priority", "high"],
        ITEM,
        "is on more than one board",
        "ambiguous",
    )


def test_control_fixture_tree_clean(repo: Repo, dup: Path) -> None:
    assert not _git(repo.root, "status", "--porcelain").strip()
