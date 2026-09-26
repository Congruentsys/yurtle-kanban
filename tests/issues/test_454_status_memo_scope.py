# ruff: noqa: F811  -- pytest fixtures imported from the #439 test module are re-bound as args
"""Issue #454 — follow-ups from the review of PR #453 (#448): the scan's status-name
memo leaks into placement, and two #448 behaviours are unpinned.

Decided behaviour:
1. (F3) `_get_type_directory` reads the CURRENT theme, not the scan memo. In one
   long-lived `KanbanService` (MCP), an override theme file that appears after a
   `scan()` is seen by the next placement with no rescan — as on main before #453.
   Public variant: `create_item` puts the file in the override's folder.
2. (F1) `query`'s two human tables name the status as the item's theme does: on hdd
   the `--no-semantic` NL table and the `--semantic` hits table say `draft` / `active`,
   not `backlog` / `in_progress`.
3. (F2) The per-board `names` memo: a multi-board scan (nautical dev + hdd research)
   of 60+ research items with a theme status name loads the board theme well under
   once per item.
4. (F4) Cache set up in `__init__`: no behaviour of its own, not pinned here.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_439_list_theme_status import (  # noqa: F401  (fixtures)
    THEMES_DIR,
    _clear_theme_cache,
    _created_id,
    _invoke,
    _multiboard,
    _native,
    _text,
    repo,
    runner,
    wide,
)
from tests.issues.test_448_status_label_everywhere import MOVE
from yurtle_kanban import cli
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.query import EmbeddingHit, EmbeddingIndex
from yurtle_kanban.service import KanbanService

# ---------------------------------------------------------------------------
# 1. F3: placement reads the current theme, not the scan memo
# ---------------------------------------------------------------------------

NEW_FEATURE_PATH = "kanban-work/feats/"


def _write_software_override(repo: Path) -> None:
    """A repo override of the software theme that moves features to `feats/`."""
    data = yaml.safe_load((THEMES_DIR / "software.yaml").read_text())
    data["item_types"]["feature"]["path"] = NEW_FEATURE_PATH
    themes = repo / ".kanban" / "themes"
    themes.mkdir(parents=True, exist_ok=True)
    (themes / "software.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    _clear_theme_cache()


@pytest.fixture
def scanned_software(repo: Path, runner: CliRunner, wide: io.StringIO) -> KanbanService:
    """A software repo with one feature, and a service that has already scanned it
    (so `create_item` won't rescan: `_items` is non-empty)."""
    _invoke(runner, ["init", "--theme", "software"])
    _created_id(runner, wide, ["create", "feature", "first feature"])
    service = cli.get_service()
    assert service.scan(), "the scan should find the first feature"
    return service


def _fresh_feature_dir() -> Path:
    """Where a brand-new service (reading the theme from scratch) places features."""
    return cli.get_service()._get_type_directory(WorkItemType.FEATURE)


def test_override_changes_fresh_placement(
    repo: Path, scanned_software: KanbanService
) -> None:
    """Control: the override really moves features (green today)."""
    before = scanned_software._get_type_directory(WorkItemType.FEATURE)
    _write_software_override(repo)
    after = _fresh_feature_dir()
    assert after != before
    assert after.name == "feats", after


def test_type_directory_sees_override_without_rescan(
    repo: Path, scanned_software: KanbanService
) -> None:
    _write_software_override(repo)
    expected = _fresh_feature_dir()
    assert expected.name == "feats", expected
    # the same long-lived service, no rescan in between
    assert scanned_software._get_type_directory(WorkItemType.FEATURE) == expected


def test_create_item_uses_override_without_rescan(
    repo: Path, scanned_software: KanbanService
) -> None:
    _write_software_override(repo)
    expected = _fresh_feature_dir()
    item = scanned_software.create_item(WorkItemType.FEATURE, "second feature")
    assert item.file_path is not None
    assert item.file_path.parent == expected, item.file_path
    assert item.file_path.exists()


# ---------------------------------------------------------------------------
# 2. F1: `query`'s two human tables use the theme's status name
# ---------------------------------------------------------------------------

QUERY_PHRASE = "experiments"


def _box_status(text: str, item_id: str) -> str:
    """The Status cell of `item_id`'s row in a boxed rich table (`query`'s tables)."""
    rows = [
        [c.strip() for c in re.split(r"[\u2502\u2503]", line)[1:-1]]
        for line in text.splitlines()
        if line.lstrip()[:1] in ("\u2502", "\u2503")
    ]
    header = next((r for r in rows if "Status" in r and "ID" in r), None)
    assert header is not None, f"no table header in:\n{text}"
    status_col = header.index("Status")
    for row in rows:
        if row and row[0] == item_id:
            return row[status_col]
    raise AssertionError(f"no row for {item_id} in:\n{text}")


class _ItemIndex:
    """A working semantic index whose hits carry the item (so a Status is shown)."""

    def __init__(self, service: Any) -> None:
        self._items = sorted(service.scan(), key=lambda i: i.id)

    def search(self, query: str, top_k: int = 10) -> list[EmbeddingHit]:
        n = len(self._items)
        return [
            EmbeddingHit(item_id=item.id, score=(n - k) / n, item=item)
            for k, item in enumerate(self._items)
        ][:top_k]


@pytest.fixture
def item_semantic(monkeypatch: pytest.MonkeyPatch) -> None:
    def from_service(cls: type, service: Any, cache_dir: Path | None = None) -> _ItemIndex:
        return _ItemIndex(service)

    monkeypatch.setattr(EmbeddingIndex, "from_service", classmethod(from_service))


def _hdd_experiment(runner: CliRunner, wide: io.StringIO) -> str:
    _invoke(runner, ["init", "--theme", "hdd"])
    return _created_id(runner, wide, ["create", "experiment", "Cache latency study"])


def test_hdd_query_nl_table_uses_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    item_id = _hdd_experiment(runner, wide)
    args = ["query", "--no-semantic", QUERY_PHRASE]
    assert _box_status(_text(runner, wide, args), item_id) == _native("hdd", "backlog")
    _invoke(runner, ["move", item_id, "active", *MOVE])
    assert _box_status(_text(runner, wide, args), item_id) == "active"


def test_hdd_query_semantic_table_uses_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO, item_semantic: None
) -> None:
    item_id = _hdd_experiment(runner, wide)
    args = ["query", "--semantic", "caching"]
    assert _box_status(_text(runner, wide, args), item_id) == "draft"
    _invoke(runner, ["move", item_id, "active", *MOVE])
    assert _box_status(_text(runner, wide, args), item_id) == "active"


def test_software_query_nl_table_stays_canonical(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "software"])
    item_id = _created_id(runner, wide, ["create", "feature", "a feature"])
    text = _text(runner, wide, ["query", "--no-semantic", "features"])
    assert _box_status(text, item_id) == "backlog"


# ---------------------------------------------------------------------------
# 3. F2: the per-board memo — a multi-board scan doesn't load a theme per item
# ---------------------------------------------------------------------------

N_RESEARCH = 60


def _many_research_items(repo: Path, runner: CliRunner, wide: io.StringIO) -> None:
    _multiboard(runner)
    first = _created_id(runner, wide, ["create", "idea", "template"])
    template = next(p for p in repo.rglob(f"{first}-*.md") if ".git" not in p.parts)
    assert "research" in template.relative_to(repo).parts, template
    body = re.sub(r"(?m)^status: .*$", "status: active", template.read_text())
    template.write_text(body)
    for n in range(2, N_RESEARCH + 1):
        new_id = first.replace("001", f"{n:03d}")
        (template.parent / f"{new_id}-item.md").write_text(body.replace(first, new_id))
    _created_id(runner, wide, ["create", "expedition", "a dev item"])  # the other board


def test_multiboard_scan_memoises_status_names_per_board(
    repo: Path, runner: CliRunner, wide: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yurtle_kanban import config as config_mod

    _many_research_items(repo, runner, wide)
    service = cli.get_service()

    calls = {"board": 0, "builtin": 0}
    real_board = KanbanService._load_board_theme
    real_builtin = config_mod._load_builtin_theme

    def count_board(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        calls["board"] += 1
        return real_board(self, *args, **kwargs)

    def count_builtin(*args: Any, **kwargs: Any) -> Any:
        calls["builtin"] += 1
        return real_builtin(*args, **kwargs)

    monkeypatch.setattr(KanbanService, "_load_board_theme", count_board)
    monkeypatch.setattr(config_mod, "_load_builtin_theme", count_builtin)
    items = service.scan()

    research = [i for i in items if i.id.startswith("IDEA")]
    assert len(research) == N_RESEARCH, [i.id for i in items]
    assert {i.status.value for i in research} == {"in_progress"}  # `active` read back
    assert calls["board"] < 15, f"{calls['board']} board-theme loads for {N_RESEARCH} items"
    assert calls["builtin"] < 15, f"{calls['builtin']} theme loads for {N_RESEARCH} items"
