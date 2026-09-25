"""Issue #321 — frontmatter that parses to something other than a mapping.

`_parse_frontmatter` is typed `dict[str, Any] | None` but returns whatever
`yaml.safe_load` gives: a non-empty YAML list or a bare scalar passes straight
through. `_get_hdd_frontmatter` (`... or {}`) and `backfill_turtle_blocks`
(`if not frontmatter`) only guard falsy values, so a file rewritten after the scan
as `- a` / `just text` reaches `.get(...)` and raises AttributeError — even with
`dry_run=True`.

Route (real code, no monkeypatching): an HDD board (real `init --theme hdd`) with a
valid EXPR-001 and HYP H-1 is scanned, then EXPR-001's file is overwritten — the
"rewritten after the scan" route #309's tests use. A scan alone can't reach it:
`_parse_file` drops an item whose frontmatter isn't a non-empty mapping.

Decided behaviour ([steer] on #321):
- `_parse_frontmatter` returns None for any non-mapping (list, str, int), as it
  already does for broken YAML. A mapping — including `{}` — and the empty-block
  None cases are unchanged.
- `get_hdd_cross_references`, `validate_hdd_links`, `build_cross_board_graph`,
  `get_experiment_readiness` treat such a file as having no frontmatter.
- `backfill_turtle_blocks` (dry-run and real) skips it; the file is untouched.
- `create_experiment_run` writes `hypothesis: ""` when the EXPR's `hypothesis:` is
  empty or null (today: null). Control: `hypothesis: H-1` still `H-1`.
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
HYP = "H-1"

_BODY = "\n# Seed\n\nBody text.\n"


def _expr_doc(hypothesis_line: str = f"hypothesis: {HYP}\n") -> str:
    return (
        "---\n"
        f"id: {EXPR}\n"
        'title: "Seed"\n'
        "type: experiment\n"
        "status: draft\n"
        f"{hypothesis_line}"
        "priority: medium\n"
        "---\n" + _BODY
    )


HYP_DOC = (
    "---\n"
    f"id: {HYP}\n"
    'title: "Hyp"\n'
    "type: hypothesis\n"
    "status: draft\n"
    "paper: PAPER-1\n"
    "priority: medium\n"
    "---\n\n# Hyp\n\nBody.\n"
)

# Non-empty, parseable, not a mapping.
NON_MAPPING_DOCS = {
    "list": "---\n- a\n- b\n---\n" + _BODY,
    "list-of-maps": "---\n- id: EXPR-001\n  type: experiment\n---\n" + _BODY,
    "str": "---\njust text\n---\n" + _BODY,
    "int": "---\n5\n---\n" + _BODY,
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def board(tmp_path: Path) -> Path:
    """HDD-theme board with a valid EXPR-001 (hypothesis H-1) and HYP H-1."""
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
    (expr_dir / f"{EXPR}-Seed.md").write_text(_expr_doc())
    hyp_dir = tmp_path / "research" / "hypotheses"
    hyp_dir.mkdir(parents=True, exist_ok=True)
    (hyp_dir / f"{HYP}-Hyp.md").write_text(HYP_DOC)
    return tmp_path


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _scanned_then_rewritten(repo: Path, doc: str) -> tuple[KanbanService, Path]:
    """A service that has scanned the valid board; EXPR-001 is then overwritten."""
    svc = _service(repo)
    svc.scan()
    item = svc.get_item(EXPR)
    assert item is not None and item.file_path is not None, "fixture: EXPR not scanned"
    assert svc.get_item(HYP) is not None, "fixture: HYP not scanned"
    item.file_path.write_text(doc)
    return svc, item.file_path


# ---------------------------------------------------------------------------
# 1. _parse_frontmatter: mapping or None
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    @pytest.mark.parametrize("case", list(NON_MAPPING_DOCS), ids=list(NON_MAPPING_DOCS))
    def test_non_mapping_is_none(self, board: Path, case: str) -> None:
        assert _service(board)._parse_frontmatter(NON_MAPPING_DOCS[case]) is None

    @pytest.mark.parametrize(
        ("doc", "expected"),
        [
            ("---\n{}\n---\nb\n", {}),
            ("---\n---\nb\n", None),
            ("---\n\n---\nb\n", None),
            ("---\nnull\n---\nb\n", None),
            ("---\nid: X-1\ntype: experiment\n---\nb\n", {"id": "X-1", "type": "experiment"}),
            ("---\nextra: [unclosed\n---\nb\n", None),
        ],
        ids=["empty-mapping", "no-body", "blank-body", "null", "mapping", "broken-yaml"],
    )
    def test_control_unchanged(self, board: Path, doc: str, expected: object) -> None:
        assert _service(board)._parse_frontmatter(doc) == expected


# ---------------------------------------------------------------------------
# 2. Callers of _get_hdd_frontmatter / backfill_turtle_blocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(NON_MAPPING_DOCS), ids=list(NON_MAPPING_DOCS))
class TestHddCallers:
    def test_cross_references(self, board: Path, case: str) -> None:
        svc, _ = _scanned_then_rewritten(board, NON_MAPPING_DOCS[case])
        xrefs = svc.get_hdd_cross_references()
        exprs = {e["id"]: e for e in xrefs["experiments"]}
        assert EXPR in exprs
        # the rewritten file has no frontmatter to link from
        assert exprs[EXPR]["hypothesis"] == ""
        # the valid hypothesis is still reported
        assert HYP in {h["id"] for h in xrefs["hypotheses"]}

    def test_validate_hdd_links(self, board: Path, case: str) -> None:
        svc, _ = _scanned_then_rewritten(board, NON_MAPPING_DOCS[case])
        assert isinstance(svc.validate_hdd_links(), dict)

    def test_build_cross_board_graph(self, board: Path, case: str) -> None:
        svc, _ = _scanned_then_rewritten(board, NON_MAPPING_DOCS[case])
        assert isinstance(svc.build_cross_board_graph(), dict)

    def test_experiment_readiness(self, board: Path, case: str) -> None:
        svc, _ = _scanned_then_rewritten(board, NON_MAPPING_DOCS[case])
        result = svc.get_experiment_readiness(EXPR)
        assert "error" not in result
        assert HYP not in result.get("blocking_chain", [])

    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "write"])
    def test_backfill_skips(self, board: Path, case: str, dry_run: bool) -> None:
        doc = NON_MAPPING_DOCS[case]
        svc, path = _scanned_then_rewritten(board, doc)
        results = svc.backfill_turtle_blocks(dry_run=dry_run)
        assert EXPR not in {r.get("id") for r in results}
        assert path.read_text() == doc


class TestHddCallersControl:
    def test_cross_references_valid(self, board: Path) -> None:
        svc = _service(board)
        svc.scan()
        exprs = {e["id"]: e for e in svc.get_hdd_cross_references()["experiments"]}
        assert exprs[EXPR]["hypothesis"] == HYP

    def test_backfill_valid_dry_run_reports_expr(self, board: Path) -> None:
        svc = _service(board)
        svc.scan()
        results = svc.backfill_turtle_blocks(dry_run=True)
        assert EXPR in {r.get("id") for r in results}


# ---------------------------------------------------------------------------
# 3. create_experiment_run: hypothesis: null/empty -> ""
# ---------------------------------------------------------------------------


def _run_hypothesis(repo: Path, hypothesis_line: str) -> object:
    svc, _ = _scanned_then_rewritten(repo, _expr_doc(hypothesis_line))
    run = svc.create_experiment_run(EXPR, being="b-v1", run_by="Tester")
    data = yaml.safe_load((run / "config.yaml").read_text())
    assert isinstance(data, dict)
    assert data["experiment"] == EXPR
    return data["hypothesis"]


class TestRunHypothesis:
    @pytest.mark.parametrize(
        "line",
        ["hypothesis:\n", "hypothesis: null\n", "hypothesis: ~\n"],
        ids=["empty", "null", "tilde"],
    )
    def test_null_written_as_empty_string(self, board: Path, line: str) -> None:
        assert _run_hypothesis(board, line) == ""

    def test_control_value_kept(self, board: Path) -> None:
        assert _run_hypothesis(board, f"hypothesis: {HYP}\n") == HYP

    def test_control_quoted_empty_kept(self, board: Path) -> None:
        assert _run_hypothesis(board, 'hypothesis: ""\n') == ""

    def test_control_absent_is_empty_string(self, board: Path) -> None:
        assert _run_hypothesis(board, "") == ""
