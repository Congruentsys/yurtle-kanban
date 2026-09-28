"""Issue #995 — post-push leftovers from the #925 (PR #993) and #928 (PR #994) reviews.

1. ``_fast_forward_to``'s ``git merge --ff-only`` runs the user's post-merge hook, so it
   passes ``timeout=None`` (#584) instead of ``_git_run``'s 30 s default.
2. The "Timed out pushing…" branches of both compare-and-swap timeout handlers can't be
   reached now that the pushes have no timeout (#925): they are removed.
3. On a diverged ``main`` (local main holds a commit origin lacks, so the post-push
   fast-forward can't happen):
   - ``create --push`` says "not in this checkout yet" ONCE: no second warning of its own
     from ``_fast_forward_to``, and never git's multi-line ``hint:`` advice squashed onto
     one line; if git's failure is shown at all, only its ``error:``/``fatal:`` lines;
   - ``next-id`` prints a note of its own that the local checkout wasn't updated, with no
     ``hint:`` text.
4. (#992/#918 nit) ``update --help`` no longer says ``update --push`` is checked against
   origin's workflows: ``update --push`` never changes status, so no workflow rule applies.

Harnesses: the #585 ``World`` (a bare origin and two clones), the #888/#925
``_git_run`` spy. Scenario 3 runs the CLI as a real subprocess, so what lands on stderr
(the logger's warnings included) is what a user sees.
"""

from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import yurtle_kanban
from tests.issues.test_585_create_push_loop import _ORIG_RUN, EXP_DIR, World, git
from tests.test_634_explicit_ids_on_base import service
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"

_REAL_GIT_RUN = KanbanService._git_run
_SIG = inspect.signature(_REAL_GIT_RUN)

NOT_UPDATED = ("not updated", "not in this checkout", "does not show")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


# --- 1. the fast-forward merge runs the post-merge hook with no timeout ------------------


def test_fast_forward_merge_runs_with_no_timeout(world, monkeypatch) -> None:
    # origin moves ahead of A, so A's fast-forward really merges
    git(world.b, "pull", "--quiet", "origin", "main")
    (world.b / EXP_DIR / "EXP-001-plain.md").write_text("plain\n")
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "plain")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")
    git(world.a, "fetch", "origin")
    sha = world.remote_sha()

    seen: list[tuple[tuple[str, ...], Any]] = []

    def spy(self: KanbanService, *args: str, **kw: Any) -> Any:
        bound = _SIG.bind(self, *args, **kw)
        bound.apply_defaults()
        seen.append((args, bound.arguments.get("timeout", "<no timeout argument>")))
        return _REAL_GIT_RUN(self, *args, **kw)

    monkeypatch.setattr(KanbanService, "_git_run", spy)

    assert service(world)._fast_forward_to("main", sha) is True
    merges = [(a, t) for a, t in seen if a and a[0] == "merge"]
    assert merges, f"no `git merge` through _git_run: {[a for a, _ in seen]}"
    for args, timeout in merges:
        assert timeout is None, (
            f"`git {' '.join(args)}` runs the user's post-merge hook with "
            f"timeout={timeout!r}; #584: commands that run user hooks pass timeout=None"
        )


# --- 2. the unreachable "Timed out pushing" branches are gone ---------------------------


def test_no_timed_out_pushing_message_left_in_src() -> None:
    hits = [
        f"{p.relative_to(SRC)}:{n}"
        for p in sorted(SRC.rglob("*.py"))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "Timed out pushing" in line
    ]
    assert not hits, (
        "the compare-and-swap pushes run with timeout=None (#925), so a "
        f"'Timed out pushing' branch can't be reached; remove it: {hits}"
    )


# --- 3. a diverged main: one note, no git hint text -------------------------------------


def _diverge(world: World) -> None:
    """A's main gets a commit origin lacks: the post-push fast-forward can't happen."""
    (world.a / "local-only.txt").write_text("local\n")
    git(world.a, "add", "local-only.txt")
    git(world.a, "commit", "-m", "local only")


def _cli(world: World, *argv: str) -> tuple[int, str, str]:
    """The CLI as a real process in A: exit code, stdout, stderr (logger warnings
    included, as a user sees them)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    env.pop("YURTLE_AGENT", None)
    # the yurtle_kanban this test process imported (this checkout's src), not whatever
    # an editable install elsewhere points at
    pkg_root = str(Path(yurtle_kanban.__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [pkg_root, env.get("PYTHONPATH")]))
    done = _ORIG_RUN(
        [sys.executable, "-c", "from yurtle_kanban.cli import main; main()", *argv],
        cwd=world.a,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return done.returncode, done.stdout, done.stderr


def _note_count(text: str) -> int:
    low = " ".join(text.lower().split())
    return sum(low.count(p) for p in NOT_UPDATED)


def _assert_no_hint(text: str) -> None:
    assert "hint:" not in text.lower(), (
        f"git's `hint:` advice reached the user (show only error:/fatal: lines): {text!r}"
    )


def _assert_git_failure_only_error_fatal(text: str) -> None:
    """Where git's own words are quoted, only its error:/fatal: lines."""
    low = text.lower()
    for advice in ("diverging branches", "git merge --no-ff", "git rebase", "advice.diverging"):
        assert advice not in low, f"git's advice text {advice!r} reached the user: {text!r}"


def test_create_push_on_diverged_main_notes_once_without_hints(world) -> None:
    _diverge(world)
    before = world.remote_sha()

    code, out, err = _cli(world, "create", "expedition", "T", "--push")
    both = out + err

    assert code == 0, both
    assert "Traceback" not in both, both
    assert world.remote_sha() != before, f"the item never landed on origin: {both!r}"
    assert "not in this checkout yet" in " ".join(both.split()), both
    _assert_no_hint(both)
    _assert_git_failure_only_error_fatal(both)
    assert _note_count(both) == 1, (
        f"the checkout-not-updated note should appear once, create's own; got "
        f"{_note_count(both)}:\nstdout={out!r}\nstderr={err!r}"
    )


def test_next_id_on_diverged_main_notes_checkout_not_updated(world) -> None:
    _diverge(world)
    before = world.remote_sha()

    code, out, err = _cli(world, "next-id", "EXP")
    both = out + err

    assert code == 0, both
    assert "Traceback" not in both, both
    assert world.remote_sha() != before, f"the allocation never landed on origin: {both!r}"
    assert "EXP-001" in out, both
    _assert_no_hint(both)
    _assert_git_failure_only_error_fatal(both)
    assert _note_count(both) >= 1, (
        f"next-id says nothing about the local checkout not being updated:\n"
        f"stdout={out!r}\nstderr={err!r}"
    )


def test_next_id_note_is_its_own_not_only_the_log_warning(world, monkeypatch) -> None:
    """next-id prints the note itself (its command output), not only via the logger."""
    _diverge(world)
    monkeypatch.chdir(world.a)

    result = CliRunner().invoke(main, ["next-id", "EXP"])

    assert result.exit_code == 0, result.output
    assert _note_count(result.output) >= 1, (
        f"next-id's own output doesn't say the local checkout wasn't updated: {result.output!r}"
    )
    _assert_no_hint(result.output)


# --- 4. update --help: no workflow claim for --push -------------------------------------


def test_update_help_does_not_tie_push_to_workflows() -> None:
    result = CliRunner().invoke(main, ["update", "--help"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "themes and workflows define it" not in text, text
    assert "workflow" not in text.lower(), (
        "update --push never changes status, so no workflow rule applies to it "
        f"(#992/#918): {text!r}"
    )
