"""Issue #399 — a PathLike root whose `__fspath__` raises escapes `trigger()`.

Since #388 (PR #396), `HookEngine.trigger()` catches only `TypeError` from
`Path(root)`. A `HookContext.repo_root` that is an `os.PathLike` whose
`__fspath__` raises something else (e.g. `RuntimeError`, `ValueError`) goes
straight to the caller. A hook must never crash its caller.

Decided behaviour — whatever `Path(root)` raises:
- `trigger(event, ctx)` does not raise.
- It logs ONE warning naming the repo root (as in #388).
- It falls back to the engine's root (or `None` when the engine has none);
  the actions still run and see that fallback root, with the caller's timestamp.
- `_depth` is restored after the call; the caller's ctx is never mutated.

Cases: `__fspath__` raising `RuntimeError`, raising `ValueError`, and
returning an int (which makes `Path()` raise `TypeError` — already caught
since #388, so these stay green).

Controls: the #388 junk roots (int, bytes, object) still warn and fall back;
str and `Path` roots behave as in #375 (no warning).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.issues.test_388_junk_root_no_crash import (  # noqa: F401 (fixtures)
    ENGINE_ROOT,
    JUNK_ROOTS,
    ROOT_WARNING,
    SENTINEL_TS,
    _ctx,
    _engine,
    _non_root_fields,
    _record_action_contexts,
    _setup,
    _warnings,
    warnings_log,
)
from yurtle_kanban.hooks import HookEvent


class _RaisingPathLike(os.PathLike):
    """A PathLike whose `__fspath__` raises the given exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __fspath__(self) -> str:
        raise self._exc

    def __repr__(self) -> str:
        return f"<raising-pathlike {type(self._exc).__name__}>"


class _IntPathLike(os.PathLike):
    """A PathLike whose `__fspath__` returns an int (Path() raises TypeError)."""

    def __fspath__(self) -> str:
        return 7  # type: ignore[return-value]

    def __repr__(self) -> str:
        return "<int-pathlike>"


def _make_bad(kind: str) -> os.PathLike:
    if kind == "runtimeerror":
        return _RaisingPathLike(RuntimeError("boom"))
    if kind == "valueerror":
        return _RaisingPathLike(ValueError("bad path"))
    return _IntPathLike()


BAD_PATHLIKES = [
    pytest.param("runtimeerror", id="fspath-raises-RuntimeError"),
    pytest.param("valueerror", id="fspath-raises-ValueError"),
    pytest.param("returns-int", id="fspath-returns-int"),
]


def test_sanity_path_raises_as_described() -> None:
    with pytest.raises(RuntimeError):
        Path(_make_bad("runtimeerror"))
    with pytest.raises(ValueError):
        Path(_make_bad("valueerror"))
    with pytest.raises(TypeError):
        Path(_make_bad("returns-int"))


# --- red: a raising PathLike root does not raise; action runs under fallback --------


@pytest.mark.parametrize("kind", BAD_PATHLIKES)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_pathlike_action_runs_under_fallback_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, with_engine_root: bool
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    engine.trigger(HookEvent.ITEM_CREATED, _ctx(_make_bad(kind)))  # must not raise
    assert len(seen) == 1, f"log action ran {len(seen)} times"
    if with_engine_root:
        assert isinstance(seen[0].repo_root, Path)
        assert seen[0].repo_root == engine_repo
        assert (engine_repo / ".kanban" / "hooks.log").is_file()
    else:
        assert seen[0].repo_root is None
        assert (elsewhere / ".kanban" / "hooks.log").is_file()
    assert seen[0].timestamp == SENTINEL_TS


@pytest.mark.parametrize("kind", BAD_PATHLIKES)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_pathlike_logs_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    kind: str,
    with_engine_root: bool,
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    engine.trigger(HookEvent.ITEM_CREATED, _ctx(_make_bad(kind)))
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert ROOT_WARNING.search(warned[0]), f"warning doesn't name the repo root: {warned[0]!r}"


@pytest.mark.parametrize("kind", BAD_PATHLIKES)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_pathlike_depth_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, with_engine_root: bool
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    assert engine._depth == 0
    engine.trigger(HookEvent.ITEM_CREATED, _ctx(_make_bad(kind)))
    assert engine._depth == 0
    # a second trigger still runs (the depth guard isn't stuck)
    engine.trigger(HookEvent.ITEM_CREATED, _ctx(_make_bad(kind)))
    assert engine._depth == 0
    assert len(seen) == 2


@pytest.mark.parametrize("kind", BAD_PATHLIKES)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_pathlike_caller_not_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, with_engine_root: bool
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    bad = _make_bad(kind)
    ctx = _ctx(bad)
    before = _non_root_fields(ctx)
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    assert ctx.repo_root is bad, "caller's context was mutated"
    assert _non_root_fields(ctx) == before


# --- green controls: #388 junk roots, str / Path roots -------------------------------


@pytest.mark.parametrize("junk", JUNK_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_388_junk_root_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    junk: object,
    with_engine_root: bool,
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    ctx = _ctx(junk)
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    assert len(seen) == 1
    assert seen[0].repo_root == (engine_repo if with_engine_root else None)
    assert engine._depth == 0
    assert ctx.repo_root is junk
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert ROOT_WARNING.search(warned[0])


@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_str_root_becomes_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
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
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
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
