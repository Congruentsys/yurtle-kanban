"""Issue #848 — local commits and the ``hdd`` push run the user's hooks in the user's locale.

Follow-up from #826. #806 put every git subprocess under ``LC_ALL=C`` / ``LANGUAGE=``
so the messages the code matches are English; #826 let ``git hook run pre-commit`` keep
the caller's locale, since its output is only shown. Two more git calls run the
user's hooks and are never parsed, yet still run under C:

- ``KanbanService._commit_paths``' ``git commit --only``: git runs the pre-commit hook
  inside it. It is reached by every local commit: ``sync_and_push`` with no remote
  (``_sync_locally``, e.g. a no-remote ``claim``), ``move_item(commit=True)`` and a
  no-remote ``next-id``, and by ``hdd registry --push`` (``_commit_or_exit``).
- the ``hdd`` push in ``_push_only_head_or_exit`` (``hdd registry --push``,
  ``experiment run --push``): git runs the pre-push hook inside it.

Decided spec (the [steer] on #848): both pass ``user_locale=True``. The pushes in
``sync_and_push`` and ``_cas_create`` stay under C, because their ``[rejected]`` text is
matched — the accepted trade-off, pinned here as a control: a ``claim`` against a
remote hands its pre-push hook ``LC_ALL=C``.

Behavioural: a real pre-commit / pre-push hook (``core.hooksPath``) appends
``<hook>|LC_ALL|LANGUAGE|LANG`` (``unset`` when absent) to a file outside the clone,
while the caller's environment says ``LC_ALL=LANG=de_DE.UTF-8``, ``LANGUAGE=de``.

``test_806_git_lc_all.py::test_sync_and_push_no_remote_is_c_locale`` pins the old rule
(the no-remote commit under C); the driver updates it, not this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    claim,
    invoke,
    item_text,
    output_of,
    push_from_a,
    service,
)
from tests.issues.test_584_scoped_commit import _git, _run
from tests.issues.test_584_scoped_commit import hdd as hdd  # noqa: F401 (fixture)
from tests.issues.test_585_create_push_loop import World, git
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItemStatus

USER = {"LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de", "LANG": "de_DE.UTF-8"}
USER_LINE = "de_DE.UTF-8|de|de_DE.UTF-8"
C_LINE_PREFIX = "C|"  # LC_ALL=C, LANGUAGE= (empty), LANG whatever the caller had


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned (the #574 claim world)."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: item_text("ready")}, "seed EXP-001")
    return w


def locale_hooks(clone: Path) -> Path:
    """pre-commit and pre-push hooks that record the locale they see and pass; returns
    the file they append to (one line per run: hook|LC_ALL|LANGUAGE|LANG)."""
    hooks = clone.parent / f"{clone.name}-hooks-848"
    hooks.mkdir(exist_ok=True)
    out = clone.parent / f"{clone.name}-hook-locale-848.txt"
    for name in ("pre-commit", "pre-push"):
        hook = hooks / name
        hook.write_text(
            "#!/bin/sh\n"
            f'printf "%s|%s|%s|%s\\n" "{name}" "${{LC_ALL-unset}}" "${{LANGUAGE-unset}}"'
            f' "${{LANG-unset}}" >> "{out}"\n'
            "exit 0\n"
        )
        hook.chmod(0o755)
    git(clone, "config", "core.hooksPath", str(hooks))
    return out


def user_locale(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> None:
    for key, value in {**USER, **env}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def seen_by(out: Path, hook: str) -> list[str]:
    """What `hook` saw, one `LC_ALL|LANGUAGE|LANG` per run."""
    lines = out.read_text().splitlines() if out.exists() else []
    return [line.split("|", 1)[1] for line in lines if line.startswith(f"{hook}|")]


def assert_user_locale(out: Path, hook: str, expected: str = USER_LINE) -> None:
    seen = seen_by(out, hook)
    assert seen, f"the {hook} hook never ran"
    assert set(seen) == {expected}, f"the {hook} hook did not see the caller's locale: {seen}"


def no_remote(world: World) -> None:
    git(world.a, "remote", "remove", "origin")


# --- 1. `_commit_paths`: the local commit's pre-commit hook -----------------------


def test_no_remote_claim_pre_commit_hook_sees_user_locale(world, monkeypatch) -> None:
    """`claim` with no remote: sync_and_push -> _sync_locally -> _commit_paths."""
    no_remote(world)
    out = locale_hooks(world.a)
    user_locale(monkeypatch)
    head = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert git(world.a, "rev-parse", "HEAD").strip() != head, "nothing was committed"
    assert_user_locale(out, "pre-commit")


def test_no_remote_sync_and_push_hook_sees_no_lc_all_when_caller_has_none(
    world, monkeypatch
) -> None:
    """A caller with no LC_ALL / LANGUAGE: the local commit's hook must not be handed C."""
    no_remote(world)
    out = locale_hooks(world.a)
    user_locale(monkeypatch, LC_ALL=None, LANGUAGE=None)

    done = claim(world.a, A)

    assert done.kind == "local", done.message
    assert_user_locale(out, "pre-commit", "unset|unset|de_DE.UTF-8")


def test_move_item_commit_pre_commit_hook_sees_user_locale(world, monkeypatch) -> None:
    """`move_item(commit=True)` commits locally through _commit_paths (remote or not)."""
    out = locale_hooks(world.a)
    user_locale(monkeypatch)
    svc = service(world.a)
    svc.scan()
    head = git(world.a, "rev-parse", "HEAD").strip()

    svc.move_item(ITEM_ID, WorkItemStatus.IN_PROGRESS, commit=True, assignee=A)

    assert git(world.a, "rev-parse", "HEAD").strip() != head, "nothing was committed"
    assert_user_locale(out, "pre-commit")


def test_no_remote_next_id_pre_commit_hook_sees_user_locale(world, monkeypatch) -> None:
    """`next-id` with no remote commits the allocation through _commit_paths."""
    no_remote(world)
    out = locale_hooks(world.a)
    user_locale(monkeypatch)
    head = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(world, monkeypatch, ["next-id", "EXP"])

    assert result.exit_code == 0, output_of(result)
    assert git(world.a, "rev-parse", "HEAD").strip() != head, "nothing was committed"
    assert_user_locale(out, "pre-commit")


# --- 2. the `hdd` push: `hdd registry --push` -------------------------------------


def _registry_push(hdd_repo: tuple[Path, Path], monkeypatch) -> Path:
    repo, remote = hdd_repo
    out = locale_hooks(repo)
    user_locale(monkeypatch)
    result = _run(repo, monkeypatch, ["hdd", "registry", "--push"])
    assert result.exit_code == 0, result.output
    assert "Committed and pushed" in result.output, result.output
    assert _git(repo, "rev-parse", "HEAD").strip() == _git(remote, "rev-parse", "main").strip()
    return out


def test_hdd_registry_push_pre_push_hook_sees_user_locale(hdd, monkeypatch) -> None:
    out = _registry_push(hdd, monkeypatch)
    assert_user_locale(out, "pre-push")


def test_hdd_registry_push_pre_commit_hook_sees_user_locale(hdd, monkeypatch) -> None:
    """Its commit goes through _commit_or_exit -> _commit_paths."""
    out = _registry_push(hdd, monkeypatch)
    assert_user_locale(out, "pre-commit")


# --- 3. control: sync_and_push's push stays under C (its text is matched) ---------


def test_control_claim_with_remote_pre_push_hook_sees_c(world, monkeypatch) -> None:
    out = locale_hooks(world.a)
    user_locale(monkeypatch)

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    assert result.exit_code == 0, output_of(result)
    pushed = seen_by(out, "pre-push")
    assert pushed, "the pre-push hook never ran"
    assert all(line.startswith(C_LINE_PREFIX) for line in pushed), (
        f"sync_and_push's push left LC_ALL=C: {pushed}"
    )
    assert all(line.split("|")[1] == "" for line in pushed), (
        f"sync_and_push's push left LANGUAGE= (empty): {pushed}"
    )
    # and its pre-commit (`git hook run`, #826) already sees the caller's locale
    assert_user_locale(out, "pre-commit")
