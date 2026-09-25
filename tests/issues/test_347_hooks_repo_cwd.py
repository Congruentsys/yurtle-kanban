"""Issue #347 — hook actions run relative to the process cwd, not the repo.

`KanbanService` builds its `HookEngine` from `<repo>/.kanban/hooks/kanban-hooks.yurtle.md`
(or an explicit `hooks_config`), but the engine never learns the repo root:

- a `log` action with no `path` opens `.kanban/hooks.log` relative to the cwd, so a
  service used from another directory writes `<cwd>/.kanban/hooks.log` (and a stray
  `.kanban/` there);
- a `log` action with a relative `path` resolves against the cwd too;
- `shell`, `nats_publish` and `notify` call `subprocess.run` without `cwd=`.

Decided behaviour: hooks built by `KanbanService` run relative to the service's repo
root whatever the cwd is — `log` defaults to `<repo>/.kanban/hooks.log`, a relative
`path` resolves against the repo root and an absolute one is used as-is, and every
subprocess runs with `cwd=<repo>`. Nothing is created in the cwd. A bare
`HookEngine(config_path)` without a repo root keeps its cwd-relative behaviour
(backward compatibility).

The tests reach the engine only through `KanbanService` (its public `create_item`
path, or `service._hook_engine.trigger(...)` as in the issue repro) and never depend
on `_execute_action`'s signature or on how the repo root is handed to the engine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from yurtle_kanban import HookEngine
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.hooks import HookContext, HookEvent
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService


def _hooks_doc(actions_yaml: str, item_types: str = "[expedition]") -> str:
    return (
        "---\n"
        "type: kanban-hooks\n"
        "id: hooks-347\n"
        "version: 1\n"
        "hooks:\n"
        "  on_create:\n"
        f"    - item_types: {item_types}\n"
        "      actions:\n"
        f"{actions_yaml}"
        "---\n"
        "# Hooks 347\n"
    )


LOG_DEFAULT = "        - type: log\n"


def _setup(tmp_path: Path, actions_yaml: str, *, default_location: bool = True) -> tuple[
    Path, Path, Path
]:
    """Make `repo/` (with the hooks file) and `elsewhere/`; return (repo, elsewhere, hooks)."""
    repo = tmp_path / "repo"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if default_location:
        hooks = repo / ".kanban" / "hooks" / "kanban-hooks.yurtle.md"
    else:
        hooks = tmp_path / "config" / "my-hooks.yurtle.md"
    hooks.parent.mkdir(parents=True)
    repo.mkdir(exist_ok=True)
    hooks.write_text(_hooks_doc(actions_yaml), encoding="utf-8")
    return repo, elsewhere, hooks


def _fire(service: KanbanService, item_id: str = "E-1") -> None:
    service._hook_engine.trigger(
        HookEvent.ITEM_CREATED,
        HookContext(event=HookEvent.ITEM_CREATED, item_id=item_id, item_type="expedition"),
    )


def _assert_cwd_untouched(elsewhere: Path) -> None:
    leftovers = sorted(p.relative_to(elsewhere).as_posix() for p in elsewhere.rglob("*"))
    assert leftovers == [], f"hook wrote into the cwd: {leftovers}"


# --- red: log action --------------------------------------------------------------


def test_default_log_path_lands_in_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, _ = _setup(tmp_path, LOG_DEFAULT)
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    _fire(service)
    log = repo / ".kanban" / "hooks.log"
    assert log.is_file(), "no <repo>/.kanban/hooks.log"
    assert '"item_id": "E-1"' in log.read_text(encoding="utf-8")
    _assert_cwd_untouched(elsewhere)


def test_default_log_via_create_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Public path: `create_item` fires on_create; the log belongs to the repo."""
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT)
    hooks.write_text(_hooks_doc(LOG_DEFAULT, item_types="[feature]"), encoding="utf-8")
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    item = service.create_item(WorkItemType.FEATURE, "Hooked item")
    log = repo / ".kanban" / "hooks.log"
    assert log.is_file(), "no <repo>/.kanban/hooks.log"
    assert item.id in log.read_text(encoding="utf-8")
    assert not (elsewhere / ".kanban").exists(), "stray .kanban/ in the cwd"


def test_relative_log_path_resolves_against_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, _ = _setup(
        tmp_path, "        - type: log\n          path: logs/{item_id}.jsonl\n"
    )
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    _fire(service)
    assert (repo / "logs" / "E-1.jsonl").is_file()
    _assert_cwd_untouched(elsewhere)


def test_explicit_hooks_config_log_lands_in_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit `hooks_config` outside the repo still runs relative to the repo."""
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT, default_location=False)
    service = KanbanService(KanbanConfig(), repo, hooks_config=hooks)
    monkeypatch.chdir(elsewhere)
    _fire(service)
    assert (repo / ".kanban" / "hooks.log").is_file()
    assert not (hooks.parent / ".kanban").exists()
    _assert_cwd_untouched(elsewhere)


# --- red: shell action ------------------------------------------------------------


def test_shell_runs_in_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, elsewhere, _ = _setup(
        tmp_path, "        - type: shell\n          command: pwd > out.txt\n"
    )
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    _fire(service)
    out = repo / "out.txt"
    assert out.is_file(), "shell did not run in the repo"
    assert Path(out.read_text(encoding="utf-8").strip()).resolve() == repo.resolve()
    _assert_cwd_untouched(elsewhere)


def test_shell_relative_write_lands_in_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, _ = _setup(
        tmp_path,
        "        - type: shell\n"
        "          command: mkdir -p notes && echo {item_id} > notes/created.txt\n",
    )
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    _fire(service)
    note = repo / "notes" / "created.txt"
    assert note.is_file()
    assert note.read_text(encoding="utf-8").strip() == "E-1"
    _assert_cwd_untouched(elsewhere)


# --- red: nats_publish / notify subprocess cwd -----------------------------------


@pytest.mark.parametrize(
    "actions_yaml",
    [
        "        - type: nats_publish\n",
        "        - type: notify\n          channel: bosun\n",
    ],
    ids=["nats_publish", "notify"],
)
def test_nats_subprocess_runs_in_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actions_yaml: str
) -> None:
    repo, elsewhere, _ = _setup(tmp_path, actions_yaml)
    service = KanbanService(KanbanConfig(), repo)
    calls: list[tuple[Any, dict[str, Any]]] = []

    def fake_run(args: Any, *a: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(subprocess, "run", fake_run)
    _fire(service)
    nats_calls = [kw for args, kw in calls if isinstance(args, list) and args[:1] == ["nats"]]
    assert nats_calls, f"no nats subprocess recorded: {calls}"
    for kw in nats_calls:
        assert "cwd" in kw, "nats subprocess has no cwd="
        assert Path(kw["cwd"]).resolve() == repo.resolve()


# --- green controls ---------------------------------------------------------------


def test_absolute_log_path_used_as_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "abs-logs" / "hooks.jsonl"
    repo, elsewhere, _ = _setup(
        tmp_path, f"        - type: log\n          path: {target.as_posix()}\n"
    )
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    _fire(service)
    assert target.is_file()
    assert not (repo / ".kanban" / "hooks.log").exists()
    _assert_cwd_untouched(elsewhere)


def test_bare_hook_engine_stays_cwd_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backward compat: `HookEngine(config_path)` with no repo root uses the cwd."""
    _, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT, default_location=False)
    engine = HookEngine(hooks)
    monkeypatch.chdir(elsewhere)
    engine.trigger(
        HookEvent.ITEM_CREATED,
        HookContext(event=HookEvent.ITEM_CREATED, item_id="E-9", item_type="expedition"),
    )
    log = elsewhere / ".kanban" / "hooks.log"
    assert log.is_file()
    assert '"item_id": "E-9"' in log.read_text(encoding="utf-8")


def test_bare_hook_engine_shell_stays_cwd_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, elsewhere, hooks = _setup(
        tmp_path,
        "        - type: shell\n          command: pwd > out.txt\n",
        default_location=False,
    )
    engine = HookEngine(hooks)
    monkeypatch.chdir(elsewhere)
    engine.trigger(
        HookEvent.ITEM_CREATED,
        HookContext(event=HookEvent.ITEM_CREATED, item_id="E-9", item_type="expedition"),
    )
    assert (elsewhere / "out.txt").is_file()
