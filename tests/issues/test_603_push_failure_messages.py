"""Issue #603 — `--push` failure messages, and the #594 round-2 follow-ups.

Trimmed to what is still missing on main after #585 (PR #594) and #584 (PR #609).
Reuses #585's real-git harness (bare remote + clones A and B, the ``subprocess.run``
wrappers), so every interleaving is deterministic.

Decided behaviour:

1. Every ``create --push`` failure shows the real git reason in the CLI. On main only
   the retries-exhausted path still says just "rejected N time(s) (lost the race…)";
   it must also carry the last rejection git printed (``[rejected]`` /
   ``non-fast-forward`` / ``fetch first``).
2. ``epic create --push`` against a remote whose pre-receive hook refuses exits 1 and
   prints the hook's reason (pin).
3. No failed ``--push`` leaves a stray ``.kanban/_ID_ALLOCATIONS.json`` (or any other
   file) in the clone: refusal, lost-race exhaustion, unreachable remote, push timeout,
   and ``epic create --push`` refusal (pins).
4. On a feature branch, where the item is not fast-forwarded into the checkout, every
   HDD create ``--push`` (pins: ``_print_created_file`` already does this on main) (``idea``, ``literature``, ``hypothesis``, ``experiment``,
   ``measure``) says to pull main and prints no ``File:`` line for a path that is not
   in the checkout. ``epic create --push`` is held to the same rule: it is another
   ``--push`` caller of the same service (it printed ``File:`` unconditionally).
5. Once the push has landed, a failure of the local fast-forward (``merge --ff-only``
   raising ``TimeoutExpired``) is NOT "nothing was created": the CLI reports success,
   the item is on origin/main, and a note says the local checkout was not updated.
6. Git's stderr in the core CLI failure line never shows a literal ``\\n``: real
   newlines, or one clean line.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator

import pytest
from click.testing import CliRunner

from tests.issues.test_585_create_push_loop import (
    FEATURE,
    TITLE,
    GitRecorder,
    PushHook,
    World,
    _no_traceback,
    _push_then_timeout,
    _timeout,
    git,
    output_of,
    porcelain,
    run_create,
    scenario_exhausted,
    scenario_feature_branch,
    scenario_unreachable,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

HDD_DIRS = ["ideas", "literature", "papers", "hypotheses", "experiments", "measures"]


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def reconfigure(world: World, theme: str, root: str, dirs: list[str]) -> None:
    """Commit a new board config (and its dirs) on main and push it."""
    for d in dirs:
        (world.a / d).mkdir(parents=True, exist_ok=True)
        (world.a / d / ".gitkeep").write_text("")
    KanbanConfig(theme=theme, paths=PathConfig(root=root, scan_paths=dirs)).save(
        world.a / ".kanban" / "config.yaml"
    )
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", f"board: {theme}")
    git(world.a, "push", "origin", "main")
    config_mod._theme_cache.clear()


def nautical_with_voyages(world: World) -> None:
    reconfigure(
        world, "nautical", "kanban-work/",
        ["kanban-work/expeditions/", "kanban-work/signals/", "kanban-work/voyages/"],
    )


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]):
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def reject_with_hook(world: World, text: str = "protected branch: main is locked") -> None:
    hook = world.remote / "hooks" / "pre-receive"
    lines = "".join(f"echo '{line}' >&2\n" for line in text.splitlines())
    hook.write_text(f"#!/bin/sh\n{lines}exit 1\n")
    hook.chmod(0o755)


def remote_files_titled(world: World, title: str, under: str) -> list[str]:
    return [
        p for p in world.remote_files()
        if p.startswith(under) and p.endswith(".md") and title in world.remote_show(p)
    ]


def no_file_line(out: str) -> bool:
    return not re.search(r"^\s*File:", out, re.M)


# --- 1. the exhausted-race failure carries git's reason ------------------------------------


def test_exhausted_failure_shows_git_reason(world, monkeypatch) -> None:
    scenario_exhausted(world, monkeypatch)
    result = run_create(world, monkeypatch)
    out = output_of(result)
    flat = " ".join(out.split())

    assert result.exit_code != 0, out
    assert re.search(r"\[rejected\]|non-fast-forward|fetch first", flat), (
        f"only the generic retry message, no git reason: {out}"
    )


# --- 2. epic create --push refused by a hook ------------------------------------------------


def test_epic_create_push_refused_exits_1_with_reason(world, monkeypatch) -> None:
    nautical_with_voyages(world)
    reject_with_hook(world)
    before = world.remote_sha()
    result = invoke(world, monkeypatch, ["epic", "create", TITLE, "--push"])
    out = output_of(result)

    assert result.exit_code == 1, out
    assert "protected branch" in out, out
    assert world.remote_sha() == before


# --- 3. no stray file after a failed push ------------------------------------------------


def _refused(world, monkeypatch) -> None:
    reject_with_hook(world)


def _push_timeout(world, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", GitRecorder({"push": _push_then_timeout}))


FAILURES = {
    "refused": _refused,
    "exhausted": scenario_exhausted,
    "unreachable": scenario_unreachable,
    "push_timeout": _push_timeout,
}


@pytest.mark.parametrize("name", list(FAILURES))
def test_failed_create_push_leaves_no_stray_file(world, monkeypatch, name) -> None:
    FAILURES[name](world, monkeypatch)
    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert result.exit_code != 0, out
    assert not (world.a / ".kanban" / "_ID_ALLOCATIONS.json").exists(), out
    assert porcelain(world.a) == [], out


def test_failed_epic_create_push_leaves_no_stray_file(world, monkeypatch) -> None:
    nautical_with_voyages(world)
    reject_with_hook(world)
    result = invoke(world, monkeypatch, ["epic", "create", TITLE, "--push"])

    assert result.exit_code != 0, output_of(result)
    assert not (world.a / ".kanban" / "_ID_ALLOCATIONS.json").exists()
    assert porcelain(world.a) == []


# --- 4. feature branch: HDD (and epic) --push say to pull, show no missing File: -----------

HDD_CREATES = {
    "idea": ["idea", "create", TITLE, "--push"],
    "literature": ["literature", "create", TITLE, "--push"],
    "hypothesis": ["hypothesis", "create", TITLE, "--push"],
    "experiment": ["experiment", "create", "--title", TITLE, "--push"],
    "measure": [
        "measure", "create", TITLE, "--unit", "count", "--category", "coverage", "--push",
    ],
}


def _feature_branch_note(world: World, monkeypatch, argv: list[str], under: str) -> None:
    scenario_feature_branch(world, monkeypatch)
    result = invoke(world, monkeypatch, argv)
    out = output_of(result)
    flat = " ".join(out.split())

    assert result.exit_code == 0, out
    assert len(remote_files_titled(world, TITLE, under)) == 1, world.remote_files()
    assert git(world.a, "rev-parse", "--abbrev-ref", "HEAD").strip() == FEATURE
    assert re.search(r"\bpull\b[^.]*\bmain\b", flat, re.I), f"no hint to pull main: {out}"
    assert no_file_line(out), f"a File: path not in this checkout: {out}"


@pytest.mark.parametrize("name", list(HDD_CREATES))
def test_hdd_create_push_on_feature_branch_says_pull(world, monkeypatch, name) -> None:
    reconfigure(world, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])
    _feature_branch_note(world, monkeypatch, HDD_CREATES[name], "research/")


def test_epic_create_push_on_feature_branch_says_pull(world, monkeypatch) -> None:
    nautical_with_voyages(world)
    _feature_branch_note(
        world, monkeypatch, ["epic", "create", TITLE, "--push"], "kanban-work/voyages/"
    )


# --- 5. a failed local fast-forward after a landed push is still a success -----------------


def test_ff_timeout_after_landed_push_reports_success(world, monkeypatch) -> None:
    rec = GitRecorder({"merge": _timeout})
    monkeypatch.setattr(subprocess, "run", rec)
    result = run_create(world, monkeypatch)
    out = output_of(result)
    flat = " ".join(out.split())

    _no_traceback(result, out)
    assert rec.count("merge") >= 1, "the fast-forward step was never reached"
    assert list(world.remote_items()) == ["EXP-001"], "the push did not land"
    assert "nothing was created" not in flat.lower(), out
    assert result.exit_code == 0, out
    assert re.search(r"EXP-001", flat), out
    assert re.search(r"not (been )?updated|pull\b[^.]*\bmain", flat, re.I), (
        f"no note that the local checkout was not updated: {out}"
    )
    assert no_file_line(out), f"a File: path not in this checkout: {out}"


# --- 6. no literal \n from git's stderr ------------------------------------------------------


def test_cli_failure_line_has_no_literal_backslash_n(world, monkeypatch) -> None:
    reject_with_hook(world, "protected branch: main is locked\nask an admin to unlock it")
    hook = PushHook(lambda n: None)
    monkeypatch.setattr(subprocess, "run", hook)
    result = run_create(world, monkeypatch)
    out = output_of(result)

    assert result.exit_code != 0, out
    assert "protected branch" in out and "ask an admin" in out, out
    assert "\\n" not in out, f"literal \\n in the failure line: {out!r}"
    assert hook.pushes == 1
