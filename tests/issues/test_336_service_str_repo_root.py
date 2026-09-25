"""Issue #336 — `KanbanService` with a `str` (or relative) repo_root.

`KanbanService.__init__` accepts `repo_root: Path | str` and stores
`self.repo_root = Path(repo_root).absolute()` (#198, #300), but built the workflow
parser and the default hooks path from the RAW argument. So:

- a `str` root raised `TypeError` (`str / str`);
- a relative `Path` root left the workflow parser holding a relative
  `.kanban` dir, so workflows loaded lazily resolved against whatever the cwd was
  at call time, not the repo the service was built for.

Decided fix: both use `self.repo_root` (absolute).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

WORKFLOW = """\
---
type: kanban-workflow
id: wf-336
applies_to: expedition
version: 1
---
# Workflow 336
"""

HOOKS = """\
---
type: kanban-hooks
id: hooks-336
version: 1
hooks:
  on_create:
    - item_types: [expedition]
      actions:
        - type: log
---
# Hooks 336
"""


def _make_repo(parent: Path, name: str = "repo") -> Path:
    repo = parent / name
    (repo / ".kanban" / "workflows").mkdir(parents=True)
    (repo / ".kanban" / "hooks").mkdir(parents=True)
    (repo / ".kanban" / "workflows" / "wf-336.yurtle.md").write_text(
        WORKFLOW, encoding="utf-8"
    )
    (repo / ".kanban" / "hooks" / "kanban-hooks.yurtle.md").write_text(
        HOOKS, encoding="utf-8"
    )
    return repo


def _assert_bound_to(service: KanbanService, repo: Path) -> None:
    assert service._hook_engine.is_configured, "hooks file under repo not loaded"
    wf = service.get_workflow("expedition")
    assert wf is not None, "workflow under repo/.kanban/workflows not found"
    assert wf.id == "wf-336"
    assert Path(wf.source_file).is_absolute()
    assert Path(wf.source_file).resolve().is_relative_to(repo.resolve())


# --- red: str root --------------------------------------------------------------


def test_str_root_constructs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    service = KanbanService(KanbanConfig(), str(repo))
    assert service.repo_root == repo.absolute()


def test_relative_str_root_binds_workflow_and_hooks_to_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)
    service = KanbanService(KanbanConfig(), "repo")
    assert service.repo_root == repo.absolute()
    assert service._workflow_parser.config_dir.is_absolute()
    # workflows load lazily: leave the parent dir before the first lookup
    monkeypatch.chdir(elsewhere)
    _assert_bound_to(service, repo)


# --- red: relative Path root (same cwd-dependence) ------------------------------


def test_relative_path_root_workflow_survives_chdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)
    service = KanbanService(KanbanConfig(), Path("repo"))
    assert service._workflow_parser.config_dir.is_absolute()
    monkeypatch.chdir(elsewhere)
    _assert_bound_to(service, repo)


def test_relative_path_root_does_not_read_decoy_in_new_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After chdir, a same-named `repo/` under the new cwd must not be consulted."""
    _make_repo(tmp_path)
    decoy_parent = tmp_path / "decoy"
    decoy_parent.mkdir()
    decoy = _make_repo(decoy_parent)
    (decoy / ".kanban" / "workflows" / "wf-336.yurtle.md").write_text(
        WORKFLOW.replace("id: wf-336", "id: decoy"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    service = KanbanService(KanbanConfig(), Path("repo"))
    monkeypatch.chdir(decoy_parent)
    wf = service.get_workflow("expedition")
    assert wf is not None and wf.id == "wf-336"


# --- green controls -------------------------------------------------------------


def test_absolute_path_root_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    service = KanbanService(KanbanConfig(), repo)
    assert service.repo_root == repo
    monkeypatch.chdir(tmp_path)
    _assert_bound_to(service, repo)


def test_explicit_hooks_config_still_wins(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / ".kanban" / "hooks" / "kanban-hooks.yurtle.md").unlink()
    other = tmp_path / "other-hooks.yurtle.md"
    other.write_text(HOOKS, encoding="utf-8")
    service = KanbanService(KanbanConfig(), repo, hooks_config=other)
    assert service._hook_engine.is_configured


def test_missing_kanban_dir_is_quiet(tmp_path: Path) -> None:
    repo = tmp_path / "bare"
    repo.mkdir()
    service = KanbanService(KanbanConfig(), repo)
    assert not service._hook_engine.is_configured
    assert service.get_workflow("expedition") is None
