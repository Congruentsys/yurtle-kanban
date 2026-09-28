"""Issue #808 — non-ASCII paths reach ``_ids_at`` raw.

``git ls-tree`` prints a path holding a byte above 0x7f C-quoted and escaped
(``"d/\\303\\237-12-x.md"``) unless ``-z`` (or ``core.quotePath=false``) is given. The
quoted name ends ``.md"``, not ``.md``, so ``_ids_at`` dropped every non-ASCII filename:
the push-time holder checks (``_holder_at`` / ``_holders_at``), next-id counting at
origin (``_next_id_number_at``) and ``_items_at`` (the other ls-tree whose names are
parsed) never saw it.

The [steer] on #808 (bucket 1) is the spec: every ls-tree whose names are parsed runs
with ``-z`` and splits on NUL, so non-ASCII paths come back raw. ``_ids_at``'s
``git grep`` already passes ``-z``; with ``-z`` git grep prints paths unquoted, so the
frontmatter-id half already sees such files — the tests here that ride only on it are
green guards, not reds.

Reds:
(1) ``_ids_at("origin/main")`` lists ``ß-12-x.md``, ``exp-é-7.md``, ``日本-3.md`` raw.
(2) a filename-only holder ``EXP-012-café.md``: ``_holder_at`` finds it and
    ``create_item_and_push(item_id="EXP-012")`` (the explicit-id
    ``--push`` path) is refused naming it.
(3) origin holds only ``EXP-020-naïve.md`` (no frontmatter id): the next EXP is 21.
(5) ``_items_at`` loads an item whose filename is non-ASCII.

Green guards (git grep -z is already raw):
(2b) ``EXP-012-café.md`` with ``id: EXP-012``: held, refused.
(4) ``notes-é.md`` with ``id: EXP-030``: listed in ``_ids_at``'s ids raw, next EXP is
    31, held for EXP-030.

Controls: the same checks over ASCII names pass before and after.

Reuses the #585/#590/#634/#752/#764 real-git harness: ``World`` (bare remote, clone A
under test, rival clone B), ``b_push`` (B pushes to origin/main).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.test_634_explicit_ids_on_base import (
    assert_untouched,
    hdd,
    service,
    to_feature_branch,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItemType

IDEA_DIR = "research/ideas"
TITLE_A = "Alpha From A"


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def item_text(item_id: str | None, item_type: str = "expedition") -> str:
    id_line = f"id: {item_id}\n" if item_id is not None else ""
    return f'---\n{id_line}title: "Rival"\ntype: {item_type}\nstatus: backlog\n---\n\n# Rival\n'


def on_origin(world: World, files: dict[str, str]) -> None:
    """B pushes `files` to origin/main; A fetches, so they sit on origin/main only."""
    b_push(world, files)
    git(world.a, "fetch", "origin")


def remote_md(world: World, under: str) -> list[str]:
    """The `.md` paths under `under` on origin's main, raw: `-z`, since the harness's
    `World.remote_files` runs plain ls-tree, which quotes a non-ASCII path (#808)."""
    listed = git(world.remote, "ls-tree", "-r", "-z", "--name-only", "main")
    return [p for p in listed.split("\0") if p.startswith(under) and p.endswith(".md")]


def quoted_by_plain_ls_tree(world: World, path: str) -> bool:
    """Precondition: plain `git ls-tree` quotes `path` (so the test exercises #808)."""
    listed = git(world.a, "ls-tree", "-r", "--name-only", "--full-tree", "origin/main")
    return path not in listed.splitlines()


# --- (1) _ids_at lists non-ASCII names raw (RED) ---------------------------------------------

NON_ASCII_IDEAS = ["ß-12-x.md", "exp-é-7.md", "日本-3.md"]
ASCII_IDEAS = ["sz-12-x.md", "exp-e-7.md", "nihon-3.md"]


@pytest.mark.parametrize("name", NON_ASCII_IDEAS)
def test_ids_at_lists_non_ascii_name_raw(world, name) -> None:
    hdd(world)
    path = f"{IDEA_DIR}/{name}"
    on_origin(world, {path: item_text(None, "idea")})
    assert quoted_by_plain_ls_tree(world, path)

    names, _ = service(world)._ids_at("origin/main")

    assert path in names, f"{path} vanished from _ids_at: {names}"
    assert not any(n.startswith('"') for n in names), f"a quoted name leaked: {names}"


def test_ids_at_lists_all_non_ascii_names_beside_ascii(world) -> None:
    hdd(world)
    files = {f"{IDEA_DIR}/{n}": item_text(None, "idea") for n in NON_ASCII_IDEAS + ASCII_IDEAS}
    on_origin(world, files)

    names, _ = service(world)._ids_at("origin/main")

    missing = sorted(set(files) - set(names))
    assert not missing, f"non-ASCII names vanished from _ids_at: {missing}"


@pytest.mark.parametrize("name", ASCII_IDEAS)
def test_control_ids_at_lists_ascii_name(world, name) -> None:
    hdd(world)
    path = f"{IDEA_DIR}/{name}"
    on_origin(world, {path: item_text(None, "idea")})

    names, _ = service(world)._ids_at("origin/main")

    assert path in names, names


# --- (2) a filename holder with a non-ASCII slug -----------------------------------------------

# (case, file name under EXP_DIR, frontmatter id or None)
STEM_HOLDERS = [("stem-only", "EXP-012-café.md", None)]
FRONTMATTER_HOLDERS = [("stem-and-frontmatter", "EXP-012-café.md", "EXP-012")]
ASCII_HOLDERS = [
    ("ascii-stem-only", "EXP-012-cafe.md", None),
    ("ascii-stem-and-frontmatter", "EXP-012-cafe.md", "EXP-012"),
]


def _holder(world: World, name: str, fid: str | None, item_id: str = "EXP-012") -> str | None:
    on_origin(world, {f"{EXP_DIR}/{name}": item_text(fid)})
    return service(world)._holder_at("origin/main", item_id)


def _create_against(world, name: str, fid: str | None):
    """`create_item_and_push(item_id="EXP-012")` against origin's `name` (the plain
    `create` command takes no `--id`; this is the path `--push` with an explicit id
    rides, as #764's service case)."""
    feat = to_feature_branch(world)
    path = f"{EXP_DIR}/{name}"
    b_push(world, {path: item_text(fid)})
    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, TITLE_A, item_id="EXP-012"
    )
    return result, path, feat


def _assert_refused(world, result, name: str, path: str, feat) -> None:
    msg = result.get("message", "")
    assert result["success"] is False, f"EXP-012 was pushed beside {name}: {result}"
    assert name in msg, f"the refusal does not name {name}: {msg}"
    assert "already taken" in msg, msg
    on_remote = remote_md(world, f"{EXP_DIR}/")
    assert on_remote == [path], f"something was pushed beside {name}: {on_remote}"
    assert_untouched(world, feat)


@pytest.mark.parametrize(("case", "name", "fid"), STEM_HOLDERS, ids=[c[0] for c in STEM_HOLDERS])
def test_holder_at_finds_non_ascii_stem(world, case, name, fid) -> None:
    held = _holder(world, name, fid)
    assert held == f"{EXP_DIR}/{name}", f"EXP-012 not held by {name}: {held!r}"


@pytest.mark.parametrize(("case", "name", "fid"), STEM_HOLDERS, ids=[c[0] for c in STEM_HOLDERS])
def test_create_push_id_refused_by_non_ascii_stem(world, case, name, fid) -> None:
    result, path, feat = _create_against(world, name, fid)
    _assert_refused(world, result, name, path, feat)


@pytest.mark.parametrize(("case", "name", "fid"), FRONTMATTER_HOLDERS,
                         ids=[c[0] for c in FRONTMATTER_HOLDERS])
def test_guard_holder_at_finds_non_ascii_frontmatter_holder(world, case, name, fid) -> None:
    """Green before and after: git grep -z already prints the path raw."""
    held = _holder(world, name, fid)
    assert held == f"{EXP_DIR}/{name}", f"EXP-012 not held by {name}: {held!r}"


@pytest.mark.parametrize(("case", "name", "fid"), FRONTMATTER_HOLDERS,
                         ids=[c[0] for c in FRONTMATTER_HOLDERS])
def test_guard_create_push_id_refused_by_non_ascii_frontmatter(
    world, case, name, fid
) -> None:
    result, path, feat = _create_against(world, name, fid)
    _assert_refused(world, result, name, path, feat)


@pytest.mark.parametrize(("case", "name", "fid"), ASCII_HOLDERS, ids=[c[0] for c in ASCII_HOLDERS])
def test_control_holder_at_ascii(world, case, name, fid) -> None:
    held = _holder(world, name, fid)
    assert held == f"{EXP_DIR}/{name}", held


@pytest.mark.parametrize(("case", "name", "fid"), ASCII_HOLDERS, ids=[c[0] for c in ASCII_HOLDERS])
def test_control_create_push_id_refused_ascii(world, case, name, fid) -> None:
    result, path, feat = _create_against(world, name, fid)
    _assert_refused(world, result, name, path, feat)


# --- (3) next-id counts a non-ASCII filename at origin (RED) ----------------------------------


def test_next_id_counts_non_ascii_stem_at_origin(world) -> None:
    on_origin(world, {f"{EXP_DIR}/EXP-020-naïve.md": item_text(None)})
    svc = service(world)
    assert svc._next_id_number_at("origin/main", "EXP") == 21, (
        "EXP-020-naïve.md was not counted at origin: EXP-020 would be re-issued"
    )


def test_control_next_id_counts_ascii_stem_at_origin(world) -> None:
    on_origin(world, {f"{EXP_DIR}/EXP-020-naive.md": item_text(None)})
    assert service(world)._next_id_number_at("origin/main", "EXP") == 21


# --- (4) a frontmatter id inside a non-ASCII-named file (GREEN guard) -------------------------

NOTES = f"{EXP_DIR}/notes-é.md"


def test_guard_ids_at_frontmatter_path_raw(world) -> None:
    """git grep -z already prints the path raw: the (path, id) pair is exact."""
    on_origin(world, {NOTES: item_text("EXP-030")})
    assert quoted_by_plain_ls_tree(world, NOTES)
    _, ids = service(world)._ids_at("origin/main")
    assert (NOTES, "EXP-030") in ids, ids


def test_ids_at_lists_frontmatter_file_name_raw(world) -> None:
    """RED: the file itself is missing from the names half (ls-tree quoted it)."""
    on_origin(world, {NOTES: item_text("EXP-030")})
    names, _ = service(world)._ids_at("origin/main")
    assert NOTES in names, names


def test_guard_next_id_counts_frontmatter_in_non_ascii_file(world) -> None:
    on_origin(world, {NOTES: item_text("EXP-030")})
    assert service(world)._next_id_number_at("origin/main", "EXP") == 31


def test_guard_holder_at_frontmatter_in_non_ascii_file(world) -> None:
    on_origin(world, {NOTES: item_text("EXP-030")})
    svc = service(world)
    assert svc._holder_at("origin/main", "EXP-030") == NOTES
    assert svc._holders_at("origin/main", "EXP-030") == [NOTES]


def test_control_frontmatter_in_ascii_file(world) -> None:
    notes = f"{EXP_DIR}/notes-e.md"
    on_origin(world, {notes: item_text("EXP-030")})
    svc = service(world)
    assert svc._next_id_number_at("origin/main", "EXP") == 31
    assert svc._holder_at("origin/main", "EXP-030") == notes


# --- (5) the other parsed ls-tree: _items_at (RED) ---------------------------------------------


def test_items_at_loads_non_ascii_named_item(world) -> None:
    on_origin(world, {f"{EXP_DIR}/EXP-040-café.md": item_text("EXP-040")})
    ids = [i.id for i in service(world)._items_at("origin/main", None)]
    assert "EXP-040" in ids, f"EXP-040-café.md vanished from _items_at: {ids}"


def test_control_items_at_loads_ascii_named_item(world) -> None:
    on_origin(world, {f"{EXP_DIR}/EXP-040-cafe.md": item_text("EXP-040")})
    ids = [i.id for i in service(world)._items_at("origin/main", None)]
    assert "EXP-040" in ids, ids
