"""Issue #346 — without the "search" extras, semantic queries must degrade, not crash.

`EmbeddingIndex` imports numpy / sentence-transformers lazily inside `search()`, so
`QueryEngine.from_service(..., enable_semantic=True)`'s `except ImportError` never
fires and the CLI's graph-only fallback never runs: an NL query with a semantic
part ends in `ModuleNotFoundError: No module named 'numpy'`.

Decided behaviour (the "search" extra = numpy + sentence-transformers):
1. `query "<phrase with a semantic part>"` without the extras exits 0, shows no
   traceback, returns the graph-only results (same as `--no-semantic`), and prints
   one dim hint line that semantic search is off / how to install it.
2. `query --semantic "x"` without the extras exits 1 with a one-line error naming
   the install hint, no traceback.
3. `QueryEngine.from_service(service, enable_semantic=True)` without the extras
   yields an engine whose `query()` returns graph-only results and doesn't raise.
Green controls: `--no-semantic` and a purely structured query are unchanged.

"Extras missing" is simulated via `sys.modules[...] = None`, so these tests hold
even in a venv that has numpy installed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban.cli import get_service, main
from yurtle_kanban.query import QueryEngine

# "all" and "done" are not structured filters here: -v shows `Semantic: "done"`.
SEMANTIC_PHRASE = "all done experiments assigned to Mini"
STRUCTURED_PHRASE = "experiments"
ID_RE = re.compile(r"\b(?:EXPR|H)-\d{3}\b")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _clear_theme_cache() -> None:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()


@pytest.fixture
def no_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `import numpy` / `import sentence_transformers` raise ImportError."""
    for name in list(sys.modules):
        if name.split(".")[0] in ("numpy", "sentence_transformers"):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "numpy", None)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: CliRunner, no_extras: None
) -> Path:
    """A scratch hdd repo (cwd) with two experiments for Mini and one hypothesis."""
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=tmp_path, capture_output=True, check=True)
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["init", "--theme", "hdd"])
    assert result.exit_code == 0, result.output
    for args in (
        ["create", "experiment", "Brain latency study", "--assignee", "Mini"],
        ["create", "experiment", "Memory recall probe", "--assignee", "Mini"],
        ["create", "experiment", "Someone else's run", "--assignee", "DGX"],
        ["create", "hypothesis", "Latency drops with caching", "--assignee", "Mini"],
    ):
        result = runner.invoke(main, args)
        assert result.exit_code == 0, result.output
    return tmp_path


def _assert_clean(result: Result) -> None:
    """No crash: no traceback in the output, no non-SystemExit exception."""
    assert "Traceback" not in result.output, result.output
    assert "ModuleNotFoundError" not in result.output, result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception


def _ids(output: str) -> set[str]:
    return set(ID_RE.findall(output))


def _hint_lines(output: str) -> list[str]:
    return [
        line
        for line in output.splitlines()
        if "semantic" in line.lower()
        and ("[search]" in line or "sentence-transformers" in line.lower())
    ]


# ---------------------------------------------------------------------------
# 1. Hybrid NL query without the extras → graph-only + one hint line
# ---------------------------------------------------------------------------


def test_nl_query_without_extras_does_not_crash(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output


def test_nl_query_without_extras_returns_graph_only_results(
    repo: Path, runner: CliRunner
) -> None:
    baseline = runner.invoke(main, ["query", "--no-semantic", SEMANTIC_PHRASE])
    assert baseline.exit_code == 0, baseline.output
    expected = _ids(baseline.output)
    assert expected == {"EXPR-001", "EXPR-002"}, baseline.output

    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert _ids(result.output) == expected, result.output


def test_nl_query_without_extras_prints_one_install_hint(
    repo: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    hints = _hint_lines(result.output)
    assert len(hints) == 1, f"expected exactly one install hint line:\n{result.output}"


def test_nl_query_json_without_extras_does_not_crash(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--json", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert _ids(result.output) == {"EXPR-001", "EXPR-002"}, result.output


# ---------------------------------------------------------------------------
# 2. Pure --semantic without the extras → exit 1, one-line error, no traceback
# ---------------------------------------------------------------------------


def test_pure_semantic_without_extras_errors_cleanly(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--semantic", "latency"])
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    out = result.output
    assert "[search]" in out or "sentence-transformers" in out.lower(), out


# ---------------------------------------------------------------------------
# 3. Library API: QueryEngine.from_service(enable_semantic=True) degrades
# ---------------------------------------------------------------------------


def test_engine_from_service_without_extras_is_graph_only(repo: Path) -> None:
    service = get_service()
    graph_only = QueryEngine.from_service(service, enable_semantic=False)
    expected = {r.item.id for r in graph_only.query(SEMANTIC_PHRASE)}
    assert expected == {"EXPR-001", "EXPR-002"}

    engine = QueryEngine.from_service(service, enable_semantic=True)
    results = engine.query(SEMANTIC_PHRASE)  # must not raise ModuleNotFoundError
    assert {r.item.id for r in results} == expected


# ---------------------------------------------------------------------------
# Green controls
# ---------------------------------------------------------------------------


def test_control_no_semantic_unchanged(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--no-semantic", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert _ids(result.output) == {"EXPR-001", "EXPR-002"}, result.output
    assert not _hint_lines(result.output), result.output


def test_control_structured_query_unchanged(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", STRUCTURED_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert _ids(result.output) == {"EXPR-001", "EXPR-002", "EXPR-003"}, result.output


def test_control_verbose_shows_semantic_part(repo: Path, runner: CliRunner) -> None:
    """Guards the premise: the phrase really has a semantic part."""
    result = runner.invoke(main, ["query", "-v", "--no-semantic", SEMANTIC_PHRASE])
    assert result.exit_code == 0, result.output
    assert "Semantic:" in result.output, result.output
