"""Issue #375 — `HookContext(repo_root=<str>)` built directly reaches the actions as a str.

`HookContext.repo_root` is annotated `Path | None`, but a caller that builds the
context itself with a str root passes that str straight through: `trigger()` only
fills a `None` root from the engine (#347/#357).

Decided behaviour ([steer] on #375): coerce in `HookEngine.trigger()`, not in
`HookContext.__post_init__`.
- A str context root reaches the actions as `Path(the str)`, whether or not the
  engine has its own root (a context root wins over the engine root, as today).
- The caller's context is never mutated: its `repo_root` stays the original str.
- The action sees the caller's `timestamp` and every other field unchanged.
- A `Path` context root passes through unchanged; a `None` context root still gets
  the engine root (#347/#357).
- `HookContext.__post_init__` is unchanged: a str `repo_root` stays a str on the
  constructed object (the #373 / PR #383 tripwire pins `__post_init__` to deriving
  only `timestamp`).

The reds observe the action via a wrapper around the log action (the #359 style),
so they hold whether or not `trigger()` works on a copy.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from yurtle_kanban import HookEngine
from yurtle_kanban import hooks as hooks_mod
from yurtle_kanban.hooks import HookContext, HookEvent

HOOKS_DOC = (
    "---\n"
    "type: kanban-hooks\n"
    "id: hooks-375\n"
    "version: 1\n"
    "hooks:\n"
    "  on_create:\n"
    "    - item_types: [expedition]\n"
    "      actions:\n"
    "        - type: log\n"
    "---\n"
    "# Hooks 375\n"
)

SENTINEL_TS = "1999-01-01T00:00:00+00:00"


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Make `repo/`, `engine-repo/`, `elsewhere/` and a hooks file outside them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    engine_repo = tmp_path / "engine-repo"
    engine_repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    hooks = tmp_path / "config" / "hooks.yurtle.md"
    hooks.parent.mkdir()
    hooks.write_text(HOOKS_DOC, encoding="utf-8")
    return repo, engine_repo, elsewhere, hooks


def _record_action_contexts(monkeypatch: pytest.MonkeyPatch) -> list[HookContext]:
    """Wrap the log action so each call records the context the action sees."""
    seen: list[HookContext] = []
    original = hooks_mod._action_log

    def recording(action: dict, context: HookContext) -> None:
        seen.append(context)
        original(action, context)

    monkeypatch.setattr(hooks_mod, "_action_log", recording)
    return seen


def _ctx(repo_root: str | Path | None) -> HookContext:
    ctx = HookContext(
        event=HookEvent.ITEM_CREATED,
        item_id="E-1",
        item_type="expedition",
        title="A title",
        old_status="backlog",
        new_status="ready",
        assignee="Mini",
        forced=True,
        metadata={"k": "v"},
        repo_root=repo_root,  # type: ignore[arg-type]
    )
    ctx.timestamp = SENTINEL_TS
    return ctx


def _fire(
    hooks: Path, engine_root: str | Path | None, ctx: HookContext
) -> None:
    HookEngine(hooks, repo_root=engine_root).trigger(HookEvent.ITEM_CREATED, ctx)


def _non_root_fields(ctx: HookContext) -> dict[str, object]:
    return {
        f.name: getattr(ctx, f.name)
        for f in dataclasses.fields(ctx)
        if f.name != "repo_root"
    }


# --- red: a str context root reaches the action as a Path ---------------------------


@pytest.mark.parametrize("with_engine_root", [False, True], ids=["no-engine-root", "engine-root"])
def test_str_context_root_action_sees_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_engine_root: bool
) -> None:
    repo, engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(str(repo))
    _fire(hooks, engine_repo if with_engine_root else None, ctx)
    assert len(seen) == 1, f"log action ran {len(seen)} times"  # sanity: it fired
    assert (repo / ".kanban" / "hooks.log").is_file()  # sanity: under the context root
    root = seen[0].repo_root
    assert isinstance(root, Path), f"action saw repo_root as {type(root).__name__}"
    assert root == Path(str(repo))


def test_relative_str_context_root_action_sees_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine_repo, _, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _fire(hooks, engine_repo, _ctx("repo"))
    assert len(seen) == 1
    assert (tmp_path / "repo" / ".kanban" / "hooks.log").is_file()
    root = seen[0].repo_root
    assert isinstance(root, Path), f"action saw repo_root as {type(root).__name__}"
    assert root == Path("repo")


# --- guards: no mutation, every other field (and the timestamp) carried --------------


@pytest.mark.parametrize("with_engine_root", [False, True], ids=["no-engine-root", "engine-root"])
def test_str_context_root_caller_not_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_engine_root: bool
) -> None:
    repo, engine_repo, elsewhere, hooks = _setup(tmp_path)
    _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(str(repo))
    before = _non_root_fields(ctx)
    _fire(hooks, engine_repo if with_engine_root else None, ctx)
    assert ctx.repo_root == str(repo)
    assert isinstance(ctx.repo_root, str), "caller's context was mutated"
    assert _non_root_fields(ctx) == before


@pytest.mark.parametrize("with_engine_root", [False, True], ids=["no-engine-root", "engine-root"])
def test_str_context_root_action_sees_caller_fields_and_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_engine_root: bool
) -> None:
    repo, engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(str(repo))
    _fire(hooks, engine_repo if with_engine_root else None, ctx)
    assert len(seen) == 1
    assert seen[0].timestamp == SENTINEL_TS
    assert _non_root_fields(seen[0]) == _non_root_fields(ctx)


# --- green controls ---------------------------------------------------------------


@pytest.mark.parametrize("with_engine_root", [False, True], ids=["no-engine-root", "engine-root"])
def test_path_context_root_passes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_engine_root: bool
) -> None:
    repo, engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(repo)
    _fire(hooks, engine_repo if with_engine_root else None, ctx)
    assert len(seen) == 1
    assert isinstance(seen[0].repo_root, Path)
    assert seen[0].repo_root == repo
    assert seen[0].timestamp == SENTINEL_TS
    assert ctx.repo_root is repo
    assert (repo / ".kanban" / "hooks.log").is_file()
    assert not (engine_repo / ".kanban").exists()


@pytest.mark.parametrize("engine_root_as", [Path, str], ids=["path", "str"])
def test_none_context_root_gets_engine_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine_root_as: type
) -> None:
    _, engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(None)
    _fire(hooks, engine_root_as(engine_repo), ctx)
    assert len(seen) == 1
    assert isinstance(seen[0].repo_root, Path)
    assert seen[0].repo_root == engine_repo
    assert seen[0].timestamp == SENTINEL_TS
    assert ctx.repo_root is None, "caller's context was mutated (#357)"
    assert (engine_repo / ".kanban" / "hooks.log").is_file()


def test_none_context_root_no_engine_root_stays_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    _fire(hooks, None, _ctx(None))
    assert len(seen) == 1
    assert seen[0].repo_root is None
    assert (elsewhere / ".kanban" / "hooks.log").is_file()


def test_post_init_leaves_str_root_a_str(tmp_path: Path) -> None:
    """Coercion happens only in trigger(); the constructed object keeps the str."""
    ctx = HookContext(
        event=HookEvent.ITEM_CREATED,
        item_id="E-1",
        item_type="expedition",
        repo_root=str(tmp_path),  # type: ignore[arg-type]
    )
    assert ctx.repo_root == str(tmp_path)
    assert isinstance(ctx.repo_root, str)
