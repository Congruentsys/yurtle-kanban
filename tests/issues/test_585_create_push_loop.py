"""Issue #585 — ``create --push``: one explicit ref, scoped rollback, clean tree on failure.

Found by the adversarial review on #574 (B1/B2). ``create_item_and_push`` pulled
``origin main`` but pushed with a bare ``git push``; a rejected push was undone with a
mixed ``reset HEAD~1`` that left ``_ID_ALLOCATIONS.json`` dirty, so every later pull
failed (only logged) and the retries ran on a stale base.

Decided behaviour (all through the real CLI, ``create expedition <title> --push``,
against REAL git: a bare remote with default branch ``main`` and two clones, A and B):

1. Lost race: B pushes between A's fetch and A's push. A's push is rejected, A retries
   on the fresh base and succeeds with a NEW id (B holds EXP-001, A gets EXP-002, no id
   appears twice on origin/main). A's tree is clean afterwards: no leftover file from
   the losing attempt, no dirty allocation file.
2. Retries exhausted (B wins before every one of A's pushes): non-zero exit, the
   message says why, A's tree is clean and A's HEAD has no commit that origin/main
   lacks. The number of push attempts is bounded.
3. Remote configured but unreachable (origin points at a missing path): fails closed.
   Chosen behaviour: non-zero exit, NO local commit (HEAD unchanged), no item file left
   in the tree, clean tree, and the message names the pull/fetch/remote failure.
4. On a feature branch whose upstream is ``origin/<feature>``: ``create --push`` never
   merges main into the feature branch and never pushes the feature branch — the
   feature ref (local and on origin) is unchanged and HEAD stays on it. Either outcome
   is accepted: (a) success, and origin/main has the new item; or (b) a clear refusal
   (non-zero exit, the message names the branch or the default branch) with origin/main
   unchanged.
5. Unrelated user work (an unstaged edit to a tracked file, a staged new file, an
   untracked file) survives every path above untouched: same content, same index
   state, and none of it is committed or pushed to origin/main. (A fix may instead
   refuse on a dirty tree; success is not asserted here.)
6. Control: the happy path creates, commits and pushes exactly one item.

Interleavings are deterministic, not timed: ``subprocess.run`` is wrapped, and just
before any ``git ... push`` the service issues, clone B pushes a commit to origin/main
(the wrapper itself uses the original ``subprocess.run``).

Not covered here: ``next-id`` (``allocate_next_id``) has its own, separate retry path
(bare ``push``, ``pull --rebase``) — it does not share this loop.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

EXP_DIR = "kanban-work/expeditions"
TITLE = "Alpha From A"
FEATURE = "feat/side-work"
ID_RE = re.compile(r"^(EXP-\d+)")

_ORIG_RUN = subprocess.run


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = _ORIG_RUN(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {result.stderr}")
    return result.stdout


def _configure(clone: Path) -> None:
    for key, value in (
        ("user.email", "t@t.com"),
        ("user.name", clone.name),
        ("commit.gpgsign", "false"),
        ("pull.rebase", "false"),
        ("core.hooksPath", "/dev/null"),
    ):
        git(clone, "config", key, value)


class World:
    """A bare remote (default branch main) plus two clones, A (under test) and B."""

    def __init__(self, tmp_path: Path) -> None:
        self.remote = tmp_path / "remote.git"
        self.a = tmp_path / "A"
        self.b = tmp_path / "B"
        self.b_pushes = 0
        git(tmp_path, "init", "--bare", "-b", "main", str(self.remote))
        git(tmp_path, "clone", str(self.remote), str(self.a))
        _configure(self.a)
        git(self.a, "checkout", "-B", "main")
        (self.a / EXP_DIR).mkdir(parents=True)
        (self.a / EXP_DIR / ".gitkeep").write_text("")
        (self.a / "kanban-work" / "signals").mkdir(parents=True)
        (self.a / "kanban-work" / "signals" / ".gitkeep").write_text("")
        (self.a / "README.md").write_text("readme\n")
        config = KanbanConfig(
            theme="nautical",
            paths=PathConfig(
                root="kanban-work/",
                scan_paths=["kanban-work/expeditions/", "kanban-work/signals/"],
            ),
        )
        config.save(self.a / ".kanban" / "config.yaml")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-m", "seed")
        git(self.a, "push", "-u", "origin", "main")
        git(tmp_path, "clone", str(self.remote), str(self.b))
        _configure(self.b)

    # --- remote views -------------------------------------------------------
    def remote_sha(self, ref: str = "main") -> str:
        return git(self.remote, "rev-parse", ref).strip()

    def remote_files(self, ref: str = "main") -> list[str]:
        return git(self.remote, "ls-tree", "-r", "--name-only", ref).split()

    def remote_items(self) -> dict[str, list[str]]:
        """id -> [paths] for every item file on origin/main."""
        found: dict[str, list[str]] = {}
        for path in self.remote_files():
            if path.startswith(EXP_DIR + "/"):
                m = ID_RE.match(Path(path).name)
                if m:
                    found.setdefault(m.group(1), []).append(path)
        return found

    def remote_show(self, path: str) -> str:
        return git(self.remote, "show", f"main:{path}")

    # --- clone B, the rival -----------------------------------------------------
    def b_push_item(self) -> None:
        """B creates the next item on origin/main and pushes it (as a rival create would)."""
        git(self.b, "fetch", "origin")
        git(self.b, "reset", "--hard", "origin/main")
        exp = self.b / EXP_DIR
        nums = [
            int(m.group(1)[4:])
            for p in exp.iterdir()
            if (m := ID_RE.match(p.name))
        ]
        num = max(nums, default=0) + 1
        item_id = f"EXP-{num:03d}"
        self.b_pushes += 1
        (exp / f"{item_id}-rival-{self.b_pushes}.md").write_text(
            f"---\nid: {item_id}\ntitle: \"Rival {self.b_pushes}\"\n"
            "type: expedition\nstatus: backlog\n---\n\n# Rival\n"
        )
        alloc = self.b / ".kanban" / "_ID_ALLOCATIONS.json"
        records = json.loads(alloc.read_text()) if alloc.exists() else []
        records.append({"id": item_id, "prefix": "EXP", "number": num})
        alloc.write_text(json.dumps(records, indent=2))
        git(self.b, "add", "-A")
        git(self.b, "commit", "-m", f"Create {item_id}: rival")
        git(self.b, "push", "origin", "HEAD:refs/heads/main")


def _is_git_push(cmd: Any) -> bool:
    if isinstance(cmd, (list, tuple)) and cmd:
        return Path(str(cmd[0])).name == "git" and "push" in [str(c) for c in cmd[1:]]
    if isinstance(cmd, str):
        return bool(re.search(r"\bgit\b.*\bpush\b", cmd))
    return False


class PushHook:
    """Wrap subprocess.run: call ``before`` just ahead of each git push the code issues."""

    def __init__(self, before: Callable[[int], None], limit: int = 25) -> None:
        self.before = before
        self.limit = limit
        self.pushes = 0

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        cmd = args[0] if args else kwargs.get("args")
        if _is_git_push(cmd):
            self.pushes += 1
            if self.pushes > self.limit:
                raise RuntimeError(f"unbounded push loop: {self.pushes} pushes")
            self.before(self.pushes)
        return _ORIG_RUN(*args, **kwargs)


def porcelain(clone: Path) -> list[str]:
    return sorted(git(clone, "status", "--porcelain", "--untracked-files=all").splitlines())


def run_create(world: World, monkeypatch: pytest.MonkeyPatch, title: str = TITLE):
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, ["create", "expedition", title, "--push"])


def output_of(result: Any) -> str:
    return (result.output or "") + (str(result.exception) if result.exception else "")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


def _items_titled(world: World, title: str) -> list[str]:
    return [
        p for paths in world.remote_items().values() for p in paths
        if title in world.remote_show(p)
    ]


# --- scenarios (shared by the per-path tests and the user-work test) ----------------


def scenario_happy(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    pass


def scenario_lost_race(world: World, monkeypatch: pytest.MonkeyPatch) -> PushHook:
    hook = PushHook(lambda n: world.b_push_item() if n == 1 else None)
    monkeypatch.setattr(subprocess, "run", hook)
    return hook


def scenario_exhausted(world: World, monkeypatch: pytest.MonkeyPatch) -> PushHook:
    hook = PushHook(lambda n: world.b_push_item())
    monkeypatch.setattr(subprocess, "run", hook)
    return hook


def scenario_unreachable(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))


def scenario_feature_branch(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    git(world.a, "checkout", "-b", FEATURE)
    (world.a / "feature.txt").write_text("feature work\n")
    git(world.a, "add", "feature.txt")
    git(world.a, "commit", "-m", "feature work")
    git(world.a, "push", "-u", "origin", FEATURE)
    world.b_push_item()  # main moves on, so a `pull origin main` would merge it in


SCENARIOS = {
    "happy": scenario_happy,
    "lost_race": scenario_lost_race,
    "exhausted": scenario_exhausted,
    "unreachable": scenario_unreachable,
    "feature_branch": scenario_feature_branch,
}


# --- 1. lost race ----------------------------------------------------------------


def test_lost_race_retries_on_fresh_base_with_new_id(world, monkeypatch) -> None:
    hook = scenario_lost_race(world, monkeypatch)
    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert world.b_pushes == 1
    assert hook.pushes >= 2, f"A never retried its push: {out}"
    assert result.exit_code == 0, out
    items = world.remote_items()
    assert all(len(paths) == 1 for paths in items.values()), f"duplicate id: {items}"
    assert sorted(items) == ["EXP-001", "EXP-002"], items
    mine = _items_titled(world, TITLE)
    assert mine and Path(mine[0]).name.startswith("EXP-002"), mine
    assert "EXP-002" in out
    assert porcelain(world.a) == [], "A's tree is dirty after the retry"


# --- 2. retries exhausted -----------------------------------------------------------


def test_retries_exhausted_fails_clean(world, monkeypatch) -> None:
    hook = scenario_exhausted(world, monkeypatch)
    head_before = git(world.a, "rev-parse", "HEAD").strip()
    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert not isinstance(result.exception, RuntimeError), out  # bounded
    assert 1 <= hook.pushes <= 10, hook.pushes
    assert result.exit_code != 0, out
    assert re.search(r"retr|reject|race|lost|conflict|attempt", out, re.I), out
    assert _items_titled(world, TITLE) == [], "A's item reached origin/main"
    assert porcelain(world.a) == [], "A's tree is dirty after giving up"
    stray = git(world.a, "rev-list", "HEAD", "--not", world.remote_sha()).split()
    assert stray == [], f"stray local commit(s) on A: {stray}"
    head_after = git(world.a, "rev-parse", "HEAD").strip()
    assert head_after == head_before or head_after in git(
        world.remote, "rev-list", "main"
    ).split()


# --- 3. remote unreachable ----------------------------------------------------------


def test_unreachable_remote_fails_closed(world, monkeypatch) -> None:
    scenario_unreachable(world, monkeypatch)
    head_before = git(world.a, "rev-parse", "HEAD").strip()
    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert result.exit_code != 0, out
    assert re.search(r"pull|fetch|remote|origin|unreachable", out, re.I), out
    assert git(world.a, "rev-parse", "HEAD").strip() == head_before, "local commit left"
    assert porcelain(world.a) == [], "A's tree is dirty"
    assert not [p for p in (world.a / EXP_DIR).iterdir() if ID_RE.match(p.name)]


# --- 4. feature branch --------------------------------------------------------------


def test_feature_branch_uses_default_branch_ref_only(world, monkeypatch) -> None:
    scenario_feature_branch(world, monkeypatch)
    feat_local = git(world.a, "rev-parse", FEATURE).strip()
    feat_remote = world.remote_sha(FEATURE)
    main_before = world.remote_sha("main")
    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert git(world.a, "rev-parse", FEATURE).strip() == feat_local, (
        "the feature branch moved (main merged in, or the item committed onto it)"
    )
    assert world.remote_sha(FEATURE) == feat_remote, "the feature branch was pushed"
    assert git(world.a, "rev-parse", "--abbrev-ref", "HEAD").strip() == FEATURE
    assert porcelain(world.a) == []
    if result.exit_code == 0:  # (a) created on the default branch
        assert len(_items_titled(world, TITLE)) == 1, world.remote_items()
    else:  # (b) refused clearly
        assert re.search(r"branch|main", out, re.I), out
        assert world.remote_sha("main") == main_before


# --- 5. unrelated user work survives --------------------------------------------------


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_user_work_survives(world, monkeypatch, name) -> None:
    SCENARIOS[name](world, monkeypatch)
    (world.a / "README.md").write_text("readme\nuser edit, unstaged\n")
    (world.a / "staged.txt").write_text("user staged work\n")
    _ORIG_RUN(["git", "add", "staged.txt"], cwd=world.a, check=True, capture_output=True)
    (world.a / "scratch.txt").write_text("user untracked\n")
    status_before = porcelain(world.a)

    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert (world.a / "README.md").read_text() == "readme\nuser edit, unstaged\n", out
    assert (world.a / "scratch.txt").read_text() == "user untracked\n"
    assert git(world.a, "show", ":staged.txt", check=False) == "user staged work\n", (
        "the staged file is no longer in the index"
    )
    assert "staged.txt" in git(world.a, "diff", "--cached", "--name-only").split(), (
        "the staged file is no longer staged (committed, or unstaged)"
    )
    assert porcelain(world.a) == status_before, out
    if name != "unreachable":
        remote = world.remote_files()
        assert "staged.txt" not in remote and "scratch.txt" not in remote
        assert world.remote_show("README.md") == "readme\n"


# --- 6. control --------------------------------------------------------------------


def test_happy_path_creates_commits_pushes_one_item(world, monkeypatch) -> None:
    before = int(git(world.remote, "rev-list", "--count", "main"))
    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert result.exit_code == 0, out
    assert "EXP-001" in out
    assert list(world.remote_items()) == ["EXP-001"]
    assert len(_items_titled(world, TITLE)) == 1
    assert int(git(world.remote, "rev-list", "--count", "main")) == before + 1
    assert porcelain(world.a) == []
