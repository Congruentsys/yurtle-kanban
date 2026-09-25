"""Issue #359 — `HookEngine(repo_root=<str>)` is stored uncoerced.

`HookEngine.__init__` annotates `repo_root: Path | None` but stores it as given, so a
direct caller passing a `str` puts a `str` into `HookContext.repo_root` (the #336 /
#348 str-tolerance class).

Decided behaviour: `HookEngine(config_path, repo_root="<str>")` works like a `Path`
repo root. When a hook fires:
- a `log` action with the default or a relative path lands under that root;
- a `shell` action runs there;
- `HookContext.repo_root`, as the actions see it, is a `Path`.
A relative str root resolves against the cwd exactly as a relative `Path` root does
(this pins the equivalence, not a particular base).

Note: `str / Path` does NOT raise — `Path.__rtruediv__` handles it — so the log and
shell behaviours already hold today and are pinned here as guards. The red is the
type the actions see: a wrapper around the log action records `context.repo_root`
as the action receives it, and today that is a `str`. (The reds observe the action,
not the caller's context, which `trigger()` may copy — #357.)

Controls: a `Path` root, a `None` root (cwd behaviour), and `config_path` given as a
str, a `Path`, `None` or `""` are unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban import HookEngine
from yurtle_kanban import hooks as hooks_mod
from yurtle_kanban.hooks import HookContext, HookEvent


def _hooks_doc(actions_yaml: str) -> str:
    return (
        "---\n"
        "type: kanban-hooks\n"
        "id: hooks-359\n"
        "version: 1\n"
        "hooks:\n"
        "  on_create:\n"
        "    - item_types: [expedition]\n"
        "      actions:\n"
        f"{actions_yaml}"
        "---\n"
        "# Hooks 359\n"
    )


LOG_DEFAULT = "        - type: log\n"
LOG_RELATIVE = "        - type: log\n          path: logs/{item_id}.jsonl\n"
SHELL_PWD = "        - type: shell\n          command: pwd > out.txt\n"


def _setup(tmp_path: Path, actions_yaml: str) -> tuple[Path, Path, Path]:
    """Make `repo/`, `elsewhere/` and a hooks file outside both; return them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    hooks = tmp_path / "config" / "hooks.yurtle.md"
    hooks.parent.mkdir()
    hooks.write_text(_hooks_doc(actions_yaml), encoding="utf-8")
    return repo, elsewhere, hooks


def _ctx(item_id: str = "E-1") -> HookContext:
    return HookContext(event=HookEvent.ITEM_CREATED, item_id=item_id, item_type="expedition")


def _fire(engine: HookEngine, item_id: str = "E-1") -> HookContext:
    ctx = _ctx(item_id)
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    return ctx


def _assert_cwd_untouched(elsewhere: Path) -> None:
    leftovers = sorted(p.relative_to(elsewhere).as_posix() for p in elsewhere.rglob("*"))
    assert leftovers == [], f"hook wrote into the cwd: {leftovers}"


# --- red: the actions see a Path ---------------------------------------------------


def _record_action_roots(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Wrap the log action so each call records the `repo_root` the action sees.

    Observes the action, not the caller's context, so it holds whether or not
    `trigger()` works on a copy of the context (#357).
    """
    seen: list[object] = []
    original = hooks_mod._action_log

    def recording(action: dict, context: HookContext) -> None:
        seen.append(context.repo_root)
        original(action, context)

    monkeypatch.setattr(hooks_mod, "_action_log", recording)
    return seen


def test_str_root_action_sees_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT)
    engine = HookEngine(hooks, repo_root=str(repo))
    seen = _record_action_roots(monkeypatch)
    monkeypatch.chdir(elsewhere)
    _fire(engine)
    assert len(seen) == 1, f"log action ran {len(seen)} times"  # sanity: it fired
    assert (repo / ".kanban" / "hooks.log").is_file()  # sanity: and wrote
    assert isinstance(seen[0], Path), f"action saw repo_root as {type(seen[0]).__name__}"
    assert seen[0] == Path(str(repo))


def test_relative_str_root_action_sees_same_path_as_path_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, hooks = _setup(tmp_path, LOG_DEFAULT)
    seen = _record_action_roots(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _fire(HookEngine(hooks, repo_root=Path("repo")))
    _fire(HookEngine(hooks, repo_root="repo"))
    assert len(seen) == 2, f"log action ran {len(seen)} times"  # sanity: both fired
    via_path, via_str = seen
    assert isinstance(via_path, Path)  # sanity: the Path baseline
    assert isinstance(via_str, Path), f"action saw repo_root as {type(via_str).__name__}"
    assert via_str == via_path


# --- guards: str root behaves like a Path root (green today via __rtruediv__) -------


def test_str_root_default_log_lands_under_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT)
    engine = HookEngine(hooks, repo_root=str(repo))
    monkeypatch.chdir(elsewhere)
    _fire(engine)
    log = Path(str(repo)) / ".kanban" / "hooks.log"
    assert log.is_file(), "no <root>/.kanban/hooks.log"
    assert '"item_id": "E-1"' in log.read_text(encoding="utf-8")
    _assert_cwd_untouched(elsewhere)


def test_str_root_relative_log_path_lands_under_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, LOG_RELATIVE)
    engine = HookEngine(hooks, repo_root=str(repo))
    monkeypatch.chdir(elsewhere)
    _fire(engine)
    assert (repo / "logs" / "E-1.jsonl").is_file()
    _assert_cwd_untouched(elsewhere)


def test_str_root_shell_runs_under_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, SHELL_PWD)
    engine = HookEngine(hooks, repo_root=str(repo))
    monkeypatch.chdir(elsewhere)
    _fire(engine)
    out = repo / "out.txt"
    assert out.is_file(), "shell did not run under the str root"
    assert Path(out.read_text(encoding="utf-8").strip()).resolve() == repo.resolve()
    _assert_cwd_untouched(elsewhere)


@pytest.mark.parametrize("actions_yaml", [LOG_DEFAULT, LOG_RELATIVE], ids=["default", "relative"])
def test_relative_str_root_matches_relative_path_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actions_yaml: str
) -> None:
    """A relative str root lands the log where a relative Path root does."""
    _, _, hooks = _setup(tmp_path, actions_yaml)
    monkeypatch.chdir(tmp_path)

    def written() -> list[str]:
        return sorted(
            p.relative_to(tmp_path).as_posix()
            for p in tmp_path.rglob("*")
            if p.is_file() and p != hooks
        )

    _fire(HookEngine(hooks, repo_root=Path("repo")), item_id="E-1")
    via_path = written()
    assert via_path, "sanity: the Path baseline wrote a log"
    for p in via_path:
        (tmp_path / p).unlink()
    _fire(HookEngine(hooks, repo_root="repo"), item_id="E-1")
    assert written() == via_path


# --- green controls -------------------------------------------------------------------


def test_path_root_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT)
    engine = HookEngine(hooks, repo_root=repo)
    seen = _record_action_roots(monkeypatch)
    monkeypatch.chdir(elsewhere)
    _fire(engine)
    assert (repo / ".kanban" / "hooks.log").is_file()
    assert seen == [repo]
    assert isinstance(seen[0], Path)
    _assert_cwd_untouched(elsewhere)


def test_none_root_stays_cwd_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT)
    engine = HookEngine(hooks, repo_root=None)
    seen = _record_action_roots(monkeypatch)
    monkeypatch.chdir(elsewhere)
    _fire(engine)
    assert seen == [None]
    assert (elsewhere / ".kanban" / "hooks.log").is_file()
    assert not (repo / ".kanban").exists()


def test_none_root_shell_stays_cwd_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, elsewhere, hooks = _setup(tmp_path, SHELL_PWD)
    engine = HookEngine(hooks)
    monkeypatch.chdir(elsewhere)
    _fire(engine)
    out = elsewhere / "out.txt"
    assert out.is_file()
    assert Path(out.read_text(encoding="utf-8").strip()).resolve() == elsewhere.resolve()


def test_config_path_str_and_path_load(tmp_path: Path) -> None:
    _, _, hooks = _setup(tmp_path, LOG_DEFAULT)
    assert HookEngine(hooks).is_configured
    assert HookEngine(str(hooks)).is_configured
    assert HookEngine(hooks, repo_root=tmp_path).is_configured
    assert HookEngine(str(hooks), repo_root=str(tmp_path)).is_configured


def test_config_path_missing_none_and_empty_are_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "no-such-hooks.yurtle.md"
    assert not HookEngine(missing).is_configured
    assert not HookEngine(str(missing)).is_configured
    assert not HookEngine(None).is_configured
    assert not HookEngine().is_configured
    assert not HookEngine("").is_configured
    assert not HookEngine("", repo_root=str(tmp_path)).is_configured
