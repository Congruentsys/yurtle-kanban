# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #869 — the case-twin guards on ``create --push`` treat names that differ only in
Unicode normalization (NFC vs NFD) as twins.

The file-twin guard (#788, in ``create_item_and_push``'s ``build``) and the folder-twin
guard (#834, ``_folder_case_twin``) compare names with ``casefold()`` alone. APFS (the
fleet's filesystem) is normalization-insensitive as well as case-insensitive: the NFC
``Café`` and the NFD ``Café`` (``e`` + U+0301) are ONE name there ([steer] on #869,
measured with ``os`` calls). Git stores names as bytes, so origin can hold both
spellings; every Mac clone then merges them into one file or folder.

Decided fix ([steer], bucket 1): both guards compare with one key,
``NFC(casefold(NFC(name)))``. The refusal names the existing file or folder; nothing
is created or pushed.

Origin's odd spelling is written with git plumbing in the bare remote (a temporary
index fed on stdin, ``core.precomposeunicode`` off), since a Mac working tree and git's
own argv precomposition would turn an NFD name back into NFC.

RED today:
- (1) file twin: origin holds ``research/experiments/ÉXP-042-x.md`` with ``É`` in one
  spelling (``id: EXP-043``, so the id guard is silent); the create builds the same
  name in the other spelling (explicit ``item_id``). Both directions.
- (2) folder twin: the board's root is ``Café/`` (NFC, as the config says), origin holds
  only ``Café/…`` in NFD (and the reverse). A create into ``Café/experiments/`` must be
  refused like #834's case twin.

Controls (green before and after): ``Straße/`` vs ``STRASSE/`` is still refused (APFS
folds ``ß`` to ``SS``, measured); a folder or file that is not a twin (``Cafe`` without
the accent, an ASCII ``EXP-042-x.md``) lands; the same spelling lands.
"""

from __future__ import annotations

import os
import tempfile
import unicodedata
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import _ORIG_RUN, World, git
from tests.issues.test_603_push_failure_messages import HDD_DIRS, reconfigure
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_754_duplicate_parent_and_epic import _service
from tests.issues.test_788_no_overwrite import EXP_043, EXPR_099
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def _key(s: str) -> str:
    """How APFS compares names (close enough for these fixtures)."""
    return nfc(nfc(s).casefold())


CAFE_NFC, CAFE_NFD = nfc("Café"), nfd("Café")
EXP_NFC, EXP_NFD = nfc("ÉXP-042"), nfd("ÉXP-042")  # "x" builds <id>-x.md
assert CAFE_NFC != CAFE_NFD and EXP_NFC != EXP_NFD
NEW_ID = "EXPR-042"  # nothing on origin holds it; "b" builds EXPR-042-b.md


# --- origin written with plumbing, names kept byte for byte -------------------------


def _plumb(world: World, *args: str, env: dict[str, str], stdin: str | None = None) -> str:
    result = _ORIG_RUN(
        ["git", "-c", "core.precomposeunicode=false", *args],
        cwd=world.remote,
        capture_output=True,
        text=True,
        input=stdin,
        env=env,
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"
    return result.stdout


def _origin_files(world: World) -> list[str]:
    """Origin main's paths, exact code points (no quotePath, no precomposition)."""
    out = _ORIG_RUN(
        [
            "git",
            "-c",
            "core.precomposeunicode=false",
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            "--full-tree",
            "main",
        ],
        cwd=world.remote,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(filter(None, out.split("\0")))


def _rewrite_origin(
    world: World, respell: tuple[str, str] | None = None, files: dict[str, str] | None = None
) -> None:
    """Commit on origin/main main's tree with every path under folder `respell[0]`
    (in either normalization) moved under `respell[1]` (same blobs), plus `files`,
    all spelled exactly as given."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(Path(tmp) / "index"),
            "GIT_WORK_TREE": tmp,  # never checked out; update-index wants one
            "GIT_AUTHOR_NAME": "Rival",
            "GIT_AUTHOR_EMAIL": "rival@example.com",
            "GIT_COMMITTER_NAME": "Rival",
            "GIT_COMMITTER_EMAIL": "rival@example.com",
        }
        listed = _plumb(world, "ls-tree", "-r", "-z", "--full-tree", "main", env=env)
        lines = []
        for entry in filter(None, listed.split("\0")):
            meta, path = entry.split("\t", 1)
            # either spelling of the folder: git on a Mac records A's folders
            # precomposed, git on Linux keeps them as written (CI runs Linux)
            if respell is not None:
                for spelled in {unicodedata.normalize(f, respell[0]) for f in ("NFC", "NFD")}:
                    if path.startswith(spelled):
                        path = respell[1] + path[len(spelled) :]
                        break
            lines.append(f"{meta}\t{path}")
        for rel, text in (files or {}).items():
            sha = _plumb(world, "hash-object", "-w", "--stdin", env=env, stdin=text).strip()
            lines.append(f"100644 blob {sha}\t{rel}")
        _plumb(world, "update-index", "-z", "--index-info", env=env, stdin="\0".join(lines) + "\0")
        tree = _plumb(world, "write-tree", env=env).strip()
        commit = _plumb(world, "commit-tree", tree, "-p", "main", "-m", "rival", env=env).strip()
        _plumb(world, "update-ref", "refs/heads/main", commit, env=env)


def _twins(paths: list[str]) -> dict[str, list[str]]:
    """Files AND folders on origin that are one name on APFS but differ in git."""
    names: set[str] = set()
    for p in paths:
        parts = p.split("/")
        names.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    folded: dict[str, set[str]] = {}
    for n in names:
        folded.setdefault(_key(n), set()).add(n)
    return {k: sorted(v) for k, v in folded.items() if len(v) > 1}


def _board(world: World, root: str) -> KanbanService:
    """An HDD board rooted at `root` (as the config and A's checkout spell it)."""
    reconfigure(world, "hdd", root, [f"{root}{d}/" for d in HDD_DIRS])
    return _service(world)


def _assert_refused_untouched(
    world: World, result: dict, names: str, base: str, before: list[str]
) -> None:
    after = _origin_files(world)
    assert world.remote_sha() == base, f"something was pushed; origin twins {_twins(after)}"
    assert after == before
    assert result["success"] is False, result
    assert nfc(names) in nfc(result.get("message", "")), result


def _assert_landed(world: World, result: dict, rel: str) -> None:
    assert result["success"] is True, result
    files = _origin_files(world)
    assert rel in files, files
    assert not _twins(files), _twins(files)


# --- (1) file twin: the new name differs from origin's only in normalization -------


@pytest.mark.parametrize(
    ("origin_id", "new_id"),
    [(EXP_NFD, EXP_NFC), (EXP_NFC, EXP_NFD)],
    ids=["origin-nfd-new-nfc", "origin-nfc-new-nfd"],
)
def test_file_nfc_nfd_twin_refused(world, origin_id, new_id) -> None:
    svc = _board(world, "research/")
    rival = f"research/experiments/{origin_id}-x.md"
    _rewrite_origin(world, files={rival: EXP_043})
    git(world.a, "fetch", "-q", "origin")
    before, base = _origin_files(world), world.remote_sha()
    assert rival in before and not _twins(before), before
    assert svc._holder_at("origin/main", new_id) is None  # not the id guard
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id=new_id)
    _assert_refused_untouched(world, result, f"{origin_id}-x.md", base, before)


# --- (2) folder twin: a folder on the path differs only in normalization -----------


@pytest.mark.parametrize(
    ("local", "origin"),
    [(CAFE_NFC, CAFE_NFD), (CAFE_NFD, CAFE_NFC)],
    ids=["board-nfc-origin-nfd", "board-nfd-origin-nfc"],
)
def test_folder_nfc_nfd_twin_refused(world, local, origin) -> None:
    # the board's config (and so the create) spells the root `local`; git on a Mac
    # records A's folders precomposed, so origin holds them NFC until rewritten
    svc = _board(world, f"{local}/")
    type_dir = svc._get_type_directory(WorkItemType.EXPERIMENT)
    rel = svc._repo_relative(type_dir, svc._git_toplevel())
    assert rel is not None and rel.as_posix() == f"{local}/experiments", (
        f"fixture: the create would not write under {local!a}/ but {rel}"
    )
    # origin holds the board's folder ONLY in `origin`'s spelling, with an item in it
    _rewrite_origin(
        world,
        respell=(f"{CAFE_NFC}/", f"{origin}/"),
        files={f"{origin}/experiments/EXPR-001-a.md": EXPR_099},
    )
    git(world.a, "fetch", "-q", "origin")
    before, base = _origin_files(world), world.remote_sha()
    assert not any(f.startswith(f"{local}/") for f in before), before
    assert not _twins(before), before
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    _assert_refused_untouched(world, result, f"{origin}/", base, before)


# --- controls ------------------------------------------------------------------------


def test_control_sharp_s_folder_still_refused(world) -> None:
    """APFS folds ß to SS (measured), so STRASSE/ and Straße/ are one folder."""
    svc = _board(world, "Straße/")
    _rewrite_origin(
        world,
        respell=(nfc("Straße/"), "STRASSE/"),
        files={"STRASSE/experiments/EXPR-001-a.md": EXPR_099},
    )
    git(world.a, "fetch", "-q", "origin")
    before, base = _origin_files(world), world.remote_sha()
    assert not any(f.startswith(nfc("Straße/")) for f in before), before
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    _assert_refused_untouched(world, result, "STRASSE/", base, before)


def test_control_unaccented_folder_not_a_twin_lands(world) -> None:
    svc = _board(world, f"{CAFE_NFC}/")
    _rewrite_origin(world, files={"Cafe/experiments/EXPR-001-a.md": EXPR_099})
    git(world.a, "fetch", "-q", "origin")
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    _assert_landed(world, result, f"{CAFE_NFC}/experiments/{NEW_ID}-b.md")
    assert "Cafe/experiments/EXPR-001-a.md" in _origin_files(world)


def test_control_same_spelling_folder_lands(world) -> None:
    svc = _board(world, f"{CAFE_NFC}/")
    _rewrite_origin(world, files={f"{CAFE_NFC}/experiments/EXPR-001-a.md": EXPR_099})
    git(world.a, "fetch", "-q", "origin")
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    _assert_landed(world, result, f"{CAFE_NFC}/experiments/{NEW_ID}-b.md")


def test_control_unaccented_file_not_a_twin_lands(world) -> None:
    svc = _board(world, "research/")
    rival = "research/experiments/EXP-042-x.md"
    _rewrite_origin(world, files={rival: EXP_043})
    git(world.a, "fetch", "-q", "origin")
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id=EXP_NFC)
    _assert_landed(world, result, f"research/experiments/{EXP_NFC}-x.md")
    assert world.remote_show(rival) == EXP_043
