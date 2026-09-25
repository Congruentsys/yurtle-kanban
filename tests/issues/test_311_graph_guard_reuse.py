"""Issue #311 — a scan parses each item's frontmatter once, not twice.

Decided behaviour ([steer] on #311):
- `_parse_file` already parses the frontmatter and checks every field for cycles /
  too-large size (#262, #277); it hands that work to `_parse_graph`, so a scan
  does not parse the frontmatter again nor re-run the size check per field.
- Other callers of `_parse_graph(content)` (create/update paths) keep
  self-guarding: with no extra argument, a too-large field is still blanked.
- The graph a scanned item gets is unchanged: a too-large field (#277) and a
  field aliasing an anchor inside it (#296) have no triples; other fields keep theirs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from rdflib import Literal, URIRef

from yurtle_kanban import service as service_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"
N_ITEMS = 5
SCHEMA = "https://yurtle.dev/schema/"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


@pytest.fixture
def board(tmp_path: Path) -> Path:
    """A software-theme board with N_ITEMS ordinary features."""
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(tmp_path, *args)
    proc = subprocess.run(
        [sys.executable, "-c", "from yurtle_kanban.cli import main; main()",
         "init", "--theme", "software"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    svc = _service(tmp_path)
    for n in range(N_ITEMS):
        svc.create_item(WorkItemType.FEATURE, f"Item {n}", description="Body")
    return tmp_path


def _items(repo: Path) -> list[Path]:
    return sorted(p for p in repo.rglob("FEAT-*.md") if ".git" not in p.parts)


def _field_count(path: Path) -> int:
    head = path.read_text(encoding="utf-8")[4:].partition("\n---")[0]
    return len(yaml.safe_load(head))


def _laughs(levels: int = 9, width: int = 10) -> str:
    """Billion-laughs flow value (#277): all anchors inside it, `&a0` a 10-item list."""
    node = "&a0 [" + ", ".join(["lol"] * width) + "]"
    for k in range(1, levels):
        node = f"&a{k} [" + node + f", *a{k - 1}" * (width - 1) + "]"
    return node


def _add_frontmatter(path: Path, lines: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text[:80]
    path.write_text("---\n" + lines + text[4:], encoding="utf-8")


def _counting(monkeypatch: pytest.MonkeyPatch, owner: Any, name: str) -> list[int]:
    """Replace owner.name with a call-through spy; returns the (mutable) counter."""
    calls = [0]
    real = getattr(owner, name)

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, spy)
    return calls


# ---------------------------------------------------------------------------
# Red: a scan parses / size-checks each file's frontmatter once
# ---------------------------------------------------------------------------


class TestScanParsesOnce:
    def test_parse_frontmatter_once_per_file(
        self, board: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = _service(board)
        calls = _counting(monkeypatch, KanbanService, "_parse_frontmatter")
        items = svc.scan()
        assert len(items) == N_ITEMS, [i.id for i in items]
        assert all(i.graph is not None and len(i.graph) > 0 for i in items)
        assert calls[0] == N_ITEMS, f"_parse_frontmatter called {calls[0]}x for {N_ITEMS} files"

    def test_expanded_size_once_per_field(
        self, board: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_expanded_size` is iterative (never calls itself), so every call is a
        top-level per-field check."""
        fields = sum(_field_count(p) for p in _items(board))
        assert fields >= N_ITEMS * 5, fields
        svc = _service(board)
        calls = _counting(monkeypatch, service_mod, "_expanded_size")
        items = svc.scan()
        assert len(items) == N_ITEMS
        assert calls[0] == fields, f"_expanded_size called {calls[0]}x for {fields} fields"


# ---------------------------------------------------------------------------
# Green controls: the graph is unchanged, and direct callers still self-guard
# ---------------------------------------------------------------------------


def _scanned(repo: Path, item_id: str = "FEAT-001") -> Any:
    items = {i.id: i for i in _service(repo).scan()}
    assert item_id in items, sorted(items)
    return items[item_id]


def _objects(graph: Any, key: str) -> set[Any]:
    return set(graph.objects(URIRef("urn:FEAT-001"), URIRef(SCHEMA + key)))


class TestGraphUnchanged:
    def test_too_large_field_blanked_others_kept(self, board: Path) -> None:
        path = _items(board)[0]
        _add_frontmatter(path, f"tags: {_laughs()}\nkeep: [k1, k2]\n")
        item = _scanned(board)
        assert item.graph is not None and len(item.graph) < 10_000, item.graph
        assert (None, None, Literal("lol")) not in item.graph
        assert _objects(item.graph, "keep") == {Literal("k1"), Literal("k2")}
        assert _objects(item.graph, "title") == {Literal("Item 0")}

    def test_alias_of_dropped_anchor_blanked(self, board: Path) -> None:
        path = _items(board)[0]
        _add_frontmatter(path, f"tags: {_laughs()}\nextra: *a0\nkeep: [k1, k2]\n")
        item = _scanned(board)
        assert item.graph is not None and 0 < len(item.graph) < 10_000
        assert (None, None, Literal("lol")) not in item.graph
        assert _objects(item.graph, "extra") == set()
        assert _objects(item.graph, "keep") == {Literal("k1"), Literal("k2")}
        assert _objects(item.graph, "title") == {Literal("Item 0")}
        assert item.metadata.get("extra") == ["lol"] * 10  # kept in metadata (#296)

    def test_direct_parse_graph_still_self_guards(self, board: Path) -> None:
        path = _items(board)[0]
        _add_frontmatter(path, f"tags: {_laughs()}\nextra: *a0\nkeep: [k1, k2]\n")
        graph = _service(board)._parse_graph(path.read_text(encoding="utf-8"))
        assert graph is not None and 0 < len(graph) < 10_000
        assert (None, None, Literal("lol")) not in graph
        assert _objects(graph, "keep") == {Literal("k1"), Literal("k2")}
