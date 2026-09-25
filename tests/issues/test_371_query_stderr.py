# ruff: noqa: F811  -- pytest fixtures imported from the #346 / #358 test modules are re-bound
"""Issue #371 (with #360 folded in) — query follow-ups from the review of PR #368 (#358).

Decided behaviour (click 8.5: `result.stdout` / `result.stderr` are separate,
`result.output` is both):
1. `query --json --verbose "<nl>"`: stdout is exactly valid JSON with the same ids
   as without `--verbose`; the "Parsed query:" block goes to stderr. Without
   `--json`, `--verbose` still shows the block (stream not pinned).
2. Error paths under `--json` go to stderr, stdout stays empty:
   - `query --json --semantic x` without the search extra: exit 1, hint on stderr.
   - `query --json --sparql "<invalid>"`: exit 1 (today's code), message on stderr.
   Without `--json` the error text is still shown (checked on the combined output).
3. `EmbeddingIndex._load_model` keeps the original ImportError text: a broken torch
   under sentence-transformers surfaces as "... (torch broken XYZ) ..." plus the
   `[search]` install hint, not just "sentence-transformers is required".
4. The hybrid graph-only hint says "unavailable" (it also covers installed-but-broken),
   not "not installed".
5. The graph-only fallback respects `top_k` (no extras, broken extra, structured-only).
Green controls: the hybrid path with a working index still ranks and slices to
`top_k`; non-JSON output is unchanged apart from the hint wording.

#360 (module-level `err_console`) is an internal refactor with no observable
behaviour of its own; items 1, 2 and 4 are the stderr call sites it serves.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_346_semantic_extras_fallback import (  # noqa: F401 (fixtures)
    SEMANTIC_PHRASE,
    _assert_clean,
    _hint_lines,
    _ids,
    no_extras,
    repo,
    runner,
)
from tests.issues.test_358_query_json_and_broken_extra import (  # noqa: F401 (fixtures)
    broken_extra,
)
from yurtle_kanban.cli import get_service, main
from yurtle_kanban.query import (
    EmbeddingHit,
    EmbeddingIndex,
    QueryEngine,
    UnifiedGraph,
)

INVALID_SPARQL = "SELECT ?s WHERE { this is not sparql"
TORCH_ERROR = "torch broken XYZ"
MANY_PHRASE_HYBRID = "experiments about caching"  # structured + semantic part
MANY_PHRASE_STRUCTURED = "experiments"  # structured only, no semantic part


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def many(repo: Path, runner: CliRunner) -> Path:
    """The #346 repo plus four more experiments: seven experiments in all."""
    for title in ("Cache warmup", "Cache eviction", "Cache sizing", "Cache sharding"):
        result = runner.invoke(main, ["create", "experiment", title, "--assignee", "Mini"])
        assert result.exit_code == 0, result.output
    return repo


def _json_rows(stdout: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not valid JSON ({e}):\n{stdout!r}") from e
    assert isinstance(rows, list), stdout
    return rows


def _has_install_hint(text: str) -> bool:
    return "[search]" in text or "sentence-transformers" in text.lower()


class _TorchBrokenFinder:
    """Meta-path finder: importing sentence_transformers fails the way a broken torch does."""

    @staticmethod
    def find_spec(name: str, path: Any = None, target: Any = None) -> None:
        if name.split(".")[0] == "sentence_transformers":
            raise ImportError(TORCH_ERROR)
        return None


@pytest.fixture
def torch_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    """numpy imports (a stub), sentence_transformers passes the probe but its import
    raises ImportError("torch broken XYZ")."""
    for name in list(sys.modules):
        if name.split(".")[0] in ("numpy", "sentence_transformers"):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> Any:
        if name.split(".")[0] == "sentence_transformers":
            return importlib.machinery.ModuleSpec(name, loader=None)
        return real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(sys, "meta_path", [_TorchBrokenFinder(), *sys.meta_path])


class _FakeIndex:
    """A working semantic index: scores items by their order in the graph."""

    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def search(self, query: str, top_k: int = 10) -> list[EmbeddingHit]:
        n = len(self._ids)
        hits = [
            EmbeddingHit(item_id=i, score=(n - k) / n, item=None)
            for k, i in enumerate(self._ids)
        ]
        return hits[:top_k]


# ---------------------------------------------------------------------------
# 1. --json --verbose: stdout is pure JSON, the parse block is on stderr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("semantic_flag", [["--no-semantic"], []], ids=["graph", "hybrid"])
def test_json_verbose_stdout_is_pure_json(
    repo: Path, runner: CliRunner, semantic_flag: list[str]
) -> None:
    baseline = runner.invoke(main, ["query", "--json", *semantic_flag, SEMANTIC_PHRASE])
    assert baseline.exit_code == 0, baseline.output
    expected = {row["id"] for row in _json_rows(baseline.stdout)}
    assert expected == {"EXPR-001", "EXPR-002"}, baseline.stdout

    result = runner.invoke(
        main, ["query", "--json", "--verbose", *semantic_flag, SEMANTIC_PHRASE]
    )
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert {row["id"] for row in _json_rows(result.stdout)} == expected, result.stdout


@pytest.mark.parametrize("semantic_flag", [["--no-semantic"], []], ids=["graph", "hybrid"])
def test_json_verbose_parse_block_on_stderr(
    repo: Path, runner: CliRunner, semantic_flag: list[str]
) -> None:
    result = runner.invoke(main, ["query", "--json", "-v", *semantic_flag, SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert "Parsed query:" in result.stderr, f"stderr:\n{result.stderr}"
    assert 'Semantic: "done"' in result.stderr, f"stderr:\n{result.stderr}"
    assert "Parsed query:" not in result.stdout, result.stdout


def test_control_plain_verbose_still_shows_parse_block(
    repo: Path, runner: CliRunner
) -> None:
    """Without --json the block is still shown (stream not pinned) and so is the table."""
    result = runner.invoke(main, ["query", "-v", "--no-semantic", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert "Parsed query:" in result.output, result.output
    assert "Type: ['experiment']" in result.output, result.output
    assert _ids(result.stdout) == {"EXPR-001", "EXPR-002"}, result.stdout


# ---------------------------------------------------------------------------
# 2. Error paths under --json: stderr, stdout empty
# ---------------------------------------------------------------------------


def test_json_semantic_missing_extra_error_on_stderr(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--json", "--semantic", "latency"])
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    assert result.stdout == "", f"stdout not empty:\n{result.stdout!r}"
    assert _has_install_hint(result.stderr), f"stderr:\n{result.stderr!r}"


def test_json_semantic_broken_extra_error_on_stderr(
    broken_extra: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", "--json", "--semantic", "latency"])
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    assert result.stdout == "", f"stdout not empty:\n{result.stdout!r}"
    assert _has_install_hint(result.stderr), f"stderr:\n{result.stderr!r}"


def test_json_invalid_sparql_error_on_stderr(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--json", "--sparql", INVALID_SPARQL])
    _assert_clean(result)
    assert result.exit_code == 1, result.output  # today's exit code for a SPARQL error
    assert result.stdout == "", f"stdout not empty:\n{result.stdout!r}"
    assert "SPARQL error" in result.stderr, f"stderr:\n{result.stderr!r}"


def test_control_plain_semantic_missing_extra_error_shown(
    repo: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", "--semantic", "latency"])
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    assert _has_install_hint(result.output), result.output


def test_control_plain_invalid_sparql_error_shown(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--sparql", INVALID_SPARQL])
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    assert "SPARQL error" in result.output, result.output


# ---------------------------------------------------------------------------
# 3. _load_model keeps the original ImportError text
# ---------------------------------------------------------------------------


def test_premise_torch_broken_simulation(torch_broken: None) -> None:
    EmbeddingIndex()  # the up-front #346 probe passes
    with pytest.raises(ImportError, match=TORCH_ERROR):
        from sentence_transformers import SentenceTransformer  # noqa: F401


def test_load_model_keeps_original_import_error(torch_broken: None) -> None:
    emb = EmbeddingIndex()
    with pytest.raises(ImportError) as excinfo:
        emb._load_model()
    msg = str(excinfo.value)
    assert TORCH_ERROR in msg, msg
    assert "[search]" in msg, msg


def test_search_keeps_original_import_error(torch_broken: None) -> None:
    emb = EmbeddingIndex()  # no cache_dir: search() goes straight to _load_model
    with pytest.raises(ImportError) as excinfo:
        emb.search("latency")
    msg = str(excinfo.value)
    assert TORCH_ERROR in msg, msg
    assert "[search]" in msg, msg


# ---------------------------------------------------------------------------
# 4. Hybrid hint wording: "unavailable"
# ---------------------------------------------------------------------------


def _unavailable_hint_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines() if "unavailable" in line and _has_install_hint(line)
    ]


def test_hybrid_hint_says_unavailable_without_extras(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert len(_unavailable_hint_lines(result.stderr)) == 1, f"stderr:\n{result.stderr}"
    assert "not installed" not in result.stderr, result.stderr


def test_hybrid_hint_says_unavailable_with_broken_extra(
    broken_extra: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert len(_unavailable_hint_lines(result.stderr)) == 1, f"stderr:\n{result.stderr}"
    assert "not installed" not in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# 5. Graph-only fallback respects top_k
# ---------------------------------------------------------------------------


def test_premise_many_matching_items(many: Path) -> None:
    engine = QueryEngine.from_service(get_service(), enable_semantic=False)
    assert len(engine.structured_query(engine._decomposer.parse(MANY_PHRASE_HYBRID))) >= 5


def test_no_extras_fallback_respects_top_k(many: Path) -> None:
    engine = QueryEngine.from_service(get_service(), enable_semantic=True)
    assert not engine.semantic_enabled  # premise: no extras
    results = engine.query(MANY_PHRASE_HYBRID, top_k=2)
    assert len(results) <= 2, [r.item.id for r in results]


def test_structured_only_query_respects_top_k(many: Path) -> None:
    engine = QueryEngine.from_service(get_service(), enable_semantic=False)
    results = engine.query(MANY_PHRASE_STRUCTURED, top_k=2)
    assert len(results) <= 2, [r.item.id for r in results]


def test_broken_extra_fallback_respects_top_k(broken_extra: Path, many: Path) -> None:
    engine = QueryEngine.from_service(get_service(), enable_semantic=True)
    assert engine.semantic_enabled  # premise: the probe passed, the import will fail
    results = engine.query(MANY_PHRASE_HYBRID, top_k=2)
    assert not engine.semantic_enabled
    assert len(results) <= 2, [r.item.id for r in results]


def test_cli_top_respects_limit_without_extras(many: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--json", "--top", "2", MANY_PHRASE_HYBRID])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert len(_json_rows(result.stdout)) <= 2, result.stdout


# ---------------------------------------------------------------------------
# 6. Green controls
# ---------------------------------------------------------------------------


def test_control_hybrid_with_working_index_ranks_and_slices(many: Path) -> None:
    ug = UnifiedGraph.from_service(get_service())
    exp_ids = sorted(i for i in ug.items if i.startswith("EXPR-"))
    engine = QueryEngine(unified_graph=ug, embedding_index=_FakeIndex(exp_ids))  # type: ignore[arg-type]
    assert engine.semantic_enabled
    results = engine.query(MANY_PHRASE_HYBRID, top_k=2)
    assert [r.item.id for r in results] == exp_ids[:2]
    assert all(r.semantic_score > 0 for r in results)
    assert results[0].combined_score > results[1].combined_score


def test_control_plain_hybrid_output_unchanged_but_for_hint(
    repo: Path, runner: CliRunner
) -> None:
    """stdout (the table) is identical to --no-semantic; the hint is on stderr only."""
    baseline = runner.invoke(main, ["query", "--no-semantic", SEMANTIC_PHRASE])
    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert result.stdout == baseline.stdout, result.stdout
    assert len(_hint_lines(result.stderr)) == 1, result.stderr
    assert not _hint_lines(result.stdout), result.stdout
