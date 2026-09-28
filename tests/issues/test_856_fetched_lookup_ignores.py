"""Issue #856 — the fetched-tree lookup of the item acted on applies the board's
ignore patterns.

At the fetched rev only the WIP count (``_items_at``) applied ignore patterns. The
lookup of the item being claimed or updated (``_item_target`` → ``_holders_at`` over
``_ids_at``) and the #777 parent lookup (``_parent_link_blob``) applied none, so an
archived item could be claimed or updated with ``--push`` while the board doesn't
show it, and an archived copy counted as a duplicate.

Decided spec ([steer] on #856, bucket 1):

1. ``claim EXP-005`` where origin's only copy sits under an ignored path (the default
   ``**/archive/**``, a configured single-board ``paths.ignore``, or a multi-board
   board's own ``ignore``) is refused "Item not found on origin"; nothing is pushed.
2. ``update EXP-005 --push --title X`` on such an item is refused the same way.
3. Duplicate: the live copy is on the board and an ignored copy holds the same
   ``id:``. ``claim`` (and ``update --push``) act on the live copy; no "on more than
   one board" refusal; the push commit touches only the live file.
4. Parent link (#777): ``hypothesis create --paper 130 --push`` whose parent's only
   copy on origin is archived answers as the local path does: the board doesn't
   have PAPER-130, so it is 'missing' ("PAPER-130 is not on any board", exit 0, the
   child is created) and the archived file is byte-identical.
5. Controls (green now): the id space still sees ignored files — ``next-id`` skips an
   archived EXP-005, ``create --push`` with ``--id EXP-005`` is refused as taken
   (``_holder_at``), and a normal claim wins.

RED today: 1, 2, 3 and 4 (not the ``control`` tests).

Reuses the #574 claim/update harness and the #645/#777/#819 HDD harness (a bare
remote, clone A under test, rival clone B).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import A, frontmatter, item_text, output_of
from tests.issues.test_574_sync_and_push import Recorder, commit_files, snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_645_parent_in_cas import HYP, PAPER, seed_on_origin
from tests.issues.test_674_parent_edges import flat
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

LIVE_ID = "EXP-001"
LIVE = f"{EXP_DIR}/EXP-001-x.md"
GONE_ID = "EXP-005"
ARCHIVED = f"{EXP_DIR}/archive/EXP-005-old.md"
PARKED = f"{EXP_DIR}/parked/EXP-005-old.md"
NOT_FOUND = f"Item not found on origin: {GONE_ID}"
MORE_THAN_ONE = "on more than one board"

# multi-board: the board's own `ignore` hides `parked/` (paths.ignore doesn't govern it)
MULTI_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    ignore:
      - "**/archive/**"
      - "**/templates/**"
      - "**/parked/**"
default_board: development
"""

PARENT = "PAPER-130"
ARCHIVED_PAPER = "research/papers/archive/PAPER-130-A-paper.md"
MISSING_LINE = f"{PARENT} is not on any board"


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def push_from_a(world: World, files: dict[str, str], message: str) -> None:
    """Commit `files` on A's main and push them; B follows origin."""
    for rel, text in files.items():
        path = world.a / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", message)
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold the live EXP-001 at `ready`, unassigned."""
    w = World(tmp_path)
    push_from_a(w, {LIVE: item_text("ready")}, "seed EXP-001")
    return w


def single_board_ignoring_parked(world: World) -> None:
    """The single board, with `paths.ignore` set to hide `parked/` (on origin and A)."""
    KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/", "kanban-work/signals/"],
            ignore=["**/archive/**", "**/templates/**", "**/parked/**"],
        ),
    ).save(world.a / ".kanban" / "config.yaml")
    push_from_a(world, {}, "board: ignore parked")


def multi_board_ignoring_parked(world: World) -> None:
    push_from_a(world, {".kanban/config.yaml": MULTI_CONFIG}, "boards: ignore parked")


# (id, board setup, where the ignored copy sits)
BOARDS = [
    ("default-archive", None, ARCHIVED),
    ("single-paths-ignore", single_board_ignoring_parked, PARKED),
    ("multi-board-ignore", multi_board_ignoring_parked, PARKED),
]
BOARD_IDS = [b[0] for b in BOARDS]


def setup_board(world: World, setup: Any) -> None:
    if setup is not None:
        setup(world)


def service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


def claim(clone: Path, item_id: str, rec: Recorder | None = None) -> Any:
    rec = rec or Recorder()
    return service(clone).claim_item(
        item_id, actor=A, take_over=False, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, argv)


def assert_one_commit_touching(world: World, base: str, rel: str) -> str:
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert commit_files(world.remote, tip) == [rel], (
        f"the commit touched {commit_files(world.remote, tip)}, not only {rel}"
    )
    return tip


def precondition_ignored_locally(world: World, item_id: str, rel: str) -> None:
    """The local board (A's scan) doesn't show the ignored copy: the lookup on origin
    must agree with it."""
    assert (world.a / rel).exists()
    svc = service(world.a)
    item = svc.get_item(item_id)
    assert item is None or Path(item.file_path).resolve() != (world.a / rel).resolve(), (
        f"precondition: A's board shows the ignored {rel}"
    )


# --- 1. claim of an item whose only copy on origin is ignored (RED) --------------------


@pytest.mark.parametrize(("case", "setup", "rel"), BOARDS, ids=BOARD_IDS)
def test_claim_of_ignored_item_is_not_found(world, case, setup, rel) -> None:
    setup_board(world, setup)
    push_from_a(world, {rel: item_text("ready", item_id=GONE_ID, title="Old")}, "ignored")
    precondition_ignored_locally(world, GONE_ID, rel)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, GONE_ID, rec)

    assert out.kind == "refused", f"an ignored item was claimed ({case}): {out.message}"
    assert out.exit_code == 1
    assert NOT_FOUND in out.message, out.message
    assert rec.seams == [], "a push was attempted"
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


@pytest.mark.parametrize(("case", "setup", "rel"), BOARDS, ids=BOARD_IDS)
def test_cli_claim_of_ignored_item_is_not_found(world, monkeypatch, case, setup, rel) -> None:
    setup_board(world, setup)
    push_from_a(world, {rel: item_text("ready", item_id=GONE_ID, title="Old")}, "ignored")
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", GONE_ID, "--agent", A])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert NOT_FOUND in out, out
    assert world.remote_sha() == base
    assert "assignee" not in frontmatter(world.remote_show(rel))


# --- 2. update --push of an ignored item (RED) -----------------------------------------


@pytest.mark.parametrize(("case", "setup", "rel"), BOARDS, ids=BOARD_IDS)
def test_update_push_of_ignored_item_is_not_found(world, monkeypatch, case, setup, rel) -> None:
    setup_board(world, setup)
    push_from_a(world, {rel: item_text("ready", item_id=GONE_ID, title="Old")}, "ignored")
    base = world.remote_sha()
    old = world.remote_show(rel)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["update", GONE_ID, "--push", "--title", "X"])

    out = output_of(result)
    assert result.exit_code == 1, f"an ignored item was updated ({case}):\n{out}"
    assert NOT_FOUND in out, out
    assert world.remote_sha() == base
    assert world.remote_show(rel) == old
    assert snapshot(world.a) == before


# --- 3. an ignored copy is no duplicate (RED) -------------------------------------------


def _ignored_copy(world: World, setup: Any, rel: str) -> str:
    """An ignored copy of EXP-001 (same `id:`) beside the live one, on origin and A."""
    setup_board(world, setup)
    copy = rel.replace("EXP-005-old", "EXP-001-old")
    push_from_a(world, {copy: item_text("ready", title="Old copy")}, "ignored copy")
    precondition_ignored_locally(world, LIVE_ID, copy)
    return copy


@pytest.mark.parametrize(("case", "setup", "rel"), BOARDS, ids=BOARD_IDS)
def test_claim_with_ignored_duplicate_claims_the_live_copy(world, case, setup, rel) -> None:
    copy = _ignored_copy(world, setup, rel)
    base = world.remote_sha()
    old_copy = world.remote_show(copy)

    out = claim(world.a, LIVE_ID)

    assert MORE_THAN_ONE not in out.message, (
        f"the ignored {copy} was counted as a duplicate ({case}): {out.message}"
    )
    assert out.kind == "won", out.message
    assert_one_commit_touching(world, base, LIVE)
    assert frontmatter(world.remote_show(LIVE))["assignee"] == A
    assert world.remote_show(copy) == old_copy


@pytest.mark.parametrize(("case", "setup", "rel"), BOARDS, ids=BOARD_IDS)
def test_update_push_with_ignored_duplicate_updates_the_live_copy(
    world, monkeypatch, case, setup, rel
) -> None:
    copy = _ignored_copy(world, setup, rel)
    base = world.remote_sha()
    old_copy = world.remote_show(copy)

    result = invoke(world, monkeypatch, ["update", LIVE_ID, "--push", "--title", "Renamed"])

    out = output_of(result)
    assert MORE_THAN_ONE not in out, f"the ignored {copy} was counted ({case}):\n{out}"
    assert result.exit_code == 0, out
    assert_one_commit_touching(world, base, LIVE)
    assert frontmatter(world.remote_show(LIVE))["title"] == "Renamed"
    assert world.remote_show(copy) == old_copy


# --- 4. the #777 parent lookup on origin skips an ignored parent (RED) -----------------


def _archived_paper(world: World, monkeypatch) -> str:
    """PAPER-130 created on origin, then moved under `research/papers/archive/` (the
    default ignore) on origin and in A: its only copy is ignored."""
    seed_on_origin(world, monkeypatch, HYP)
    (world.a / ARCHIVED_PAPER).parent.mkdir(parents=True, exist_ok=True)
    git(world.a, "mv", PAPER, ARCHIVED_PAPER)
    git(world.a, "commit", "-q", "-m", "archive PAPER-130")
    git(world.a, "push", "-q", "origin", "main")
    config_mod._theme_cache.clear()
    assert ARCHIVED_PAPER in world.remote_files() and PAPER not in world.remote_files()
    return world.remote_show(ARCHIVED_PAPER)


def _hyp_files(root: Path) -> list[str]:
    d = root / "research" / "hypotheses"
    return sorted(p.name for p in d.glob("*.md")) if d.exists() else []


def test_control_local_create_calls_archived_parent_missing(world, monkeypatch) -> None:
    """What the push path must match (green now): the local board has no PAPER-130."""
    text = _archived_paper(world, monkeypatch)
    assert service(world.a).parent_link_state(PARENT, "hypothesis", "H130.9") == "missing"

    local = invoke(world, monkeypatch, [a for a in HYP.argv if a != "--push"])

    out = flat(local)
    assert local.exit_code == 0, out
    assert MISSING_LINE in out, out
    assert (world.a / ARCHIVED_PAPER).read_text() == text
    assert len(_hyp_files(world.a)) == 1, _hyp_files(world.a)


def test_parent_link_blob_skips_archived_parent(world, monkeypatch) -> None:
    _archived_paper(world, monkeypatch)
    git(world.a, "fetch", "-q", "origin")

    got = service(world.a)._parent_link_blob("origin/main", PARENT, "hypothesis", "H130.9")

    assert got == ({}, "missing"), (
        f"the archived {ARCHIVED_PAPER} was taken as the parent on origin: {got}"
    )


def test_create_push_with_archived_parent_matches_local(world, monkeypatch) -> None:
    text = _archived_paper(world, monkeypatch)
    base = world.remote_sha()

    pushed = invoke(world, monkeypatch, list(HYP.argv))

    out = flat(pushed)
    assert world.remote_show(ARCHIVED_PAPER) == text, (
        f"--push wrote the parent link into the archived {ARCHIVED_PAPER}:\n"
        + git(world.remote, "diff", base, "main", "--", ARCHIVED_PAPER)
    )
    assert pushed.exit_code == 0, out
    assert MISSING_LINE in out, f"--push did not call {PARENT} missing:\n{out}"
    new = git(world.remote, "rev-list", f"{base}..main").split()
    assert len(new) == 1, f"the create did not land as one commit: {new}"
    files = set(git(world.remote, "show", "--name-only", "--format=", new[0]).split())
    children = [f for f in files if f.startswith(HYP.child_dir) and f.endswith(".md")]
    assert len(children) == 1, files
    assert files <= {children[0], ".kanban/_ID_ALLOCATIONS.json"}, sorted(files)


# --- 5. controls: the id space still sees ignored files (green now) --------------------


@pytest.mark.parametrize(("case", "setup", "rel"), BOARDS, ids=BOARD_IDS)
def test_control_next_id_counts_ignored_id(world, monkeypatch, case, setup, rel) -> None:
    setup_board(world, setup)
    b_push(world, {rel: item_text("done", item_id=GONE_ID, title="Old")})
    git(world.a, "fetch", "-q", "origin")
    assert service(world.a)._next_id_number_at("origin/main", "EXP") == 6

    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])

    out = output_of(result)
    assert result.exit_code == 0, out
    assert "EXP-006" in json.dumps(json.loads(result.output)), out


@pytest.mark.parametrize(("case", "setup", "rel"), BOARDS, ids=BOARD_IDS)
def test_control_create_push_explicit_id_held_by_ignored_file(world, case, setup, rel) -> None:
    setup_board(world, setup)
    b_push(world, {rel: item_text("done", item_id=GONE_ID, title="Old")})
    git(world.a, "fetch", "-q", "origin")
    assert service(world.a)._holder_at("origin/main", GONE_ID) == rel
    base = world.remote_sha()

    result = service(world.a).create_item_and_push(
        WorkItemType.EXPEDITION, "New five", item_id=GONE_ID
    )

    msg = result.get("message", "")
    assert result["success"] is False, f"{GONE_ID} was reissued beside {rel}: {result}"
    assert "already taken" in msg and rel in msg, msg
    assert world.remote_sha() == base


def test_control_normal_claim_wins(world) -> None:
    base = world.remote_sha()

    out = claim(world.a, LIVE_ID)

    assert out.kind == "won", out.message
    assert_one_commit_touching(world, base, LIVE)
    assert frontmatter(world.remote_show(LIVE))["assignee"] == A
