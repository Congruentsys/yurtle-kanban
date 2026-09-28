"""Issue #826 — the pre-commit hook runs in the user's locale, not under ``LC_ALL=C``.

#806 put every git subprocess under ``GIT_ENV`` (``LC_ALL=C``, ``LANGUAGE=``) so the
messages the code matches are English. That swept in ``git hook run pre-commit`` in
``_commit_on``, whose output is only ever displayed, never parsed: the user's hook
should see the user's locale, exactly as under a plain ``git commit``.

Decided spec (the [steer] on #826):
- ``git hook run pre-commit`` gets the caller's ``LC_ALL`` / ``LANG`` / ``LANGUAGE``
  unchanged, and still ``GIT_TERMINAL_PROMPT=0``;
- every other git call stays under ``LC_ALL=C`` / ``LANGUAGE=``.

1. Structural: the #806 spy (caller env ``LC_ALL=LANG=en_US.UTF-8``, ``LANGUAGE=en``)
   records every git call of ``sync_and_push`` against a remote and
   ``create --push`` (the no-remote path commits with a plain ``git commit``, no
   ``hook run``); the ``hook run`` call carries the caller's values, every other
   call is C.
2. Behavioural: a real pre-commit hook writes the ``LC_ALL`` / ``LANGUAGE`` / ``LANG``
   it sees to a file; it sees the caller's values (and, for a caller with no
   ``LC_ALL``, no ``LC_ALL`` — never ``C``).

``test_806_git_lc_all.py::test_sync_and_push_with_remote_is_c_locale`` pins the old
rule (``hook run`` under C); the driver updates it, not this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_574_sync_and_push import (
    ITEM,
    ITEM_TEXT,
    LINE,
    Appender,
    Recorder,
    run,
)
from tests.issues.test_574_sync_and_push import world as world  # noqa: F401 (fixture)
from tests.issues.test_585_create_push_loop import git, output_of, run_create
from tests.issues.test_806_git_lc_all import GitSpy
from tests.issues.test_806_git_lc_all import spy as spy  # noqa: F401 (fixture)
from yurtle_kanban import sync as sync_mod

HOOK = ("hook", "run")


# --- harness ---------------------------------------------------------------------


def assert_hook_user_locale_rest_c(spy: GitSpy) -> None:
    """The `git hook run` call(s) carry the caller's locale and GIT_TERMINAL_PROMPT=0;
    every other git call is LC_ALL=C / LANGUAGE=."""
    hooks = spy.with_sub(*HOOK)
    assert hooks, f"expected a `git hook run` call; saw {[c for c, _ in spy.calls]}"
    for cmd, env in hooks:
        seen = {k: env.get(k) for k in ("LC_ALL", "LANG", "LANGUAGE", "GIT_TERMINAL_PROMPT")}
        assert seen == {
            "LC_ALL": "en_US.UTF-8",
            "LANG": "en_US.UTF-8",
            "LANGUAGE": "en",
            "GIT_TERMINAL_PROMPT": "0",
        }, f"`{' '.join(cmd)}` did not run in the caller's locale: {seen}"
    bad = [
        f"{' '.join(c)}  (LC_ALL={e.get('LC_ALL')!r}, LANGUAGE={e.get('LANGUAGE')!r})"
        for c, e in spy.calls
        if not all(w in c[1:] for w in HOOK)
        and (e.get("LC_ALL") != "C" or e.get("LANGUAGE") != "")
    ]
    assert not bad, "non-hook git calls not under LC_ALL=C / LANGUAGE=:\n" + "\n".join(bad)


def _locale_hook(clone: Path) -> Path:
    """A pre-commit hook that records the locale it sees and passes; returns the file
    it writes (one line per run: LC_ALL|LANGUAGE|LANG, 'unset' when absent)."""
    hooks = clone.parent / f"{clone.name}-hooks"
    hooks.mkdir(exist_ok=True)
    out = clone.parent / f"{clone.name}-hook-locale.txt"
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        'printf "%s|%s|%s\\n" "${LC_ALL-unset}" "${LANGUAGE-unset}" "${LANG-unset}"'
        f' >> "{out}"\n'
        "exit 0\n"
    )
    hook.chmod(0o755)
    git(clone, "config", "core.hooksPath", str(hooks))
    return out


def _caller_locale(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> None:
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


# --- 1. structural ---------------------------------------------------------------


def test_sync_and_push_with_remote_hook_runs_in_user_locale(world, spy, monkeypatch) -> None:
    # the world fixture exports GIT_TERMINAL_PROMPT=0; drop it, so a 0 on the hook
    # call is the code's doing, not inherited
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    out = run(world, Appender(sync_mod), Recorder())
    assert out.kind == "won", out.message
    assert spy.with_sub("push"), "expected a push"
    assert_hook_user_locale_rest_c(spy)


def test_create_push_hook_runs_in_user_locale(world, spy, monkeypatch) -> None:
    result = run_create(world, monkeypatch)
    assert result.exit_code == 0, output_of(result)
    assert_hook_user_locale_rest_c(spy)


# --- 2. behavioural: a real hook sees the caller's locale --------------------------


def test_pre_commit_hook_sees_callers_lc_all(world, monkeypatch) -> None:
    seen = _locale_hook(world.a)
    _caller_locale(monkeypatch, LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8", LANGUAGE="en")
    out = run(world, Appender(sync_mod), Recorder())
    assert out.kind == "won", out.message
    lines = seen.read_text().splitlines()
    assert lines, "the pre-commit hook never ran"
    assert set(lines) == {"en_US.UTF-8|en|en_US.UTF-8"}, (
        f"the hook did not see the caller's locale: {lines}"
    )
    assert world.remote_show(ITEM) == ITEM_TEXT + LINE + "\n"


def test_pre_commit_hook_sees_no_lc_all_when_caller_has_none(world, monkeypatch) -> None:
    """A caller with no LC_ALL / LANGUAGE: the hook must not be handed C."""
    seen = _locale_hook(world.a)
    _caller_locale(monkeypatch, LC_ALL=None, LANGUAGE=None, LANG="en_US.UTF-8")
    out = run(world, Appender(sync_mod), Recorder())
    assert out.kind == "won", out.message
    lines = seen.read_text().splitlines()
    assert lines, "the pre-commit hook never ran"
    assert set(lines) == {"unset|unset|en_US.UTF-8"}, (
        f"the hook was not left in the caller's (unset) locale: {lines}"
    )
