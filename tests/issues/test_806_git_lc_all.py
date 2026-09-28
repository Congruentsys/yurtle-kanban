"""Issue #806 — git runs under ``LC_ALL=C``, so the messages the code matches are English.

``_race_to_branch`` / ``sync_and_push`` decide retry, ``push_refused`` and
``unreachable`` by matching git's stderr ('fetch first', 'non-fast-forward',
'[remote rejected]'). Under a translated locale git prints those in another language,
and a lost race could be misread as a refusal.

Decided spec (the [steer] on #806): every git subprocess whose output the code parses
runs with ``LC_ALL=C`` and ``LANGUAGE=`` (empty) in its environment. That is
``KanbanService._git_run`` and every direct ``subprocess.run(["git", ...])`` in src:
``git_toplevel`` (rev-parse), ``inputs._git_user_name`` (config), the ``git cat-file``
readers in ``_reader_at`` and ``_parent_link_blob``, ``git ls-tree`` / ``git cat-file --batch`` in ``_blobs_at``,
and ``git hook run`` (through ``_git_run``).

1. Structural: ``subprocess.run`` is spied on (the real git still runs) while the
   caller's environment says ``LC_ALL=en_US.UTF-8`` / ``LANGUAGE=en``; every git call
   must carry ``LC_ALL == "C"`` and ``LANGUAGE == ""``. Flows: ``_git_run("status")``,
   ``sync_and_push`` with no remote, ``sync_and_push`` against a remote, and
   ``create --push``; plus each direct git call site on its own.
2. Behavioural: under ``LANG``/``LC_ALL=de_DE.UTF-8``, ``LANGUAGE=de``, a push that
   loses the race to a rival must still retry and win. Skipped when this git has no
   German catalog (it would print English anyway, and the test would prove nothing).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_sync_and_push import (
    ITEM,
    ITEM_TEXT,
    LINE,
    Appender,
    Recorder,
    _no_remote,
    rival,
    run,
    service,
)
from tests.issues.test_574_sync_and_push import world as world  # noqa: F401 (fixture)
from tests.issues.test_585_create_push_loop import (
    _ORIG_RUN,
    World,
    git,
    output_of,
    run_create,
)
from yurtle_kanban import sync as sync_mod
from yurtle_kanban.inputs import _git_user_name
from yurtle_kanban.service import git_toplevel

# --- harness ---------------------------------------------------------------------


class GitSpy:
    """Wrap subprocess.run: record (argv, effective env) of every git call; run it."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, (list, tuple)) and cmd and Path(str(cmd[0])).name == "git":
            env = kwargs.get("env")
            self.calls.append(([str(c) for c in cmd], dict(os.environ if env is None else env)))
        return _ORIG_RUN(*args, **kwargs)

    def with_sub(self, *words: str) -> list[tuple[list[str], dict[str, str]]]:
        """The calls whose argv (after 'git') contains every word in `words`."""
        return [(c, e) for c, e in self.calls if all(w in c[1:] for w in words)]

    def not_c(self, *words: str) -> list[str]:
        """Each git call (containing `words`) whose env is not LC_ALL=C with an empty
        LANGUAGE."""
        return [
            f"{' '.join(c)}  (LC_ALL={e.get('LC_ALL')!r}, LANGUAGE={e.get('LANGUAGE')!r})"
            for c, e in self.with_sub(*words)
            if e.get("LC_ALL") != "C" or e.get("LANGUAGE") != ""
        ]


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> GitSpy:
    """A caller whose locale is NOT C: git must still be told LC_ALL=C."""
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LANGUAGE", "en")
    s = GitSpy()
    monkeypatch.setattr(subprocess, "run", s)
    return s


def assert_all_c(spy: GitSpy, *must_see: tuple[str, ...], only: bool = False) -> None:
    """Every git call is C (or, `only`: every call matching `must_see`, so one call
    site's test goes green on its own fix)."""
    assert spy.calls, "no git subprocess was recorded"
    for words in must_see:
        assert spy.with_sub(*words), (
            f"expected a `git {' '.join(words)}` call; saw {[c for c, _ in spy.calls]}"
        )
    bad = spy.not_c(*must_see[0]) if only else spy.not_c()
    assert not bad, "git calls not under LC_ALL=C / LANGUAGE=:\n" + "\n".join(bad)


# --- 1. structural: flows ----------------------------------------------------------------


def test_git_run_status_is_c_locale(world, spy) -> None:
    done = service(world)._git_run("status")
    assert done.returncode == 0, done.stderr
    assert_all_c(spy, ("status",))


def test_git_run_keeps_c_locale_with_explicit_env(world, spy) -> None:
    """A caller-supplied env (the index-file env `_commit_on` passes) is still C."""
    env = {**os.environ, "LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de"}
    done = service(world)._git_run("status", env=env)
    assert done.returncode == 0, done.stderr
    assert_all_c(spy, ("status",))


def test_sync_and_push_no_remote_is_c_locale(world, spy) -> None:
    _no_remote(world)
    out = run(world, Appender(sync_mod), Recorder())
    assert out.kind == "local", out.message
    # every call under C but the commit, whose pre-commit hook runs in the user's
    # locale and whose output is only shown (#848)
    assert spy.with_sub("commit", "--only"), [c for c, _ in spy.calls]
    bad = [line for line in spy.not_c() if not line.startswith("git commit")]
    assert not bad, "git calls not under LC_ALL=C / LANGUAGE=:\n" + "\n".join(bad)


def test_sync_and_push_with_remote_is_c_locale(world, spy) -> None:
    out = run(world, Appender(sync_mod), Recorder())
    assert out.kind == "won", out.message
    # every call under C but the pre-commit hook's, whose output is only shown
    # and runs in the user's locale (#826)
    assert spy.with_sub("hook", "run"), [c for c, _ in spy.calls]
    bad = [line for line in spy.not_c() if not line.startswith("git hook run")]
    assert not bad, "git calls not under LC_ALL=C / LANGUAGE=:\n" + "\n".join(bad)
    for words in (("fetch",), ("push",), ("cat-file",)):
        assert spy.with_sub(*words), [c for c, _ in spy.calls]


def test_create_push_is_c_locale(world, spy, monkeypatch) -> None:
    result = run_create(world, monkeypatch)
    assert result.exit_code == 0, output_of(result)
    assert spy.with_sub("push"), [c for c, _ in spy.calls]
    # all under C but the pre-commit hook's, which runs in the user's locale (#826)
    bad = [line for line in spy.not_c() if not line.startswith("git hook run")]
    assert not bad, "git calls not under LC_ALL=C / LANGUAGE=:\n" + "\n".join(bad)


# --- 1. structural: each direct subprocess.run(["git", ...]) site --------------------------


def test_git_toplevel_is_c_locale(world, spy) -> None:
    assert git_toplevel(world.a) is not None
    assert_all_c(spy, ("rev-parse", "--show-toplevel"), only=True)


def test_git_user_name_is_c_locale(world, spy) -> None:
    _git_user_name(world.a)
    assert_all_c(spy, ("config", "user.name"), only=True)


def test_reader_at_cat_file_is_c_locale(world, spy) -> None:
    base = git(world.a, "rev-parse", "HEAD").strip()
    spy.calls.clear()
    reader = service(world)._reader_at(base, {})
    assert reader(ITEM) == ITEM_TEXT
    assert_all_c(spy, ("cat-file", "blob"), only=True)


def test_parent_link_blob_cat_file_is_c_locale(world, spy) -> None:
    git(world.a, "fetch", "origin")
    base = git(world.a, "rev-parse", f"origin/{world.default}").strip()
    svc = service(world)
    # the relation is checked before origin is read (#777): give `expedition` one,
    # so the parent's blob is read and its cat-file is what this test sees
    relation = next(iter(type(svc)._INVERSE_RELATIONS.values()))
    svc._INVERSE_RELATIONS = {**type(svc)._INVERSE_RELATIONS, "expedition": relation}
    spy.calls.clear()
    svc._parent_link_blob(base, "EXP-001", "expedition", "EXP-002")
    assert_all_c(spy, ("cat-file", "blob"), only=True)


def test_blobs_at_reads_are_c_locale(world, spy) -> None:
    """`_blobs_at` reads blobs with `ls-tree` + `cat-file --batch` (#832): both C."""
    svc = service(world)
    spy.calls.clear()
    blobs = svc._blobs_at("HEAD", [ITEM])
    assert blobs.get(ITEM) == ITEM_TEXT, blobs
    assert_all_c(spy, ("cat-file", "--batch"), ("ls-tree",), only=True)
    assert not spy.not_c("ls-tree"), spy.not_c("ls-tree")


# --- 2. behavioural: a German locale still retries a lost race -----------------------------


def _german_env() -> dict[str, str]:
    return {**os.environ, "LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de"}


def _git_speaks_german(tmp_path: Path) -> bool:
    """Does this git translate its messages? Ask it for one outside any repository."""
    probe = tmp_path / "not-a-repo"
    probe.mkdir()
    done = _ORIG_RUN(
        ["git", "status"], cwd=probe, capture_output=True, text=True,
        env={**_german_env(), "GIT_CEILING_DIRECTORIES": str(tmp_path)},
    )
    return bool(done.stderr.strip()) and "not a git repository" not in done.stderr


def test_german_locale_lost_race_still_retries_and_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not _git_speaks_german(tmp_path):
        pytest.skip("this git has no German catalog: it prints English under LANGUAGE=de, "
                    "so a German-locale run would prove nothing")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    w = World(tmp_path / "w")
    (w.a / ITEM).write_text(ITEM_TEXT)
    git(w.a, "add", ITEM)
    git(w.a, "commit", "-m", "seed EXP-001")
    git(w.a, "push", "origin", f"HEAD:refs/heads/{w.default}")
    git(w.b, "fetch", "origin")
    for key, value in _german_env().items():
        if key in ("LANG", "LC_ALL", "LANGUAGE"):
            monkeypatch.setenv(key, value)

    rec = Recorder(lambda attempt: rival(w) if attempt == 0 else None)
    out = run(w, Appender(sync_mod), rec)

    assert out.kind == "won", f"a German-locale lost race was not retried: {out.message}"
    assert rec.seams == [0, 1]
    assert w.remote_show(ITEM) == ITEM_TEXT + LINE + "\n"
