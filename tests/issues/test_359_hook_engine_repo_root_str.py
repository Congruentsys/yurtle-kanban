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
type the actions see: the engine writes its root into the caller's `HookContext`
(`trigger` fills `context.repo_root` when it is None), and today that is a `str`.

Controls: a `Path` root, a `None` root (cwd behaviour), and `config_path` given as a
str, a `Path`, `None` or `""` are unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban import HookEngine
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


def test_str_root_context_repo_root_is_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT)
    engine = HookEngine(hooks, repo_root=str(repo))
    monkeypatch.chdir(elsewhere)
    ctx = _fire(engine)
    assert (repo / ".kanban" / "hooks.log").is_file()  # sanity: the hook fired
    assert isinstance(ctx.repo_root, Path), (
        f"actions saw repo_root as {type(ctx.repo_root).__name__}"
    )
    assert ctx.repo_root == repo


def test_relative_str_root_context_repo_root_matches_path_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, hooks = _setup(tmp_path, LOG_DEFAULT)
    monkeypatch.chdir(tmp_path)
    via_path = _fire(HookEngine(hooks, repo_root=Path("repo")))
    via_str = _fire(HookEngine(hooks, repo_root="repo"))
    assert isinstance(via_path.repo_root, Path)  # sanity: the Path baseline
    assert isinstance(via_str.repo_root, Path), (
        f"actions saw repo_root as {type(via_str.repo_root).__name__}"
    )
    assert via_str.repo_root == via_path.repo_root


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
    monkeypatch.chdir(elsewhere)
    ctx = _fire(engine)
    assert (repo / ".kanban" / "hooks.log").is_file()
    assert isinstance(ctx.repo_root, Path)
    assert ctx.repo_root == repo
    _assert_cwd_untouched(elsewhere)


def test_none_root_stays_cwd_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT)
    engine = HookEngine(hooks, repo_root=None)
    monkeypatch.chdir(elsewhere)
    ctx = _fire(engine)
    assert ctx.repo_root is None
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
