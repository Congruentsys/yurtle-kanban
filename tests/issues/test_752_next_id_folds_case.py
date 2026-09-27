"""Issue #752 — the next-id allocator folds case in every source it counts.

The [steer] on #752 (bucket 1, from the #732 ruling: an id is one id whatever its
case) is the spec. ``_get_next_id_number(prefix)`` is the max over four sources, and
each one folds case:

(a) scanned frontmatter ids (``self._items``): ``id: exp-12`` in ``thing.md``;
(b) filename stems: ``exp-12-thing.md`` whose frontmatter has no id;
(c) origin/<default> (``_next_id_number_at``): origin holds ``exp-12`` (as a
    frontmatter id, a stem, or an allocation record) and the checkout does not;
(d) ``.kanban/_ID_ALLOCATIONS.json`` records: ``{"id": "exp-12", ...}``.

With ``exp-12`` in any single source and nothing higher anywhere, the next EXP is 13:
through the service, through ``next-id EXP --no-sync``, and through a local
``create expedition``.

Controls (green before and after): upper-case ids are counted as before; a longer
prefix sharing the letters (``EXPR-50``, ``expr-50``) is not counted for EXP; a
multi-segment prefix (``IDEA-R-003``) still counts; the paper-scoped ``H130.`` space
stays apart from the dashed ``H-`` space, and a lower-case ``h130.4`` does not count
for ``H``. (A lower-case ``h130.4`` counting for ``H130.``, and ``idea-r-003`` for
``IDEA-R``, are part of the fold and are red.)

Reuses the #585/#590/#634/#641 real-git harness: ``World`` (bare remote, clone A under
test, rival clone B) and ``b_push`` (B pushes files and allocation records to
origin/main); A then fetches, so the rival is in ``refs/remotes/origin/main`` only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import EXP_DIR, World, git, output_of
from tests.issues.test_590_next_id_and_hdd_ids import ALLOC, b_push
from tests.issues.test_603_push_failure_messages import HDD_DIRS, invoke, reconfigure
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

LOCAL_SOURCES = ["frontmatter", "stem", "allocation"]
ORIGIN_SOURCES = ["origin-frontmatter", "origin-stem", "origin-allocation"]
ALL_SOURCES = LOCAL_SOURCES + ORIGIN_SOURCES

HYP_DIR = "research/hypotheses"
IDEA_DIR = "research/ideas"


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def service(world: World) -> KanbanService:
    return KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)


def hdd(world: World) -> None:
    reconfigure(world, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])


def item_text(item_id: str | None, item_type: str = "expedition") -> str:
    id_line = f"id: {item_id}\n" if item_id is not None else ""
    return (
        f'---\n{id_line}title: "Thing"\ntype: {item_type}\nstatus: backlog\n---\n\n# Thing\n'
    )


def seed(
    world: World, source: str, item_id: str, under: str = EXP_DIR, item_type: str = "expedition"
) -> None:
    """Put `item_id` in exactly one source.

    Local sources are written into A's working tree (not pushed). Origin sources are
    pushed by B and fetched by A, so they sit in refs/remotes/origin/main only.
    The frontmatter source uses a neutral filename (`thing.md`) so the stem source
    does not see it; the stem source's frontmatter carries no id.
    """
    space, number = KanbanService._id_space(item_id) or (item_id, 0)
    stem_name = f"{item_id}-thing.md"
    if source == "frontmatter":
        path = world.a / under / "thing.md"
        path.write_text(item_text(item_id, item_type))
    elif source == "stem":
        path = world.a / under / stem_name
        path.write_text(item_text(None, item_type))
    elif source == "allocation":
        lock = world.a / ALLOC
        records = json.loads(lock.read_text()) if lock.exists() else []
        records.append({"id": item_id, "prefix": space, "number": number})
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps(records, indent=2))
    elif source == "origin-frontmatter":
        b_push(world, {f"{under}/thing.md": item_text(item_id, item_type)})
    elif source == "origin-stem":
        b_push(world, {f"{under}/{stem_name}": item_text(None, item_type)})
    elif source == "origin-allocation":
        b_push(world, {}, [{"id": item_id, "prefix": space, "number": number}])
    else:  # pragma: no cover
        raise ValueError(source)
    if source.startswith("origin-"):
        git(world.a, "fetch", "origin")
        # precondition: the checkout itself does not hold the id
        for p in (world.a / under).rglob("*.md"):
            assert item_id not in p.read_text() and item_id not in p.name, p
        lock = world.a / ALLOC
        assert not lock.exists() or item_id not in lock.read_text()


def allocated_id(out: str) -> str | None:
    m = re.search(r'"id":\s*"([^"]+)"', out)
    return m.group(1) if m else None


# --- the four sources fold case (RED) --------------------------------------------------------


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_lowercase_id_counts_in_every_source(world, source) -> None:
    seed(world, source, "exp-12")
    assert service(world)._get_next_id_number("EXP") == 13, (
        f"`exp-12` in the {source} source was not counted: EXP-012 would be re-issued"
    )


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_cli_next_id_no_sync_counts_lowercase(world, monkeypatch, source) -> None:
    seed(world, source, "exp-12")
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    out = output_of(result)
    assert result.exit_code == 0, out
    assert allocated_id(out) == "EXP-013", f"`exp-12` ({source}) not counted: {out}"


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_cli_create_counts_lowercase(world, monkeypatch, source) -> None:
    seed(world, source, "exp-12")
    result = invoke(world, monkeypatch, ["create", "expedition", "Alpha From A"])
    out = output_of(result)
    assert result.exit_code == 0, out
    made = sorted(p.name for p in (world.a / EXP_DIR).glob("EXP-*.md"))
    assert len(made) == 1 and made[0].startswith("EXP-013-"), (
        f"`exp-12` ({source}) not counted by create: {made} {out}"
    )


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_lowercase_paper_scoped_id_counts_in_its_space(world, source) -> None:
    hdd(world)
    seed(world, source, "h130.4", under=HYP_DIR, item_type="hypothesis")
    assert service(world)._get_next_id_number("H130.") == 5


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_lowercase_multi_segment_id_counts(world, source) -> None:
    hdd(world)
    seed(world, source, "idea-r-003", under=IDEA_DIR, item_type="idea")
    assert service(world)._get_next_id_number("IDEA-R") == 4


# --- controls (GREEN before and after) -------------------------------------------------------


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_uppercase_counts_as_before(world, source) -> None:
    seed(world, source, "EXP-12")
    assert service(world)._get_next_id_number("EXP") == 13


@pytest.mark.parametrize("source", ALL_SOURCES)
@pytest.mark.parametrize("other", ["EXPR-50", "expr-50"])
def test_control_longer_prefix_is_not_counted(world, source, other) -> None:
    seed(world, source, other)
    assert service(world)._get_next_id_number("EXP") == 1, (
        f"{other} ({source}) was counted in the EXP space"
    )


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_multi_segment_prefix_counts(world, source) -> None:
    hdd(world)
    seed(world, source, "IDEA-R-003", under=IDEA_DIR, item_type="idea")
    assert service(world)._get_next_id_number("IDEA-R") == 4


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_paper_scoped_uppercase_counts_in_its_space(world, source) -> None:
    hdd(world)
    seed(world, source, "H130.4", under=HYP_DIR, item_type="hypothesis")
    assert service(world)._get_next_id_number("H130.") == 5


@pytest.mark.parametrize("source", ALL_SOURCES)
@pytest.mark.parametrize("paper_id", ["H130.4", "h130.4"])
def test_control_paper_scoped_id_not_counted_for_dashed_h(world, source, paper_id) -> None:
    hdd(world)
    seed(world, source, paper_id, under=HYP_DIR, item_type="hypothesis")
    svc = service(world)
    assert svc._get_next_id_number("H") == 1, f"{paper_id} ({source}) counted in H-"
    assert svc.get_next_unparented_hypothesis_id() == "H-001"


def test_control_dashed_h_not_counted_for_paper_space(world) -> None:
    hdd(world)
    seed(world, "frontmatter", "h-007", under=HYP_DIR, item_type="hypothesis")
    assert service(world)._get_next_id_number("H130.") == 1
