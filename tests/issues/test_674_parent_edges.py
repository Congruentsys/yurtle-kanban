"""Issue #674 — the edges of a parent link riding in the child's commit (#645).

Decided behaviour ([steer] on #674):

(1) Deleted-parent wording: a parent absent from origin/<default> gives 'push X first'
    only when X exists only in this checkout. When a rival removed it from origin,
    the message says X is not on origin/<default> (it may have been removed there);
    nothing was created. Control: a parent that only exists locally still says
    'push X first'.
(2) Dirty parent after a landed CAS: when the local fast-forward fails because the
    parent file has uncommitted edits, the pull note names it: 'commit or stash your
    edit to <parent path> before pulling'.
(3) No-remote path: with a parent file that has uncommitted edits, the create is
    REFUSED before anything is written, naming the parent; the link commit never
    sweeps in unrelated edits (G1). Control: a clean parent gets the link in the
    child's single commit, as today.
(4) The parent's linked text is computed BEFORE the child file is written, so an
    unexpected error leaves nothing half-written.

Reuses the #645 harness (World, seed_on_origin, PushRecorder, rival pushes).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import World, git, output_of, porcelain
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import (
    HYP,
    PAPER,
    commits_since,
    frontmatter_id,
    hdd,
    links,
    seed_on_origin,
    spy,
    touched,
    turtle_block,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.service import KanbanService

LOCAL_EDIT = "Local edit 674: not committed, must never be swept in."
PUSH_FIRST_RE = re.compile(r"push\s+\S+\s+first", re.I)


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def flat(result) -> str:
    return " ".join(output_of(result).split())


def hypothesis_files(world: World) -> list[str]:
    root = world.a / "research" / "hypotheses"
    return sorted(
        str(p.relative_to(world.a)) for p in root.rglob("*.md")
    ) if root.exists() else []


def dirty_parent(world: World) -> str:
    path = world.a / PAPER
    path.write_text(path.read_text() + f"\n{LOCAL_EDIT}\n")
    return path.read_text()


def drop_remote(world: World) -> None:
    git(world.a, "remote", "remove", "origin")
    assert git(world.a, "remote").strip() == ""


# --- (1) a rival deletes the parent on origin between A's fetch and push ---------------


def test_rival_deleted_parent_is_not_push_first(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    head = git(world.a, "rev-parse", "HEAD").strip()
    rival: dict[str, str] = {}

    def rival_deletes_parent(n: int) -> None:
        if n != 1:
            return
        git(world.b, "fetch", "origin")
        git(world.b, "reset", "--hard", "origin/main")
        git(world.b, "rm", "-q", PAPER)
        git(world.b, "commit", "-m", "rival removes the paper")
        git(world.b, "push", "origin", "HEAD:refs/heads/main")
        rival["sha"] = git(world.b, "rev-parse", "HEAD").strip()

    spy(monkeypatch, rival_deletes_parent)
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)

    assert "sha" in rival, f"the rival never ran: A made no push:\n{out}"
    assert result.exit_code != 0, f"a parent removed from origin was not refused:\n{out}"
    assert "Traceback" not in out, out
    assert world.remote_sha() == rival["sha"], "A pushed after the parent was removed"
    assert "PAPER-130" in out, f"the refusal does not name the parent:\n{out}"
    assert not PUSH_FIRST_RE.search(out), (
        f"the parent IS pushed (a rival removed it from origin), yet the refusal "
        f"says to push it first:\n{out}"
    )
    assert "not on origin/main" in out, (
        f"the refusal does not say the parent is not on origin/main:\n{out}"
    )
    assert "nothing was created" in out, out
    assert git(world.a, "rev-parse", "HEAD").strip() == head, "a local commit was made"
    assert porcelain(world.a) == [], porcelain(world.a)


def test_control_parent_only_local_says_push_first(world, monkeypatch) -> None:
    hdd(world)
    result = invoke(world, monkeypatch, ["paper", "create", "130", "A paper"])
    assert result.exit_code == 0, output_of(result)
    config_mod._theme_cache.clear()

    spy(monkeypatch)
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)

    assert result.exit_code != 0, out
    assert re.search(r"push\s+PAPER-130\s+first", out), (
        f"a parent that only exists locally no longer says 'push PAPER-130 first':\n{out}"
    )


# --- (2) from main, a dirty parent blocks the local fast-forward ----------------------


def test_dirty_parent_pull_note_names_it(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    base = world.remote_sha()
    head = git(world.a, "rev-parse", "HEAD").strip()
    edited = dirty_parent(world)

    spy(monkeypatch)
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)

    assert result.exit_code == 0, out
    new = commits_since(world, base)
    assert len(new) == 1, f"the CAS commit did not land as one commit: {new}"
    assert PAPER in touched(world, new[0]), "the parent link is not in the child's commit"
    assert LOCAL_EDIT not in world.remote_show(PAPER), "the uncommitted edit was published"
    assert git(world.a, "rev-parse", "HEAD").strip() == head, (
        "the checkout was fast-forwarded over a dirty parent"
    )
    assert (world.a / PAPER).read_text() == edited, "the local edit to the parent was lost"
    assert PAPER in "".join(out.split()), f"the pull note does not name the dirty parent {PAPER}:\n{out}"
    assert re.search(r"commit or stash", out, re.I), (
        f"the pull note does not say to commit or stash the parent edit:\n{out}"
    )
    assert re.search(r"before pulling", out, re.I), out


# --- (3) no remote: a dirty parent refuses the create before anything is written -----


def test_no_remote_dirty_parent_is_refused(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    head = git(world.a, "rev-parse", "HEAD").strip()
    hyps_before = hypothesis_files(world)
    edited = dirty_parent(world)
    status_before = porcelain(world.a)

    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)

    assert result.exit_code != 0, (
        f"a create linking a parent with uncommitted edits was not refused:\n{out}"
    )
    assert "Traceback" not in out, out
    assert "PAPER-130" in out or PAPER in out, f"the refusal does not name the parent:\n{out}"
    assert git(world.a, "rev-parse", "HEAD").strip() == head, (
        "a commit was made: " + git(world.a, "show", "--stat", "--format=%s", "HEAD")
    )
    assert hypothesis_files(world) == hyps_before, (
        f"a child file was written: {hypothesis_files(world)}"
    )
    assert (world.a / PAPER).read_text() == edited, "the parent's local edit was touched"
    assert porcelain(world.a) == status_before, porcelain(world.a)


def test_control_no_remote_clean_parent_links_in_one_commit(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    head = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)

    assert result.exit_code == 0, out
    new = git(world.a, "rev-list", f"{head}..HEAD").split()
    assert len(new) == 1, f"expected ONE local commit, got {len(new)}"
    files = set(git(world.a, "show", "--name-only", "--format=", new[0]).split())
    children = [p for p in files if p.startswith("research/hypotheses/") and p != PAPER]
    assert len(children) == 1, files
    assert PAPER in files, f"the parent link is not in the child's commit: {files}"
    child_id = frontmatter_id((world.a / children[0]).read_text())
    assert links(turtle_block((world.a / PAPER).read_text()), HYP, child_id)
    assert porcelain(world.a) == [], porcelain(world.a)


# --- (4) the parent's linked text is computed before the child is written -----------


def test_link_error_leaves_no_child_file(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    head = git(world.a, "rev-parse", "HEAD").strip()
    hyps_before = hypothesis_files(world)

    def boom(self, *args, **kwargs):
        raise RuntimeError("link computation failed (674)")

    monkeypatch.setattr(KanbanService, "_linked_parent_text", boom)
    result = invoke(world, monkeypatch, HYP.argv)

    assert result.exit_code != 0, output_of(result)
    assert hypothesis_files(world) == hyps_before, (
        f"the child file was written before the parent link was computed: "
        f"{hypothesis_files(world)}"
    )
    assert git(world.a, "rev-parse", "HEAD").strip() == head, "a commit was made"
    assert porcelain(world.a) == [], porcelain(world.a)
