"""Issue #590 — `next-id` on the #585 compare-and-swap path; HDD ids from the fetched base.

Scope from the [steer] on #590 (after #584, #585 and #603 merged). G1: no duplicate ids.
Reuses the #585/#603 real-git harness (bare remote + clones A and B, the
``subprocess.run`` wrappers), so every interleaving is deterministic.

Decided behaviour:

1. ``next-id EXP`` run from a feature branch, with an unstaged edit, a staged file and an
   untracked file, allocates against origin/<default> with a compare-and-swap push.
   The feature branch (local and on origin), the index and the worktree are
   untouched, and the allocation record lands on origin/main. The id is the next one
   past what origin/main already holds.
2. A lost race in ``next-id`` (B pushes between A's fetch and A's push) retries on a
   fresh base and returns a different id. origin/main holds no allocation id twice,
   and A's tree is clean.
3. An unreachable remote or a refused push fails closed with git's own reason, as
   ``create --push`` does (#585): non-zero exit, the reason in the output, a refusal
   pushed only once, no local commit, and a clean tree.
4. HDD ``idea``/``literature``/``measure``/``experiment``/``hypothesis create --push``,
   when a rival has already pushed the id A's local scan would pick (A is stale, or B
   wins the race just before A's push), ends with TWO DIFFERENT ids on origin/main.
   This is the reviewer's IDEA-R-001 duplicate repro from PR #594.
5. A remote item whose frontmatter id does not match its filename (``notes.md`` with
   ``id: EXP-007``) is counted by the remote id scan. The next id that
   ``create --push`` and ``next-id`` hand out is EXP-008.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import (
    FEATURE,
    PushHook,
    World,
    git,
    output_of,
    porcelain,
    run_create,
    scenario_feature_branch,
)
from tests.issues.test_603_push_failure_messages import (
    HDD_DIRS,
    invoke,
    reconfigure,
    reject_with_hook,
)
from yurtle_kanban import config as config_mod

ALLOC = ".kanban/_ID_ALLOCATIONS.json"


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def b_push(world: World, files: dict[str, str], alloc: list[dict] | None = None) -> None:
    """B writes `files` (and appends `alloc` records) on a fresh origin/main and pushes."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", "origin/main")
    for rel, text in files.items():
        path = world.b / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    if alloc:
        lock = world.b / ALLOC
        records = json.loads(lock.read_text()) if lock.exists() else []
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps(records + alloc, indent=2))
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "rival")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")


def remote_allocs(world: World) -> list[str]:
    shown = subprocess.run(
        ["git", "show", f"main:{ALLOC}"], cwd=world.remote, capture_output=True, text=True
    )
    return [r["id"] for r in json.loads(shown.stdout)] if shown.returncode == 0 else []


def next_id(world: World, monkeypatch, prefix: str = "EXP"):
    return invoke(world, monkeypatch, ["next-id", prefix, "--json"])


def allocated(out: str) -> str | None:
    m = re.search(r'"id":\s*"([A-Z]+-\d+)"', out)
    return m.group(1) if m else None


def frontmatter_id(text: str) -> str | None:
    m = re.search(r'^id:\s*"?([^"\s]+)"?\s*$', text, re.M)
    return m.group(1) if m else None


# --- 1. next-id from a dirty feature branch ---------------------------------------------


def test_next_id_on_dirty_feature_branch_is_cas_on_default(world, monkeypatch) -> None:
    scenario_feature_branch(world, monkeypatch)  # B has pushed EXP-001 to main
    (world.a / "README.md").write_text("readme\nuser edit, unstaged\n")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")
    (world.a / "scratch.txt").write_text("user untracked\n")
    feat_local = git(world.a, "rev-parse", FEATURE).strip()
    feat_remote = world.remote_sha(FEATURE)
    main_before = int(git(world.remote, "rev-list", "--count", "main"))
    status_before = porcelain(world.a)
    cached_before = git(world.a, "diff", "--cached")

    result = next_id(world, monkeypatch)
    out = output_of(result)

    assert result.exit_code == 0, out
    assert allocated(out) == "EXP-002", out
    assert git(world.a, "rev-parse", FEATURE).strip() == feat_local, "feature branch moved"
    assert world.remote_sha(FEATURE) == feat_remote, "the feature branch was pushed"
    assert git(world.a, "rev-parse", "--abbrev-ref", "HEAD").strip() == FEATURE
    assert porcelain(world.a) == status_before, out
    assert git(world.a, "diff", "--cached") == cached_before, "the index changed"
    assert (world.a / "README.md").read_text() == "readme\nuser edit, unstaged\n"
    assert remote_allocs(world).count("EXP-002") == 1, remote_allocs(world)
    assert int(git(world.remote, "rev-list", "--count", "main")) == main_before + 1
    assert "staged.txt" not in world.remote_files()


# --- 2. a lost race in next-id ----------------------------------------------------------


def test_next_id_lost_race_retries_with_a_new_id(world, monkeypatch) -> None:
    hook = PushHook(lambda n: world.b_push_item() if n == 1 else None)
    monkeypatch.setattr(subprocess, "run", hook)
    result = next_id(world, monkeypatch)
    out = output_of(result)

    assert world.b_pushes == 1
    assert hook.pushes >= 2, f"never retried: {out}"
    assert result.exit_code == 0, out
    assert allocated(out) == "EXP-002", out
    ids = remote_allocs(world)
    assert sorted(ids) == ["EXP-001", "EXP-002"], ids
    assert porcelain(world.a) == [], "A's tree is dirty after the retry"


# --- 3. next-id fails closed ------------------------------------------------------------


def test_next_id_unreachable_remote_fails_closed(world, monkeypatch) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    head = git(world.a, "rev-parse", "HEAD").strip()
    result = next_id(world, monkeypatch)
    out = " ".join(output_of(result).split())

    assert result.exit_code != 0, out
    assert re.search(r"does not appear to be a git repository|could not read", out, re.I), out
    assert git(world.a, "rev-parse", "HEAD").strip() == head, "a local commit was left"
    assert porcelain(world.a) == [], out


def test_next_id_refused_push_fails_closed_once(world, monkeypatch) -> None:
    reject_with_hook(world)
    hook = PushHook(lambda n: None)
    monkeypatch.setattr(subprocess, "run", hook)
    before = world.remote_sha()
    result = next_id(world, monkeypatch)
    out = " ".join(output_of(result).split())

    assert result.exit_code != 0, out
    assert "protected branch" in out, out
    assert hook.pushes == 1, f"a refusal was retried {hook.pushes} times"
    assert world.remote_sha() == before
    stray = git(world.a, "rev-list", "HEAD", "--not", world.remote_sha()).split()
    assert stray == [], f"stray local commit(s): {stray}"
    assert porcelain(world.a) == [], out


# --- 4. HDD --push: a rival's id is never reused ------------------------------------------

TITLE_A = "Alpha From A"

# command -> (argv, directory, the id A's local scan would pick, its allocation prefix)
HDD = {
    "idea": (["idea", "create", TITLE_A, "--push"], "ideas", "IDEA-R-001", "IDEA-R"),
    "literature": (["literature", "create", TITLE_A, "--push"], "literature", "LIT-001", "LIT"),
    "measure": (
        ["measure", "create", TITLE_A, "--unit", "count", "--category", "coverage", "--push"],
        "measures", "M-001", "M",
    ),
    "experiment": (
        ["experiment", "create", "--title", TITLE_A, "--push"], "experiments", "EXPR-001", "EXPR",
    ),
    "hypothesis": (["hypothesis", "create", TITLE_A, "--push"], "hypotheses", "H-001", "H"),
}


def _rival(world: World, name: str) -> None:
    _, sub, rid, prefix = HDD[name]
    b_push(
        world,
        {f"research/{sub}/{rid}-rival.md": f'---\nid: {rid}\ntitle: "Rival"\n---\n\n# Rival\n'},
        [{"id": rid, "prefix": prefix, "number": 1}],
    )


@pytest.mark.parametrize("timing", ["stale", "race"])
@pytest.mark.parametrize("name", list(HDD))
def test_hdd_create_push_never_duplicates_a_rival_id(world, monkeypatch, name, timing) -> None:
    reconfigure(world, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])
    argv, sub, rid, _ = HDD[name]
    if timing == "stale":  # B pushed before A ran; A's checkout does not have it
        _rival(world, name)
    else:  # B wins just before A's first push
        monkeypatch.setattr(
            subprocess, "run", PushHook(lambda n: _rival(world, name) if n == 1 else None)
        )
    result = invoke(world, monkeypatch, argv)
    out = output_of(result)

    assert result.exit_code == 0, out
    files = [
        p for p in world.remote_files()
        if p.startswith(f"research/{sub}/") and p.endswith(".md")
    ]
    ids = [frontmatter_id(world.remote_show(p)) for p in files]
    assert len(files) == 2, files
    assert len(set(ids)) == 2, f"duplicate id on origin/main: {dict(zip(files, ids))}"
    assert rid in ids
    assert porcelain(world.a) == [], out


# --- 5. the remote scan reads frontmatter ids -----------------------------------------------


def _odd_named_item(world: World) -> None:
    b_push(world, {
        "kanban-work/expeditions/notes.md":
            '---\nid: EXP-007\ntitle: "Odd name"\ntype: expedition\nstatus: backlog\n---\n'
    })


def test_create_push_counts_remote_frontmatter_ids(world, monkeypatch) -> None:
    _odd_named_item(world)
    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert result.exit_code == 0, out
    assert "EXP-008" in out, out
    assert any(
        p.startswith("kanban-work/expeditions/EXP-008") for p in world.remote_files()
    ), world.remote_files()


def test_next_id_counts_remote_frontmatter_ids(world, monkeypatch) -> None:
    _odd_named_item(world)
    result = next_id(world, monkeypatch)
    out = output_of(result)

    assert result.exit_code == 0, out
    assert allocated(out) == "EXP-008", out
