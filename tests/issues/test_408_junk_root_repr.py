"""Issue #408 — `repr()` of a junk context root inside the #388/#399 handler can raise.

Since #399 (PR #406), `HookEngine.trigger()` catches whatever `Path(root)` raises
and logs a warning formatted with `{root!r}`. That f-string runs inside the
`except` block, so a root whose `__repr__` raises escapes `trigger()` from the
handler (`_depth` is still restored by the `finally`).

Decided behaviour — a root that can't become a path AND whose `__repr__` (and
`__str__`) raise:
- `trigger(event, ctx)` does not raise.
- It logs ONE repo-root warning that names at least the root's type (its class name).
- It falls back to the engine's root (or `None` when the engine has none);
  the actions still run and see that fallback root, with the caller's timestamp.
- `_depth` is restored after the call; the caller's ctx is never mutated.

Cases: a PathLike whose `__fspath__` raises and whose `__repr__` raises; a
non-PathLike object whose `__repr__` raises.

Controls: the #388/#399 junk roots are unchanged, and a normal junk root's
warning still shows its repr (`5`, `b'x'`, `<opaque>`).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.issues.test_388_junk_root_no_crash import (  # noqa: F401 (fixtures)
    ENGINE_ROOT,
    ROOT_WARNING,
    SENTINEL_TS,
    _ctx,
    _engine,
    _non_root_fields,
    _Opaque,
    _record_action_contexts,
    _setup,
    _warnings,
    warnings_log,
)
from tests.issues.test_399_fspath_raises import _RaisingPathLike
from yurtle_kanban.hooks import HookEvent


class _ReprBoomError(Exception):
    """Raised by the bad roots' `__repr__` / `__str__` — distinct so a leak is obvious."""


class _BadReprFspathRoot(os.PathLike):
    """A PathLike whose `__fspath__`, `__repr__` and `__str__` all raise."""

    def __fspath__(self) -> str:
        raise RuntimeError("fspath boom")

    def __repr__(self) -> str:
        raise _ReprBoomError("repr boom")

    def __str__(self) -> str:
        raise _ReprBoomError("str boom")


class _BadReprOpaqueRoot:
    """Neither str nor PathLike; `__repr__` and `__str__` raise."""

    def __repr__(self) -> str:
        raise _ReprBoomError("repr boom")

    def __str__(self) -> str:
        raise _ReprBoomError("str boom")


_BAD_REPR_CLASSES = {
    "fspath-raises": _BadReprFspathRoot,
    "not-pathlike": _BadReprOpaqueRoot,
}

BAD_REPR_ROOTS = [
    pytest.param("fspath-raises", id="fspath-and-repr-raise"),
    pytest.param("not-pathlike", id="not-pathlike-repr-raises"),
]


def _make_bad(kind: str) -> object:
    return _BAD_REPR_CLASSES[kind]()


def test_sanity_bad_roots_raise_as_described() -> None:
    for kind in _BAD_REPR_CLASSES:
        bad = _make_bad(kind)
        with pytest.raises(Exception):
            Path(bad)  # type: ignore[arg-type]
        with pytest.raises(_ReprBoomError):
            repr(bad)
        with pytest.raises(_ReprBoomError):
            str(bad)


# --- red: a root whose repr raises does not raise; action runs under fallback ------


@pytest.mark.parametrize("kind", BAD_REPR_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_repr_root_action_runs_under_fallback_root(
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


@pytest.mark.parametrize("kind", BAD_REPR_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_repr_root_logs_one_warning_naming_type(
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
    type_name = _BAD_REPR_CLASSES[kind].__name__
    assert type_name in warned[0], f"warning doesn't name the root's type: {warned[0]!r}"


@pytest.mark.parametrize("kind", BAD_REPR_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_repr_root_depth_restored(
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


@pytest.mark.parametrize("kind", BAD_REPR_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_repr_root_caller_not_mutated(
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


@pytest.mark.parametrize("kind", BAD_REPR_ROOTS)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_bad_repr_root_no_matching_hook_warns_no_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    kind: str,
    with_engine_root: bool,
) -> None:
    engine_repo, elsewhere, hooks = _setup(tmp_path)
    seen = _record_action_contexts(monkeypatch)
    monkeypatch.chdir(elsewhere)
    engine = _engine(hooks, engine_repo if with_engine_root else None)
    bad = _make_bad(kind)
    ctx = _ctx(bad, item_type="chore")  # no hook for this type
    engine.trigger(HookEvent.ITEM_CREATED, ctx)  # must not raise
    assert seen == []  # sanity: nothing matched
    assert engine._depth == 0
    assert ctx.repo_root is bad
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert ROOT_WARNING.search(warned[0]), f"warning doesn't name the repo root: {warned[0]!r}"


# --- green controls: #388 / #399 roots unchanged; normal junk warning shows repr ----


JUNK_WITH_REPR = [
    pytest.param(5, "5", id="int"),
    pytest.param(b"x", "b'x'", id="bytes"),
    pytest.param(_Opaque(), "<opaque>", id="object"),
    pytest.param(
        _RaisingPathLike(RuntimeError("boom")),
        "<raising-pathlike RuntimeError>",
        id="fspath-raises-RuntimeError",
    ),
]


@pytest.mark.parametrize(("junk", "shown"), JUNK_WITH_REPR)
@pytest.mark.parametrize("with_engine_root", ENGINE_ROOT)
def test_normal_junk_root_warning_shows_repr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    junk: object,
    shown: str,
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
    assert shown in warned[0], f"warning doesn't show the root's repr {shown!r}: {warned[0]!r}"
