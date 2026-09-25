"""Issue #245 — `title`/`id` YAML booleans read as "true"/"false".

Follow-up from the review of PR #242 (#225): `_parse_file` still used `str(title)`
and `str(item_id)`, so `title: true` read as `"True"`, Python's spelling. Decided:
route both through `_scalar_text`, one spelling everywhere.

- `title: true` -> "true"; `title: false` -> "false".
- `id: true` -> "true". `id: false` is falsy, so it falls back to the
  filename-derived ID, as before (control).
- Numbers are unchanged (#179): `title: 2024` -> "2024", `id: 42` -> "42".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItem
from yurtle_kanban.service import KanbanService


def _git_init(path: Path) -> None:
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=path, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with the software theme, cwd set to it."""
    from yurtle_kanban import config as config_mod

    _git_init(tmp_path)
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/features/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    config_mod._theme_cache.clear()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _only_item(repo: Path, front: str, stem: str = "FEAT-001-x") -> WorkItem:
    """Write one item with frontmatter `front`; return the single item scanned."""
    path = repo / "kanban-work" / "features" / f"{stem}.md"
    path.write_text(f"---\n{front}type: feature\nstatus: backlog\n---\n\nBody\n")
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    items = service.get_items()
    assert len(items) == 1, items
    return items[0]


# ---------------------------------------------------------------------------
# 1. title
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "false"])
def test_boolean_title_reads_lowercase(repo: Path, value: str) -> None:
    item = _only_item(repo, f"id: FEAT-001\ntitle: {value}\n")
    assert item.title == value, repr(item.title)


def test_numeric_title_unchanged_control(repo: Path) -> None:
    item = _only_item(repo, "id: FEAT-001\ntitle: 2024\n")
    assert item.title == "2024"


def test_quoted_capitalised_title_unchanged_control(repo: Path) -> None:
    item = _only_item(repo, "id: FEAT-001\ntitle: 'True'\n")
    assert item.title == "True"


# ---------------------------------------------------------------------------
# 2. id
# ---------------------------------------------------------------------------


def test_true_id_reads_lowercase(repo: Path) -> None:
    item = _only_item(repo, "id: true\ntitle: x\n")
    assert item.id == "true", repr(item.id)


def test_false_id_falls_back_to_filename_control(repo: Path) -> None:
    item = _only_item(repo, "id: false\ntitle: x\n", stem="feat-007-thing")
    assert item.id == "FEAT_007_THING", repr(item.id)


def test_numeric_id_unchanged_control(repo: Path) -> None:
    item = _only_item(repo, "id: 42\ntitle: x\n")
    assert item.id == "42"
