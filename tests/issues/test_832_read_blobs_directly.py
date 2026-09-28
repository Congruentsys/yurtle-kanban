"""Issue #832: reads of origin's fetched tree are not altered by ``export-subst``.

``KanbanService._blobs_at`` reads the board files at a commit through ``git
archive``. A ``.gitattributes`` ``export-subst`` attribute makes the archive rewrite
``$Format:...$`` placeholders, so the text that gets counted is not the text that
was committed. The [steer] on #832 fixes the shape: list the files with ``ls-tree -r
-z`` and read them with ``cat-file`` (``--batch`` fed from a temp file, since git
never reads stdin, #580). The #814 fail-closed behaviour stays for trees that
really can't be read.

Harness: the #585 ``World`` (a bare origin plus clones A and B) and the #574 claim
helpers, as in tests/issues/test_814_wip_fails_closed.py.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. The substitution is made to change a COUNTED value, in both directions:
   - under-count (fail-open): B's in-progress item has ``id: "$Format:%s$"`` and
     the tip commit's subject is ``EXP-001``. The archive turns its id into
     ``EXP-001``, the item being claimed, and a claim never counts itself, so WIP
     reads 0 and A wins. Read as committed, it holds the slot and A is refused.
   - over-count: B's item has ``status: $Format:%s$`` (an unknown status, parsed as
     backlog) and the tip's subject is ``in_progress``. The archive makes it in
     progress, so A is refused. Read as committed, WIP is free and A wins.
b. ``_blobs_at(rev, oids)[name]`` must equal the committed bytes, decoded. This is
   pinned directly, as well as through ``claim``.
c. ``export-ignore`` (#814): with direct reads the file IS readable, since it is in
   the tree. The fixed behaviour this file pins is that the file is READ and counted
   (``_blobs_at`` returns it; WIP full refuses as WIP, not with an archive message;
   WIP free wins). Several #814 tests assert the old fail-closed-on-export-ignore
   behaviour and will need to change with the fix.
d. git must never read stdin (#580): the CLI claim is run as a real subprocess with
   ``stdin=subprocess.DEVNULL``. In process, every git subprocess ``_blobs_at``
   starts must be given its stdin explicitly (``stdin=`` or ``input=``), never
   inherit it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_claim import (
    ITEM_ID,
    OTHER,
    WIP_CONFIG,
    A,
    B,
    assert_claimed_by,
    claim,
    item_text,
    push_from_a,
    service,
)
from tests.issues.test_574_sync_and_push import Recorder, snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from yurtle_kanban import config as config_mod

REPO = Path(__file__).resolve().parents[2]

SUBST_ALL_MD = "*.md export-subst\n"
SUBST_BOARD = "kanban-work/** export-subst\n"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned."""
    w = World(tmp_path)
    push_from_a(w, {f"{EXP_DIR}/EXP-001-x.md": item_text("ready")}, "seed EXP-001")
    return w


# --- harness -----------------------------------------------------------------------------


def id_placeholder_item() -> str:
    """B's in-progress item whose id is a `$Format:%s$` placeholder."""
    return (
        '---\nid: "$Format:%s$"\ntitle: "Y"\ntype: expedition\nstatus: in_progress\n'
        f"assignee: {B}\n---\n\n# Y\n\nA description long enough.\n"
    )


def status_placeholder_item() -> str:
    """B's item whose status is a `$Format:%s$` placeholder (unknown: backlog)."""
    return (
        '---\nid: EXP-002\ntitle: "Y"\ntype: expedition\nstatus: $Format:%s$\n'
        f"assignee: {B}\n---\n\n# Y\n\nA description long enough.\n"
    )


def b_push_as(world: World, files: dict[str, str], subject: str) -> None:
    """B commits `files` on a fresh origin/main with commit subject `subject` (what
    `%s` expands to) and pushes. It is the tip commit the claim reads."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    for rel, text in files.items():
        path = world.b / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", subject)
    git(world.b, "push", "origin", f"HEAD:refs/heads/{world.default}")


def setup_origin(world: World, attributes: str | None, item: str, subject: str) -> None:
    """WIP limit 1 (and `attributes`, if any) on origin, then B's `item` at OTHER
    as the tip commit with `subject`. A never pulls B's item."""
    files = {".kanban/config.yaml": WIP_CONFIG}
    if attributes is not None:
        files[".gitattributes"] = attributes
    push_from_a(world, files, "board: wip 1")
    b_push_as(world, {OTHER: item}, subject)
    assert not (world.a / OTHER).exists(), "A must not see B's item locally"


def blob_oids(repo: Path, rev: str, names: list[str]) -> dict[str, str]:
    """`_blobs_at`'s input (#880): each name's blob object id at `rev`, as
    `_items_at`'s `ls-tree` passes them."""
    return {n: git(repo, "rev-parse", f"{rev}:{n}").strip() for n in names}


def origin_rev(world: World) -> str:
    git(world.a, "fetch", "origin")
    return git(world.a, "rev-parse", f"origin/{world.default}").strip()


def committed_text(world: World, rel: str) -> str:
    raw = subprocess.run(
        ["git", "cat-file", "blob", f"{world.default}:{rel}"],
        cwd=world.remote, capture_output=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout
    return raw.decode("utf-8")


def archived_text(world: World, rev: str, rel: str) -> str:
    """What `git archive` gives for `rel` (sanity: the attribute does rewrite it)."""
    import io
    import tarfile

    done = subprocess.run(
        ["git", "archive", "--format=tar", rev, "--", rel],
        cwd=world.a, capture_output=True, check=True, stdin=subprocess.DEVNULL,
    )
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        handle = tar.extractfile(rel)
        assert handle is not None
        return handle.read().decode("utf-8")


def assert_refused_as_wip(world: World, out: Any, base: str, before: dict, rec: Recorder) -> None:
    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    msg = out.message.lower()
    assert "wip" in msg, out.message
    assert not any(w in msg for w in ("archive", "export-", ".gitattributes")), out.message
    assert world.remote_sha() == base, "the refused claim changed origin"
    assert rec.seams == [], "a push was attempted"
    assert snapshot(world.a) == before, "the refused claim wrote to A's checkout"


# --- 1. export-subst: _blobs_at returns the committed text -------------------------------


@pytest.mark.parametrize("attributes", [SUBST_ALL_MD, SUBST_BOARD], ids=["all-md", "board"])
@pytest.mark.parametrize(
    "item, subject",
    [(id_placeholder_item(), ITEM_ID), (status_placeholder_item(), "in_progress")],
    ids=["id", "status"],
)
def test_blobs_at_reads_committed_bytes_under_export_subst(
    world, attributes, item, subject
) -> None:
    setup_origin(world, attributes, item, subject)
    rev = origin_rev(world)
    committed = committed_text(world, OTHER)
    assert "$Format:%s$" in committed
    assert archived_text(world, rev, OTHER) != committed, "export-subst did not apply"

    blobs = service(world.a)._blobs_at(rev, blob_oids(world.a, rev, [OTHER]))

    assert blobs.get(OTHER) == committed, (
        f"_blobs_at altered {OTHER}:\n{blobs.get(OTHER)!r}\n!=\n{committed!r}"
    )


def test_items_at_parses_committed_id_and_status(world) -> None:
    setup_origin(world, SUBST_ALL_MD, id_placeholder_item(), ITEM_ID)
    rev = origin_rev(world)

    items = {i.id: i for i in service(world.a)._items_at(rev, None)}

    assert "$Format:%s$" in items, f"placeholder id rewritten: {sorted(items)}"
    assert items["$Format:%s$"].status.value == "in_progress"
    assert items[ITEM_ID].status.value == "ready", "EXP-001 was shadowed by B's item"
    assert items[ITEM_ID].assignee in (None, "")


# --- 1. export-subst through claim's WIP count --------------------------------------------


@pytest.mark.parametrize("attributes", [SUBST_ALL_MD, SUBST_BOARD], ids=["all-md", "board"])
def test_claim_export_subst_cannot_hide_an_in_progress_item(world, attributes) -> None:
    """Under-count: the archive would rename B's in-progress item to EXP-001, which
    a claim of EXP-001 never counts. Read as committed, WIP is full."""
    setup_origin(world, attributes, id_placeholder_item(), ITEM_ID)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert_refused_as_wip(world, out, base, before, rec)


@pytest.mark.parametrize("attributes", [SUBST_ALL_MD, SUBST_BOARD], ids=["all-md", "board"])
def test_claim_export_subst_cannot_invent_an_in_progress_item(world, attributes) -> None:
    """Over-count: the archive would turn B's placeholder status into in_progress.
    Read as committed, it is not in progress, so WIP is free."""
    setup_origin(world, attributes, status_placeholder_item(), "in_progress")
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", f"{out.kind}: {out.message}"
    assert_claimed_by(world, A, base)


def test_cli_claim_export_subst_stdin_devnull_refused(world) -> None:
    """The CLI as a real process, stdin closed off: refused on WIP, origin unchanged."""
    setup_origin(world, SUBST_ALL_MD, id_placeholder_item(), ITEM_ID)
    base = world.remote_sha()
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "GIT_TERMINAL_PROMPT": "0"}
    env.pop("YURTLE_AGENT", None)

    done = subprocess.run(
        [sys.executable, "-c", "from yurtle_kanban.cli import main; main()",
         "claim", ITEM_ID, "--agent", A],
        cwd=world.a, env=env, capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )

    out = " ".join((done.stdout + done.stderr).split())
    assert done.returncode == 1, out
    assert "wip" in out.lower(), out
    assert world.remote_sha() == base


# --- 2. control: no export-subst, the text is unchanged -----------------------------------


@pytest.mark.parametrize(
    "item, subject",
    [(id_placeholder_item(), ITEM_ID), (status_placeholder_item(), "in_progress")],
    ids=["id", "status"],
)
def test_control_blobs_at_without_export_subst_is_verbatim(world, item, subject) -> None:
    setup_origin(world, None, item, subject)
    rev = origin_rev(world)

    blobs = service(world.a)._blobs_at(rev, blob_oids(world.a, rev, [OTHER]))

    assert blobs.get(OTHER) == committed_text(world, OTHER)


def test_control_claim_without_export_subst_counts_placeholder_item(world) -> None:
    setup_origin(world, None, id_placeholder_item(), ITEM_ID)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert_refused_as_wip(world, out, base, before, rec)


def test_control_claim_without_export_subst_placeholder_status_wins(world) -> None:
    setup_origin(world, None, status_placeholder_item(), "in_progress")
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", f"{out.kind}: {out.message}"
    assert_claimed_by(world, A, base)


# --- 3. export-ignore (#814): direct reads read the file -----------------------------------


@pytest.mark.parametrize(
    "attributes",
    ["kanban-work/** export-ignore\n", f"{OTHER} export-ignore\n", "*.md export-ignore\n"],
    ids=["board-root", "one-item", "all-md"],
)
def test_blobs_at_reads_export_ignored_file(world, attributes) -> None:
    setup_origin(world, attributes, item_text("in_progress", B, "EXP-002", "Y"), "rival")
    rev = origin_rev(world)
    names = [f"{EXP_DIR}/EXP-001-x.md", OTHER]

    blobs = service(world.a)._blobs_at(rev, blob_oids(world.a, rev, names))

    assert blobs == {n: committed_text(world, n) for n in names}


def test_claim_export_ignore_wip_full_refused_as_wip(world) -> None:
    setup_origin(
        world, "kanban-work/** export-ignore\n",
        item_text("in_progress", B, "EXP-002", "Y"), "rival",
    )
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert_refused_as_wip(world, out, base, before, rec)


def test_claim_export_ignore_wip_free_wins(world) -> None:
    setup_origin(
        world, "kanban-work/** export-ignore\n",
        item_text("ready", B, "EXP-002", "Y"), "rival",
    )
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", f"{out.kind}: {out.message}"
    assert_claimed_by(world, A, base)


# --- 4. git never inherits stdin ----------------------------------------------------------


def test_control_blobs_at_git_calls_never_inherit_stdin(world, monkeypatch) -> None:
    """Every git subprocess `_blobs_at` starts is given its stdin (`stdin=` or
    `input=`), through `subprocess.run` or `subprocess.Popen`."""
    setup_origin(world, SUBST_ALL_MD, id_placeholder_item(), ITEM_ID)
    rev = origin_rev(world)
    svc = service(world.a)
    oids = blob_oids(world.a, rev, [f"{EXP_DIR}/EXP-001-x.md", OTHER])
    calls: list[tuple[list[str], bool]] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def given(kwargs: dict[str, Any]) -> bool:
        return kwargs.get("stdin") is not None or "input" in kwargs

    def run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git":
            calls.append((list(map(str, cmd)), given(kwargs)))
        return real_run(cmd, *args, **kwargs)

    class Popen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, cmd: Any, *args: Any, **kwargs: Any) -> None:
            if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git":
                calls.append((list(map(str, cmd)), given(kwargs)))
            super().__init__(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)

    svc._blobs_at(rev, oids)

    assert calls, "no git subprocess seen"
    inherited = [" ".join(c) for c, ok in calls if not ok]
    assert not inherited, "git calls inheriting stdin:\n" + "\n".join(inherited)

