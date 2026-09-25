"""Issue #348 — `hooks_config` / `HookEngine` given a `str` path.

`KanbanService(..., hooks_config=...)` and `HookEngine(config_path)` are annotated
`Path | None`, but a `str` raised
`AttributeError: 'str' object has no attribute 'exists'` (`hooks.py`) — the same
class of bug as #336 (`repo_root: str`).

Decided behaviour:
- a `str` path (absolute or relative) is accepted by both the service and
  `HookEngine`, and the hooks in it are loaded;
- a relative `str` resolves exactly as a relative `Path` does (against the process
  cwd) — this pins the equivalence, not a particular base;
- a `str` to a missing file is quiet and unconfigured, like a missing `Path`;
- `Path` and `None` behave as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.hooks import HookEngine
from yurtle_kanban.service import KanbanService

HOOKS = """\
---
type: kanban-hooks
id: hooks-348
version: 1
hooks:
  on_create:
    - item_types: [expedition]
      actions:
        - type: log
---
# Hooks 348
"""


def _make_repo(parent: Path) -> Path:
    repo = parent / "repo"
    (repo / ".kanban").mkdir(parents=True)
    return repo


def _write_hooks(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOKS, encoding="utf-8")
    return path


# --- red: service with a str hooks_config -------------------------------------


def test_service_str_hooks_config_constructs_and_loads(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    hooks = _write_hooks(tmp_path / "custom-hooks.yurtle.md")
    service = KanbanService(KanbanConfig(), repo, hooks_config=str(hooks))
    assert service._hook_engine.is_configured


def test_service_str_hooks_config_missing_file_is_quiet(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    missing = tmp_path / "no-such-hooks.yurtle.md"
    service = KanbanService(KanbanConfig(), repo, hooks_config=str(missing))
    assert not service._hook_engine.is_configured


def test_service_relative_str_hooks_config_matches_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    _write_hooks(tmp_path / "hooks" / "custom-hooks.yurtle.md")
    rel = "hooks/custom-hooks.yurtle.md"
    monkeypatch.chdir(tmp_path)
    via_path = KanbanService(KanbanConfig(), repo, hooks_config=Path(rel))
    via_str = KanbanService(KanbanConfig(), repo, hooks_config=rel)
    assert via_path._hook_engine.is_configured  # sanity: the Path baseline loads
    assert via_str._hook_engine.is_configured == via_path._hook_engine.is_configured


# --- red: HookEngine with a str config_path -----------------------------------


def test_hook_engine_absolute_str_loads(tmp_path: Path) -> None:
    hooks = _write_hooks(tmp_path / "custom-hooks.yurtle.md")
    assert Path(str(hooks)).is_absolute()
    engine = HookEngine(str(hooks))
    assert engine.is_configured


def test_hook_engine_str_missing_file_is_quiet(tmp_path: Path) -> None:
    engine = HookEngine(str(tmp_path / "no-such-hooks.yurtle.md"))
    assert not engine.is_configured


def test_hook_engine_relative_str_matches_relative_path_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_hooks(tmp_path / "hooks" / "custom-hooks.yurtle.md")
    rel = "hooks/custom-hooks.yurtle.md"
    monkeypatch.chdir(tmp_path)
    via_path = HookEngine(Path(rel))
    via_str = HookEngine(rel)
    assert via_path.is_configured  # sanity: the Path baseline loads
    assert via_str.is_configured == via_path.is_configured


def test_hook_engine_relative_str_matches_relative_path_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From a cwd where the relative path does not exist, both are unconfigured."""
    _write_hooks(tmp_path / "hooks" / "custom-hooks.yurtle.md")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    rel = "hooks/custom-hooks.yurtle.md"
    monkeypatch.chdir(elsewhere)
    via_path = HookEngine(Path(rel))
    via_str = HookEngine(rel)
    assert not via_path.is_configured  # sanity: the Path baseline misses
    assert via_str.is_configured == via_path.is_configured


# --- green controls ------------------------------------------------------------


def test_service_path_hooks_config_unchanged(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    hooks = _write_hooks(tmp_path / "custom-hooks.yurtle.md")
    service = KanbanService(KanbanConfig(), repo, hooks_config=hooks)
    assert service._hook_engine.is_configured


def test_service_path_hooks_config_missing_is_quiet(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    missing = tmp_path / "no-such-hooks.yurtle.md"
    service = KanbanService(KanbanConfig(), repo, hooks_config=missing)
    assert not service._hook_engine.is_configured


def test_service_none_hooks_config_uses_repo_default(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_hooks(repo / ".kanban" / "hooks" / "kanban-hooks.yurtle.md")
    service = KanbanService(KanbanConfig(), repo, hooks_config=None)
    assert service._hook_engine.is_configured


def test_service_none_hooks_config_without_default_is_quiet(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    service = KanbanService(KanbanConfig(), repo)
    assert not service._hook_engine.is_configured


def test_hook_engine_path_and_none_unchanged(tmp_path: Path) -> None:
    hooks = _write_hooks(tmp_path / "custom-hooks.yurtle.md")
    assert HookEngine(hooks).is_configured
    assert not HookEngine(tmp_path / "no-such-hooks.yurtle.md").is_configured
    assert not HookEngine(None).is_configured
    assert not HookEngine().is_configured
