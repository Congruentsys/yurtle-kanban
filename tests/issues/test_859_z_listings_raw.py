"""Issue #859 — ``-z`` path listings read in text mode turn a lone ``\\r`` in a filename into ``\\n``.

``_git_run`` runs git with ``text=True``: universal newlines, so a lone ``\\r`` (or a
CRLF pair) in NUL-separated ``-z`` output reaches the caller as ``\\n``. A board file
named ``EXP-001\\rx.md`` is listed as ``EXP-001\\nx.md``, a name no file has. Since #830
the ``git grep`` in ``_ids_at`` reads raw, so ``_ids_at``'s two sides now spell the one
file two ways.

The [steer] on #859 (bucket 1) gives the spec: every ``-z`` path listing is read raw
(``text=False``), decoded UTF-8 with ``surrogateescape`` as #830's grep is, and split
on NUL, through one helper.

Reds, one per listing reached (each asserts the exact str: ``\\r`` kept, no ``\\n``):
- ``_ids_at``'s ``ls-tree --name-only``: the name agrees with the grep side's path;
- ``_folder_case_twin``'s ``ls-tree``: a folder named with ``\\r`` is found as a twin,
  and a folder named with ``\\n`` (which the tree does not have) is not;
- ``_git_state``'s ``diff --cached -z``: a staged rename's ``\\r``-named destination
  reads ``changed``, not ``untracked``;
- ``_blob_at``'s ``ls-tree -z``: the ``\\r``-named file's text is read, not None;
- ``_items_at``'s ``ls-tree -z``: the item's path keeps its ``\\r``;
- ``_blobs_at``'s ``ls-tree -z``: a ``\\r``-named file is read, not refused with
  "ls-tree does not list".
Controls (green before and after): the same with a plain name.

Not reached: the ``ls-tree --name-only`` case-twin check inside ``create``'s CAS
builder (service.py ~2247). The path it compares is built from the new id and title,
so it never holds a ``\\r``; the listing's translation can't change its answer through
``create``.

Real git (macOS and Linux allow ``\\r`` in a filename); skips where the filesystem
refuses one. Reuses the #585/#590/#830 real-git harness.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_832_read_blobs_directly import blob_oids
from tests.test_634_explicit_ids_on_base import service
from yurtle_kanban import config as config_mod

CR_NAME = f"{EXP_DIR}/EXP-001\rx.md"
LF_NAME = f"{EXP_DIR}/EXP-001\nx.md"  # what text mode turns CR_NAME into
PLAIN_NAME = f"{EXP_DIR}/EXP-001-x.md"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    probe = tmp_path / "probe\rname"
    try:
        probe.write_text("")
        probe.unlink()
    except OSError:
        pytest.skip("this filesystem refuses a \\r in a filename")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def item(item_id: str = "EXP-001") -> str:
    return "\n".join([
        "---", f"id: {item_id}", 'title: "Odd name"', "type: expedition", "status: backlog",
        "---", "", "# Odd name", "",
    ])


def on_origin(world: World, files: dict[str, str]) -> None:
    """B pushes `files` to origin/main; A fetches, so they sit on origin/main only."""
    b_push(world, files)
    git(world.a, "fetch", "origin")


def assert_exact(got: str, want: str) -> None:
    assert got == want, f"{got!r} != {want!r}"
    if "\r" in want:
        assert "\n" not in got, f"a lone \\r came back as \\n: {got!r}"


# --- _ids_at: ls-tree names vs grep paths ------------------------------------------------------


@pytest.mark.parametrize("rev", ["origin/main", "HEAD"])
def test_ids_at_names_keep_cr(world, rev) -> None:
    on_origin(world, {CR_NAME: item()})
    if rev == "HEAD":
        git(world.a, "reset", "--hard", "origin/main")
    names, ids = service(world)._ids_at(rev)
    assert (CR_NAME, "EXP-001") in ids, f"precondition (#830): grep keeps the \\r: {ids}"
    md = [n for n in names if n.startswith(f"{EXP_DIR}/EXP-001")]
    assert len(md) == 1, names
    assert_exact(md[0], CR_NAME)
    assert md[0] == next(p for p, _ in ids), "ls-tree and grep spell the file two ways"


def test_control_ids_at_plain_name(world) -> None:
    on_origin(world, {PLAIN_NAME: item()})
    names, ids = service(world)._ids_at("origin/main")
    assert PLAIN_NAME in names, names
    assert (PLAIN_NAME, "EXP-001") in ids, ids


# --- _folder_case_twin: the whole-tree ls-tree (`_tree_names`, #903) ---------------------------

CR_FOLDER = "kanban-work/Odd\rDir"


def test_folder_case_twin_finds_cr_folder(world) -> None:
    on_origin(world, {f"{CR_FOLDER}/a.md": item()})
    svc = service(world)
    got = svc._folder_case_twin(
        svc._tree_names("origin/main"), [Path(f"{CR_FOLDER.lower()}/b.md")]
    )
    assert got is not None, "the \\r-named folder's case twin was missed"
    assert_exact(got, CR_FOLDER)


def test_folder_case_twin_invents_no_lf_folder(world) -> None:
    """The tree has `Odd\\rDir` only: `odd\\ndir` is no twin of anything there."""
    on_origin(world, {f"{CR_FOLDER}/a.md": item()})
    svc = service(world)
    got = svc._folder_case_twin(
        svc._tree_names("origin/main"), [Path("kanban-work/odd\ndir/b.md")]
    )
    assert got is None, f"a folder the tree does not have was reported: {got!r}"


def test_control_folder_case_twin_plain(world) -> None:
    on_origin(world, {"kanban-work/OddDir/a.md": item()})
    svc = service(world)
    got = svc._folder_case_twin(svc._tree_names("origin/main"), [Path("kanban-work/odddir/b.md")])
    assert got == "kanban-work/OddDir"


# --- _git_state: diff --cached -M --name-status -z ---------------------------------------------


def _staged_rename(world: World, old: str, new: str) -> Path:
    (world.a / old).write_text(item())
    git(world.a, "add", "--", old)
    git(world.a, "commit", "-m", "item")
    git(world.a, "mv", "--", old, new)
    return world.a / new


def test_git_state_staged_rename_of_cr_name(world) -> None:
    new = f"{EXP_DIR}/EXP-002\ry.md"
    path = _staged_rename(world, CR_NAME, new)
    assert service(world)._git_state(path) == "changed", (
        "a staged rename's \\r-named destination read as a new file"
    )


def test_control_git_state_staged_rename_plain(world) -> None:
    path = _staged_rename(world, PLAIN_NAME, f"{EXP_DIR}/EXP-002-y.md")
    assert service(world)._git_state(path) == "changed"


# --- _blob_at: ls-tree -z of one path ----------------------------------------------------------


def test_blob_at_reads_cr_name(world) -> None:
    on_origin(world, {CR_NAME: item()})
    assert service(world)._blob_at("origin/main", CR_NAME) == item()


def test_control_blob_at_plain_name(world) -> None:
    on_origin(world, {PLAIN_NAME: item()})
    assert service(world)._blob_at("origin/main", PLAIN_NAME) == item()


# --- _items_at / _blobs_at: ls-tree -r -z ------------------------------------------------------


def test_items_at_keeps_cr_in_path(world) -> None:
    on_origin(world, {CR_NAME: item()})
    found = {i.id: i for i in service(world)._items_at("origin/main", None)}
    assert "EXP-001" in found, found
    rel = found["EXP-001"].file_path.relative_to(world.a).as_posix()
    assert_exact(rel, CR_NAME)


def test_control_items_at_plain_name(world) -> None:
    on_origin(world, {PLAIN_NAME: item()})
    found = {i.id: i for i in service(world)._items_at("origin/main", None)}
    assert found["EXP-001"].file_path.relative_to(world.a).as_posix() == PLAIN_NAME


def test_blobs_at_reads_cr_name(world) -> None:
    on_origin(world, {CR_NAME: item()})
    blobs = service(world)._blobs_at("origin/main", blob_oids(world.a, "origin/main", [CR_NAME]))
    assert list(blobs) == [CR_NAME], [repr(k) for k in blobs]
    assert blobs[CR_NAME] == item()


def test_control_blobs_at_plain_name(world) -> None:
    on_origin(world, {PLAIN_NAME: item()})
    assert service(world)._blobs_at(
        "origin/main", blob_oids(world.a, "origin/main", [PLAIN_NAME])
    ) == {PLAIN_NAME: item()}
