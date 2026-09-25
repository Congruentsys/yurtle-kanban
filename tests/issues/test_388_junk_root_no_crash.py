"""Issue #388 — a junk `HookContext.repo_root` (int/bytes/object) raises out of `trigger()`.

Since #384 (#375), `HookEngine.trigger()` coerces a non-`Path` context root with
`Path(root)` inside its try/finally (no except), so an int, bytes or arbitrary
object root raises `TypeError` straight to the caller. A hook must never crash
the caller.

Decided behaviour:
- `trigger(event, ctx)` with a root that can't become a path does not raise.
- It logs ONE warning that the context's repo root isn't a path.
- It falls back to the engine's root (or `None` when the engine has none); the
  actions still run and see that fallback root, with the caller's timestamp.
- `_depth` is restored after the call; the caller's ctx is never mutated.
- The same fallback applies when no hook matches (warning, no raise).

Controls (as in #375): a str root reaches the actions as `Path(str)`, a `Path`
root passes through unchanged, a `None` root gets the engine root — none of them warn.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from yurtle_kanban import HookEngine
from yurtle_kanban import hooks as hooks_mod
from yurtle_kanban.hooks import HookContext, HookEvent

LOGGER = "yurtle-kanban"
HOOKS_LOGGER = "yurtle-kanban.hooks"

HOOKS_DOC = (
    "---\n"
    "type: kanban-hooks\n"
    "id: hooks-388\n"
    "version: 1\n"
    "hooks:\n"
    "  on_create:\n"
    "    - item_types: [expedition]\n"
    "      actions:\n"
    "        - type: log\n"
    "---\n"
    "# Hooks 388\n"
)

SENTINEL_TS = "1999-01-01T00:00:00+00:00"


class _Opaque:
    """An object that is neither str nor os.PathLike."""

    def __repr__(self) -> str:
        return "<opaque>"


JUNK_ROOTS = [
    pytest.param(5, id="int"),
    pytest.param(b"x", id="bytes"),
    pytest.param(_Opaque(), id="object"),
]

ENGINE_ROOT = [
    pytest.param(False, id="no-engine-root"),
    pytest.param(True, id="engine-root"),
]

ROOT_WARNING = re.compile(r"repo[ _-]?root", re.IGNORECASE)


# --- fixtures / helpers --------------------------------------------------------------


@pytest.fixture
def warnings_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, attached straight to the yurtle-kanban loggers (whatever their propagation)."""
    logs = [logging.getLogger(LOGGER), logging.getLogger(HOOKS_LOGGER)]
    old_levels = [log.level for log in logs]
    for log in logs:
        log.addHandler(caplog.handler)
        log.setLevel(logging.WARNING)
    caplog.handler.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        for log, level in zip(logs, old_levels):
            log.removeHandler(caplog.handler)
            log.setLevel(level)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    records = {id(r): r for r in caplog.records}.values()
    return [
        r.getMessage()
        for r in records
        if r.name.startswith(LOGGER) and r.levelno >= logging.WARNING
    ]


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Make `engine-repo/`, `elsewhere/` and a hooks file outside them."""
    engine_repo = tmp_path / "engine-repo"
    engine_repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    hooks = tmp_path / "config" / "hooks.yurtle.md"
    hooks.parent.mkdir()
    hooks.write_text(HOOKS_DOC, encoding="utf-8")
    return engine_repo, elsewhere, hooks


def _record_action_contexts(monkeypatch: pytest.MonkeyPatch) -> list[HookContext]:
    """Wrap the log action so each call records the context the action sees."""
    seen: list[HookContext] = []
    original = hooks_mod._action_log

    def recording(action: dict, context: HookContext) -> None:
        seen.append(context)
        original(action, context)

    monkeypatch.setattr(hooks_mod, "_action_log", recording)
    return seen


def _ctx(repo_root: object, item_type: str = "expedition") -> HookContext:
    ctx = HookContext(
        event=HookEvent.ITEM_CREATED,
        item_id="E-1",
        item_type=item_type,
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


def _engine(hooks: Path, engine_root: Path | None) -> HookEngine:
    engine = HookEngine(hooks, repo_root=engine_root)
    assert engine.is_configured  # sanity: the hooks file loaded
    return engine


def _non_root_fields(ctx: HookContext) -> dict[str, object]:
    return {
        f.name: getattr(ctx, f.name)
        for f in dataclasses.fields(ctx)
        if f.name != "repo_root"
    }


# --- red: a junk root does not raise; the action runs under the fallback root ---------


@pytest.mark.parametrize("junk", JUNK_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_junk_root_action_runs_under_fallback_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, junk: object, with_engine_root: bool
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    engine.trigger(HookEvent.ITEM_CREATED, _ctx(junk))  # must not raise
    assert len(seen) == 1, f"log action ran {len(seen)} times"
    if with_engine_root:
        assert isinstance(seen[0].repo_root, Path)
        assert seen[0].repo_root == engine_repo
        assert (engine_repo / ".kanban" / "hooks.log").is_file()
    else:
        assert seen[0].repo_root is None
        assert (elsewhere / ".kanban" / "hooks.log").is_file()
    assert seen[0].timestamp == SENTINEL_TS


@pytest.mark.parametrize("junk", JUNK_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_junk_root_logs_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    junk: object,
    with_engine_root: bool,
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    engine.trigger(HookEvent.ITEM_CREATED, _ctx(junk))
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert ROOT_WARNING.search(warned[0]), f"warning doesn't name the repo root: {warned[0]!r}"


@pytest.mark.parametrize("junk", JUNK_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_junk_root_depth_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, junk: object, with_engine_root: bool
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    assert engine._depth == 0
    engine.trigger(HookEvent.ITEM_CREATED, _ctx(junk))
    assert engine._depth == 0
    # a second trigger still runs (the depth guard isn't stuck)
    engine.trigger(HookEvent.ITEM_CREATED, _ctx(junk))
    assert engine._depth == 0


@pytest.mark.parametrize("junk", JUNK_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_junk_root_caller_not_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, junk: object, with_engine_root: bool
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    ctx = _ctx(junk)
    before = _non_root_fields(ctx)
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    assert ctx.repo_root is junk, "caller's context was mutated"
    assert _non_root_fields(ctx) == before


@pytest.mark.parametrize("junk", JUNK_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_junk_root_no_matching_hook_warns_no_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    junk: object,
    with_engine_root: bool,
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    ctx = _ctx(junk, item_type="chore")  # no hook for this type
    engine.trigger(HookEvent.ITEM_CREATED, ctx)  # must not raise
    assert seen == []  # sanity: nothing matched
    assert engine._depth == 0
    assert ctx.repo_root is junk
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert ROOT_WARNING.search(warned[0]), f"warning doesn't name the repo root: {warned[0]!r}"


# --- green controls: str / Path / None roots, no warning ------------------------------


@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_str_root_becomes_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    with_engine_root: bool,
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(str(repo))
    _engine(hooks, engine_repo if with_engine_root else None).trigger(HookEvent.ITEM_CREATED, ctx)
    assert len(seen) == 1
    assert isinstance(seen[0].repo_root, Path)
    assert seen[0].repo_root == repo
    assert ctx.repo_root == str(repo)
    assert _warnings(warnings_log) == []


@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_path_root_passes_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    with_engine_root: bool,
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(repo)
    _engine(hooks, engine_repo if with_engine_root else None).trigger(HookEvent.ITEM_CREATED, ctx)
    assert len(seen) == 1
    assert seen[0].repo_root is repo
    assert ctx.repo_root is repo
    assert _warnings(warnings_log) == []


def test_none_root_gets_engine_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    ctx = _ctx(None)
    engine = _engine(hooks, engine_repo)
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    assert len(seen) == 1
    assert seen[0].repo_root == engine_repo
    assert seen[0].timestamp == SENTINEL_TS
    assert ctx.repo_root is None
    assert engine._depth == 0
    assert _warnings(warnings_log) == []
