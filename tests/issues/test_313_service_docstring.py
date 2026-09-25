"""#313: document that ``KanbanService`` binds a bare config to its repo.

Follow-up from PR #310 (#300). ``KanbanService(config, repo)`` fills
``config.repo_root`` in place when it is None, so one bare ``KanbanConfig()``
reused across repos stays bound to the first repo (and its themes). The class
docstring should say so.

1. Red: ``KanbanService.__doc__`` mentions ``repo_root`` and that the config is
   bound / filled in place (loose match; wording not pinned).
2. Green control: the documented behaviour itself.
"""

from __future__ import annotations

from pathlib import Path

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService


def test_class_docstring_documents_repo_root_binding() -> None:
    doc = (KanbanService.__doc__ or "").lower()
    assert "repo_root" in doc, "KanbanService docstring should mention repo_root"
    assert "bound" in doc or "in place" in doc, (
        "KanbanService docstring should say a config with no repo_root is "
        "bound to / filled in place with this service's repo"
    )


def test_bare_config_reused_across_repos_stays_bound_to_first(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()
    cfg = KanbanConfig()
    assert cfg.repo_root is None

    KanbanService(cfg, repo_a)
    second = KanbanService(cfg, repo_b)

    assert cfg.repo_root == repo_a.absolute()
    assert cfg.repo_root.is_absolute()
    assert second.config is cfg
    assert second.repo_root == repo_b.absolute()
