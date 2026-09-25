"""Issue #309 — `create_experiment_run` on an EXPR whose frontmatter doesn't parse.

`create_experiment_run` looks the experiment up to copy its `hypothesis:` into the
run's config.yaml, via `fm = self._parse_frontmatter(content); fm.get(...)`. When the
file's frontmatter doesn't parse (broken YAML, nested too deeply) or isn't a mapping
(a YAML list, or no frontmatter at all), `_parse_frontmatter` returns None / a list
and `.get` raises AttributeError, so no run is created.

Route (real code, no monkeypatching): an HDD board with a valid EXPR-001 is scanned
(`svc.scan()`, so `get_item` returns the cached item), then the file is overwritten
with bad frontmatter before `create_experiment_run` re-reads it — the same
"rewritten after the scan" route #188's and #297's tests use. A scan alone can't
reach it: `_parse_file` drops an item whose frontmatter isn't a non-empty mapping.

Decided behaviour:
- The run is still created, its config.yaml has `hypothesis: ""` (an empty string)
  and the other fields as usual; no exception.
- Control: a well-formed EXPR with `hypothesis: H-1` still records `H-1`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"
EXPR = "EXPR-001"

_BODY = "\n# Seed\n\nBody text.\n"
VALID_DOC = (
    "---\n"
    f"id: {EXPR}\n"
    'title: "Seed"\n'
    "type: experiment\n"
    "status: draft\n"
    "hypothesis: H-1\n"
    "priority: medium\n"
    "---\n" + _BODY
)

BAD_DOCS = {
    # yaml.safe_load raises YAMLError -> None
    "broken-yaml": f"---\nid: {EXPR}\ntype: experiment\nextra: [unclosed\n---\n" + _BODY,
    # yaml.safe_load raises RecursionError -> None (#297)
    "too-deep": (
        f"---\nid: {EXPR}\ntype: experiment\nextra: " + "[" * 1000 + "x" + "]" * 1000
        + "\n---\n" + _BODY
    ),
    # parses, but to a list: not a mapping
    "list": "---\n- a\n- b\n---\n" + _BODY,
    # parses, but to a bare string: not a mapping
    "scalar": "---\njust text\n---\n" + _BODY,
    # empty frontmatter: safe_load gives None
    "empty": "---\n\n---\n" + _BODY,
    # no `---` block at all: _split_frontmatter gives None
    "no-frontmatter": "# Seed\n\nNo frontmatter here.\n",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def board(tmp_path: Path) -> Path:
    """An HDD-theme board (real `init`) with a valid EXPR-001 carrying `hypothesis: H-1`."""
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(tmp_path, *args)
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", "from yurtle_kanban.cli import main; main()",
         "init", "--theme", "hdd"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    expr_dir = tmp_path / "research" / "experiments"
    expr_dir.mkdir(parents=True, exist_ok=True)
    (expr_dir / f"{EXPR}-Seed.md").write_text(VALID_DOC)
    return tmp_path


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _config(run: Path) -> dict:
    data = yaml.safe_load((run / "config.yaml").read_text())
    assert isinstance(data, dict)
    return data


def _scanned_then_broken(repo: Path, doc: str) -> KanbanService:
    """A service that has scanned the valid EXPR, whose file is then overwritten."""
    svc = _service(repo)
    svc.scan()
    item = svc.get_item(EXPR)
    assert item is not None and item.file_path is not None, "fixture: EXPR not scanned"
    item.file_path.write_text(doc)
    return svc


class TestBrokenFrontmatter:
    @pytest.mark.parametrize("case", list(BAD_DOCS), ids=list(BAD_DOCS))
    def test_run_created_with_empty_hypothesis(self, board: Path, case: str) -> None:
        svc = _scanned_then_broken(board, BAD_DOCS[case])
        run = svc.create_experiment_run(EXPR, being="b-v1", run_by="Tester")
        assert (run / "config.yaml").is_file()
        config = _config(run)
        assert config["hypothesis"] == ""
        assert config["experiment"] == EXPR
        assert config["being"] == "b-v1"
        assert config["run_by"] == "Tester"
        assert config["status"] == "running"

    def test_run_listed_afterwards(self, board: Path) -> None:
        svc = _scanned_then_broken(board, BAD_DOCS["broken-yaml"])
        svc.create_experiment_run(EXPR, being="b-v1", run_by="Tester")
        runs = svc.get_experiment_runs(EXPR)
        assert len(runs) == 1
        assert runs[0]["being"] == "b-v1"


class TestControl:
    def test_valid_hypothesis_recorded(self, board: Path) -> None:
        run = _service(board).create_experiment_run(EXPR, being="b-v1", run_by="Tester")
        assert _config(run)["hypothesis"] == "H-1"

    def test_valid_after_scan_recorded(self, board: Path) -> None:
        svc = _scanned_then_broken(board, VALID_DOC.replace("H-1", "H-2"))
        run = svc.create_experiment_run(EXPR, being="b-v1", run_by="Tester")
        assert _config(run)["hypothesis"] == "H-2"
