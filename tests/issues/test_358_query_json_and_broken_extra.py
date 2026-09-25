# ruff: noqa: F811  -- pytest fixtures imported from the #346 test module are re-bound as args
"""Issue #358 — query follow-ups from the review of PR #353 (#346).

1. A *partially broken* search extra: `find_spec` finds numpy / sentence_transformers,
   so `EmbeddingIndex`'s up-front probe passes, but actually importing them fails
   (torch missing, broken C-extension). Today the ImportError escapes from
   `search()` as a raw traceback.
   - NL query: exit 0, graph-only results, the one hint line, no traceback.
   - `query --semantic x`: exit 1, a one-line error with the install hint.
2. `query --json` with no results prints exactly `[]` on stdout, never "No results.".
3. The `--semantic` "extras missing" error stays on one line at an 80-column console.
Green controls: non-empty `--json` output and non-JSON "No results." are unchanged.

"Broken extra" is simulated by un-blocking the modules, making
`importlib.util.find_spec` return a spec for them (what `_importable` probes), and
putting a meta-path finder first that raises ImportError on the real import, so the
tests hold whether or not numpy is installed in the venv.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from rich.console import Console

from tests.issues.test_346_semantic_extras_fallback import (  # noqa: F401 (fixtures)
    SEMANTIC_PHRASE,
    _assert_clean,
    _hint_lines,
    _ids,
    no_extras,
    repo,
    runner,
)
from yurtle_kanban import cli as cli_mod
from yurtle_kanban.cli import main
from yurtle_kanban.query import EmbeddingIndex, _importable

EXTRAS = ("numpy", "sentence_transformers")
NO_MATCH_PHRASE = "experiments assigned to Nobody"
EMPTY_SPARQL = "SELECT ?s WHERE { ?s <urn:yk358:nothing> ?o }"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _BrokenExtraFinder:
    """Meta-path finder: `import numpy` / `import sentence_transformers` blow up."""

    @staticmethod
    def find_spec(name: str, path: Any = None, target: Any = None) -> None:
        if name.split(".")[0] in EXTRAS:
            raise ImportError(f"broken extra (#358): cannot import {name}")
        return None


@pytest.fixture
def broken_extra(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The 346 repo, but with the extras *found* by the probe and *failing* on import."""
    for name in list(sys.modules):
        if name.split(".")[0] in EXTRAS:
            monkeypatch.delitem(sys.modules, name)  # drop the `None` blocks from no_extras

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> Any:
        if name.split(".")[0] in EXTRAS:
            return importlib.machinery.ModuleSpec(name, loader=None)
        return real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(sys, "meta_path", [_BrokenExtraFinder(), *sys.meta_path])
    return repo


def _nonblank_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _has_install_hint(text: str) -> bool:
    return "[search]" in text or "sentence-transformers" in text.lower()


@pytest.fixture
def narrow_console(monkeypatch: pytest.MonkeyPatch) -> Console:
    """cli's module console at 80 columns, writing to whatever sys.stdout is now."""
    console = Console(width=80, color_system=None, force_terminal=False)
    monkeypatch.setattr(cli_mod, "console", console)
    return console


# ---------------------------------------------------------------------------
# 1. Partially broken extra
# ---------------------------------------------------------------------------


def test_premise_broken_extra_passes_probe_but_fails_import(broken_extra: Path) -> None:
    """Guards the simulation: the probe says yes, the import says no."""
    assert all(_importable(m) for m in EXTRAS)
    EmbeddingIndex()  # the up-front #346 probe does not raise
    with pytest.raises(ImportError):
        import numpy  # noqa: F401
    with pytest.raises(ImportError):
        from sentence_transformers import SentenceTransformer  # noqa: F401


def test_broken_extra_nl_query_does_not_crash(broken_extra: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output


def test_broken_extra_nl_query_returns_graph_only_results(
    broken_extra: Path, runner: CliRunner
) -> None:
    baseline = runner.invoke(main, ["query", "--no-semantic", SEMANTIC_PHRASE])
    assert baseline.exit_code == 0, baseline.output
    expected = _ids(baseline.output)
    assert expected == {"EXPR-001", "EXPR-002"}, baseline.output

    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert _ids(result.output) == expected, result.output


def test_broken_extra_nl_query_prints_one_install_hint(
    broken_extra: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", SEMANTIC_PHRASE])
    _assert_clean(result)
    hints = _hint_lines(result.output)
    assert len(hints) == 1, f"expected exactly one install hint line:\n{result.output}"


def test_broken_extra_nl_query_json_stdout_is_pure_json(
    broken_extra: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", "--json", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not valid JSON ({e}):\n{result.stdout}") from e
    assert {row["id"] for row in rows} == {"EXPR-001", "EXPR-002"}, result.stdout


def test_broken_extra_pure_semantic_errors_cleanly(
    broken_extra: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", "--semantic", "latency"])
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    lines = _nonblank_lines(result.output)
    assert len(lines) == 1, f"expected a one-line error:\n{result.output}"
    assert _has_install_hint(lines[0]), result.output


# ---------------------------------------------------------------------------
# 2. --json with no results → stdout is exactly []
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("semantic_flag", [["--no-semantic"], []], ids=["graph", "hybrid"])
def test_nl_json_no_results_is_empty_list(
    repo: Path, runner: CliRunner, semantic_flag: list[str]
) -> None:
    result = runner.invoke(main, ["query", "--json", *semantic_flag, NO_MATCH_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert "No results." not in result.stdout, result.stdout
    assert json.loads(result.stdout) == [], result.stdout


def test_sparql_json_no_results_is_empty_list(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--json", "--sparql", EMPTY_SPARQL])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert "No results." not in result.stdout, result.stdout
    assert json.loads(result.stdout) == [], result.stdout


class _EmptyIndex:
    def search(self, query: str, top_k: int = 10) -> list[Any]:
        return []


def test_semantic_json_no_results_is_empty_list(
    repo: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cli.py has no "No results." path for --semantic today; keep it that way."""
    monkeypatch.setattr(
        EmbeddingIndex, "from_service", classmethod(lambda cls, svc: _EmptyIndex())
    )
    result = runner.invoke(main, ["query", "--json", "--semantic", "zzz"])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert "No results." not in result.stdout, result.stdout
    assert json.loads(result.stdout) == [], result.stdout


# ---------------------------------------------------------------------------
# 3. --semantic error on one line at 80 columns
# ---------------------------------------------------------------------------


def test_semantic_missing_extras_error_is_one_line_at_80_cols(
    repo: Path, runner: CliRunner, narrow_console: Console
) -> None:
    result = runner.invoke(main, ["query", "--semantic", "latency"], env={"COLUMNS": "80"})
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    lines = _nonblank_lines(result.output)
    assert len(lines) == 1, f"error wrapped / spans lines at 80 cols:\n{result.output}"
    assert _has_install_hint(lines[0]), result.output
    assert len(lines[0]) > 80, "premise: the message is longer than the console"


# ---------------------------------------------------------------------------
# 4. Green controls
# ---------------------------------------------------------------------------


def test_control_nl_json_with_results_unchanged(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--json", "--no-semantic", SEMANTIC_PHRASE])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {row["id"] for row in rows} == {"EXPR-001", "EXPR-002"}, result.stdout
    assert set(rows[0]) == {
        "id", "title", "status", "priority", "semantic_score", "combined_score"
    }, rows[0]


def test_control_sparql_json_with_results_unchanged(repo: Path, runner: CliRunner) -> None:
    q = "PREFIX kb: <https://yurtle.dev/kanban/> SELECT ?id WHERE { ?i kb:id ?id }"
    result = runner.invoke(main, ["query", "--json", "--sparql", q])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert isinstance(rows, list) and rows, result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["query", "--no-semantic", NO_MATCH_PHRASE],
        ["query", NO_MATCH_PHRASE],
        ["query", "--sparql", EMPTY_SPARQL],
    ],
    ids=["nl-graph", "nl-hybrid", "sparql"],
)
def test_control_plain_no_results_message_unchanged(
    repo: Path, runner: CliRunner, args: list[str]
) -> None:
    result = runner.invoke(main, args)
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert "No results." in result.stdout, result.output
