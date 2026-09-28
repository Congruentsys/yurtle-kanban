"""Issue #891 — pin ``_board_loads`` against the scan for the layouts the #856 review
checked by hand.

``_board_loads(rel)`` answers, for a path relative to the git work tree's top, whether
a scan would load that file (#856). ``_holders_at(loaded=True)`` uses it to keep an
ignored copy on origin from being claimed or updated. Nothing pinned it for:

1. nested multi-board roots (board A at ``work/``, board B at ``work/sub/``, each with
   its own ``ignore``), including a file one board ignores that lies under the other
   board's root;
2. a board at the repo root (``path: .`` or ``""``) with ``.kanban/`` in a
   subdirectory of the git top, so the service's repo root is not the git top;
3. a single board with ``paths.ignore`` and ``.kanban/`` in a subdirectory.

For each layout: ``_board_loads(rel)`` equals "the scan has this file" for every
``.md`` file ``_ids_at`` lists at HEAD (the input ``_holders_at`` feeds it), ignored
ones included. On a multi-board config ``_board_loads`` also checks the board root, so
there it is pinned over every tracked ``.md`` file in the tree. One end-to-end pin per
layout: ``claim`` of an item whose only copy on origin is ignored is "Item not found
on origin", and ``claim`` of a loaded item wins with one commit touching only it.

Test-only (bucket 1, [steer] on #891): green on main. Each pin was shown red by a
temporary mutation of ``_board_loads`` (reverted).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.issues.test_574_claim import A, frontmatter, item_text
from tests.issues.test_574_sync_and_push import Recorder, commit_files
from tests.issues.test_585_create_push_loop import _configure, git
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

# --- layouts -----------------------------------------------------------------------
#
# Every path below is relative to the git top. `kanban` is the folder holding
# `.kanban/` (the service's repo root). `files` maps each .md to whether a scan loads
# it; the pins compare `_board_loads` with the actual scan, and these expectations
# only guard that each layout exercises what it claims to.

NESTED_CONFIG = """\
version: "2.0"
boards:
  - name: outer
    preset: nautical
    path: work/
    ignore:
      - "**/archive/**"
      - "work/parked/**"
      - "work/sub/held/**"
  - name: inner
    preset: nautical
    path: work/sub/
    ignore:
      - "**/archive/**"
      - "work/sub/draft/**"
default_board: outer
"""

NESTED_FILES = {
    "work/EXP-001-top.md": True,
    "work/sub/EXP-002-inner.md": True,
    # ignored by outer, not under inner's root: nobody loads it
    "work/parked/EXP-003-parked.md": False,
    # ignored by inner, under outer's root and not ignored there: outer loads it
    "work/sub/draft/EXP-004-draft.md": True,
    # ignored by outer, under inner's root and not ignored there: inner loads it
    "work/sub/held/EXP-005-held.md": True,
    # ignored by both
    "work/sub/archive/EXP-006-old.md": False,
    "work/archive/EXP-007-old.md": False,
    # under no board
    "notes/EXP-008-notes.md": False,
    "README.md": False,
}


def root_board_config(path: str) -> str:
    return f"""\
version: "2.0"
boards:
  - name: dev
    preset: nautical
    path: "{path}"
    ignore:
      - "**/archive/**"
      - "parked/**"
default_board: dev
"""


# `.kanban/` in `proj/`; the board is `proj/` itself. Ignore patterns match paths
# relative to `proj/` (the repo root), not to the git top.
ROOT_BOARD_FILES = {
    "proj/EXP-001-top.md": True,
    "proj/expeditions/EXP-002-exp.md": True,
    "proj/parked/EXP-003-parked.md": False,
    "proj/expeditions/archive/EXP-004-old.md": False,
    # `parked/**` is repo-root relative: a nested `parked/` is loaded
    "proj/expeditions/parked/EXP-005-deep.md": True,
    # outside the repo root, beside it, and a sibling sharing its name as a prefix
    "other/EXP-006-other.md": False,
    "proj-extra/EXP-007-extra.md": False,
    "README.md": False,
}

SINGLE_CONFIG = """\
version: "1.0"
theme: nautical
paths:
  root: work/
  scan_paths:
    - work/
  ignore:
    - "**/archive/**"
    - "work/parked/**"
"""

# `.kanban/` in `proj/`; `paths.ignore` matches paths relative to `proj/`.
SINGLE_FILES = {
    "proj/work/EXP-001-top.md": True,
    "proj/work/expeditions/EXP-002-exp.md": True,
    "proj/work/parked/EXP-003-parked.md": False,
    "proj/work/archive/EXP-004-old.md": False,
    # `work/parked/**` is repo-root relative: `parked/` elsewhere is loaded
    "proj/work/expeditions/parked/EXP-005-deep.md": True,
}


@dataclass(frozen=True)
class Layout:
    name: str
    kanban: str  # folder holding .kanban/, relative to the git top ("" = the top)
    config: str
    files: dict[str, bool]
    ignored: str  # an ignored item's file, for the end-to-end claim
    loaded: str  # a loaded item's file, likewise

    @property
    def multi(self) -> bool:
        return "boards:" in self.config


LAYOUTS = [
    Layout(
        "nested-multi-board",
        "",
        NESTED_CONFIG,
        NESTED_FILES,
        ignored="work/parked/EXP-003-parked.md",
        loaded="work/sub/held/EXP-005-held.md",
    ),
    Layout(
        "root-board-dot-kanban-in-subdir",
        "proj",
        root_board_config("."),
        ROOT_BOARD_FILES,
        ignored="proj/parked/EXP-003-parked.md",
        loaded="proj/expeditions/parked/EXP-005-deep.md",
    ),
    Layout(
        "root-board-empty-kanban-in-subdir",
        "proj",
        root_board_config(""),
        ROOT_BOARD_FILES,
        ignored="proj/parked/EXP-003-parked.md",
        loaded="proj/expeditions/parked/EXP-005-deep.md",
    ),
    Layout(
        "single-board-paths-ignore-kanban-in-subdir",
        "proj",
        SINGLE_CONFIG,
        SINGLE_FILES,
        ignored="proj/work/parked/EXP-003-parked.md",
        loaded="proj/work/expeditions/parked/EXP-005-deep.md",
    ),
]
LAYOUT_IDS = [layout.name for layout in LAYOUTS]


def _id_of(rel: str) -> str:
    return "-".join(Path(rel).stem.split("-")[:2])


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@dataclass
class Repo:
    remote: Path
    top: Path  # clone A's work tree
    layout: Layout

    @property
    def repo_root(self) -> Path:
        return self.top / self.layout.kanban if self.layout.kanban else self.top

    def service(self) -> KanbanService:
        config_mod._theme_cache.clear()
        return KanbanService(
            KanbanConfig.load(self.repo_root / ".kanban" / "config.yaml"), self.repo_root
        )

    def remote_sha(self) -> str:
        return git(self.remote, "rev-parse", "main").strip()

    def remote_show(self, rel: str) -> str:
        return git(self.remote, "show", f"main:{rel}")


def build(tmp_path: Path, layout: Layout) -> Repo:
    """A bare remote and clone A holding `layout`'s config and files, pushed."""
    remote = tmp_path / "remote.git"
    top = tmp_path / "A"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", str(remote), str(top))
    _configure(top)
    git(top, "checkout", "-B", "main")
    repo = Repo(remote, top, layout)
    (repo.repo_root / ".kanban").mkdir(parents=True)
    (repo.repo_root / ".kanban" / "config.yaml").write_text(layout.config)
    for rel in layout.files:
        path = top / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (
            "readme\n"
            if rel == "README.md"
            else item_text("ready", item_id=_id_of(rel), title=Path(rel).stem)
        )
        path.write_text(text)
    git(top, "add", "-A")
    git(top, "commit", "-m", "seed")
    git(top, "push", "-u", "origin", "main")
    git(top, "fetch", "origin")
    return repo


@pytest.fixture(params=LAYOUTS, ids=LAYOUT_IDS)
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> Repo:
    return build(tmp_path, request.param)


def scanned(repo: Repo) -> set[str]:
    """The files a scan loads, relative to the git top."""
    svc = repo.service()
    top = svc._git_toplevel()
    out = set()
    for item in svc.scan():
        rel = svc._repo_relative(Path(item.file_path), top)
        assert rel is not None, f"the scan loaded {item.file_path}, outside {top}"
        out.add(rel.as_posix())
    return out


def tracked_md(repo: Repo) -> set[str]:
    listed = git(repo.top, "ls-tree", "-r", "--name-only", "--full-tree", "HEAD").split()
    return {name for name in listed if name.endswith(".md")}


# --- 1. _board_loads agrees with the scan -------------------------------------------


def test_layout_exercises_what_it_claims(repo: Repo) -> None:
    """Guard: the scan loads exactly the files the layout says, so each pin below
    covers loaded and ignored files (including the cross-board ones)."""
    expected = {rel for rel, loads in repo.layout.files.items() if loads}
    assert scanned(repo) == expected
    assert repo.service().config.is_multi_board == repo.layout.multi


def test_board_loads_agrees_with_scan_over_ids_at(repo: Repo) -> None:
    """Every `.md` `_ids_at` lists (the input `_holders_at(loaded=True)` filters),
    ignored ones included."""
    svc = repo.service()
    listed = set(svc._ids_at("HEAD")[0])
    assert listed, "precondition: _ids_at lists the work paths' files"
    loads = scanned(repo)
    ignored = listed - loads
    assert ignored, "precondition: some listed file is ignored"

    got = {rel: svc._board_loads(rel) for rel in sorted(listed)}

    wrong = {rel: v for rel, v in got.items() if v != (rel in loads)}
    assert not wrong, (
        f"_board_loads disagrees with the scan ({repo.layout.name}): {wrong}; "
        f"scan loads {sorted(loads)}"
    )


def test_board_loads_agrees_with_scan_over_whole_tree(repo: Repo) -> None:
    """Multi-board: `_board_loads` checks each board's root itself, so it must agree
    with the scan for any `.md` in the tree, outside every board too."""
    if not repo.layout.multi:
        pytest.skip("single board: _board_loads leaves the work-path filter to _ids_at")
    svc = repo.service()
    loads = scanned(repo)
    tree = tracked_md(repo)
    assert tree == set(repo.layout.files)

    got = {rel: svc._board_loads(rel) for rel in sorted(tree)}

    wrong = {rel: v for rel, v in got.items() if v != (rel in loads)}
    assert not wrong, (
        f"_board_loads disagrees with the scan ({repo.layout.name}): {wrong}; "
        f"scan loads {sorted(loads)}"
    )


def test_holders_at_loaded_follows_the_scan(repo: Repo) -> None:
    """Through `_holders_at(loaded=True)` at origin: an ignored item has no holder, a
    loaded one has its file."""
    svc = repo.service()
    ignored, loaded = repo.layout.ignored, repo.layout.loaded
    assert svc._holders_at("origin/main", _id_of(ignored), loaded=True) == []
    assert svc._holders_at("origin/main", _id_of(ignored)) == [ignored]
    assert svc._holders_at("origin/main", _id_of(loaded), loaded=True) == [loaded]


# --- 2. end to end: claim at origin -------------------------------------------------


def claim(repo: Repo, item_id: str, rec: Recorder) -> object:
    return repo.service().claim_item(
        item_id, actor=A, take_over=False, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )


def test_claim_of_ignored_item_is_not_found(repo: Repo) -> None:
    rel = repo.layout.ignored
    item_id = _id_of(rel)
    base = repo.remote_sha()
    old = repo.remote_show(rel)
    rec = Recorder()

    out = claim(repo, item_id, rec)

    assert out.kind == "refused", (
        f"the ignored {rel} was claimed ({repo.layout.name}): {out.message}"
    )
    assert out.exit_code == 1
    assert f"Item not found on origin: {item_id}" in out.message, out.message
    assert rec.seams == [], "a push was attempted"
    assert repo.remote_sha() == base
    assert repo.remote_show(rel) == old


def test_claim_of_loaded_item_wins(repo: Repo) -> None:
    rel = repo.layout.loaded
    base = repo.remote_sha()

    out = claim(repo, _id_of(rel), Recorder())

    assert out.kind == "won", f"{rel} ({repo.layout.name}): {out.message}"
    tip = repo.remote_sha()
    assert git(repo.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(repo.remote, tip) == [rel]
    assert frontmatter(repo.remote_show(rel))["assignee"] == A
