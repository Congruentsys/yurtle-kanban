# ruff: noqa: F811  -- pytest fixtures imported from the #346 / #371 test modules are re-bound
"""Issue #377 — follow-ups from the review of PR #374 (#371).

Decided behaviour:
1. `query --top 0 ...` and `query --top -3 ...` are refused up front: click's usage
   error (exit 2) naming `--top`, no query runs (``get_service`` is never called),
   no traceback. Covered for every mode `--top` applies to: the hybrid NL query
   (`engine.query(top_k=...)`), `--semantic` (`emb.search(top_k=...)`) and
   `--sparql` (table rows sliced by `[:top_k]`). The `-n` alias is refused too.
   `--top 1` and the default (20) are unchanged.
   Today: `--top 0` exits 0 with "No results.", `--top -3` drops the last 3 items.
2. `EmbeddingIndex._load_model` chains the cause (`raise ... from e`): the raised
   ImportError's `__cause__` is the original ImportError("torch broken XYZ").
   This pins #371's `from e` and is green today.
Green controls: `--top 2` limits results in every mode; `--top` accepts large values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from tests.issues.test_346_semantic_extras_fallback import (  # noqa: F401 (fixtures)
    _assert_clean,
    no_extras,
    repo,
    runner,
)
from tests.issues.test_371_query_stderr import (  # noqa: F401 (fixtures)
    MANY_PHRASE_HYBRID,
    MANY_PHRASE_STRUCTURED,
    TORCH_ERROR,
    _FakeIndex,
    many,
    torch_broken,
)
from yurtle_kanban import cli as cli_mod
from yurtle_kanban.cli import main
from yurtle_kanban.query import EmbeddingIndex

SPARQL_ALL_IDS = "SELECT ?id WHERE { ?item kb:id ?id . }"
SEMANTIC_TEXT = "caching"
N_ITEMS = 8  # `many`: seven experiments + one hypothesis
N_EXPERIMENTS = 7

# mode -> CLI args after `query --json --top <n>` (sparql is checked on the table,
# since `--top` only slices the table there)
MODE_ARGS: dict[str, list[str]] = {
    "nl": [MANY_PHRASE_STRUCTURED],
    "nl-hybrid": [MANY_PHRASE_HYBRID],
    "semantic": ["--semantic", SEMANTIC_TEXT],
    "sparql": ["--sparql", SPARQL_ALL_IDS],
}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def service_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every `get_service()` call made by the query command (= a query ran)."""
    calls: list[int] = []
    real = cli_mod.get_service

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(cli_mod, "get_service", spy)
    return calls


@pytest.fixture
def fake_semantic(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--semantic` gets a working index over every item id (no extras needed)."""

    def from_service(cls: type, service: Any, cache_dir: Path | None = None) -> _FakeIndex:
        return _FakeIndex(sorted(i.id for i in service.scan()))

    monkeypatch.setattr(EmbeddingIndex, "from_service", classmethod(from_service))


def _json_rows(stdout: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not valid JSON ({e}):\n{stdout!r}") from e
    assert isinstance(rows, list), stdout
    return rows


def _sparql_table_rows(stdout: str) -> int:
    """Count the data rows of the SPARQL results table (one id cell per row)."""
    return sum(1 for line in stdout.splitlines() if "EXPR-" in line or "H-" in line)


def _assert_usage_error(result: Result, calls: list[int]) -> None:
    _assert_clean(result)
    assert result.exit_code == 2, f"exit {result.exit_code}:\n{result.output}"
    assert "--top" in result.output, result.output
    assert calls == [], "a query ran before --top was validated"
    assert "No results." not in result.output, result.output


# ---------------------------------------------------------------------------
# 1. --top 0 / negative is refused up front (RED today)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "-3"], ids=["zero", "negative"])
@pytest.mark.parametrize("mode", list(MODE_ARGS), ids=list(MODE_ARGS))
def test_top_out_of_range_is_usage_error(
    many: Path,
    runner: CliRunner,
    fake_semantic: None,
    service_calls: list[int],
    mode: str,
    value: str,
) -> None:
    result = runner.invoke(main, ["query", "--json", "--top", value, *MODE_ARGS[mode]])
    _assert_usage_error(result, service_calls)


@pytest.mark.parametrize("value", ["0", "-3"], ids=["zero", "negative"])
def test_top_out_of_range_without_json_is_usage_error(
    many: Path, runner: CliRunner, service_calls: list[int], value: str
) -> None:
    result = runner.invoke(main, ["query", "--top", value, MANY_PHRASE_STRUCTURED])
    _assert_usage_error(result, service_calls)


@pytest.mark.parametrize("value", ["0", "-3"], ids=["zero", "negative"])
def test_short_alias_out_of_range_is_usage_error(
    many: Path, runner: CliRunner, service_calls: list[int], value: str
) -> None:
    result = runner.invoke(main, ["query", "--json", "-n", value, MANY_PHRASE_STRUCTURED])
    _assert_usage_error(result, service_calls)


def test_semantic_top_zero_refused_even_without_extras(
    many: Path, runner: CliRunner, service_calls: list[int]
) -> None:
    """Without the extras `--semantic` exits 1 today; `--top 0` is refused first (2)."""
    result = runner.invoke(main, ["query", "--json", "--top", "0", "--semantic", "x"])
    _assert_usage_error(result, service_calls)


# ---------------------------------------------------------------------------
# 2. _load_model chains the original ImportError (pin, green today)
# ---------------------------------------------------------------------------


def test_load_model_chains_original_import_error(torch_broken: None) -> None:
    emb = EmbeddingIndex()
    with pytest.raises(ImportError) as excinfo:
        emb._load_model()
    cause = excinfo.value.__cause__
    assert isinstance(cause, ImportError), repr(cause)
    assert TORCH_ERROR in str(cause), str(cause)
    assert cause is not excinfo.value


# ---------------------------------------------------------------------------
# 3. Green controls: valid --top values behave as before
# ---------------------------------------------------------------------------


def test_control_premise_many_items(many: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", "--json", MANY_PHRASE_STRUCTURED])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert len(_json_rows(result.stdout)) == N_EXPERIMENTS, result.stdout


@pytest.mark.parametrize(
    ("top", "expected"),
    [
        (["--top", "1"], 1),
        (["--top", "2"], 2),
        (["-n", "2"], 2),
        ([], N_EXPERIMENTS),
        (["--top", "1000"], N_EXPERIMENTS),
    ],
    ids=["top1", "top2", "n2", "default", "large"],
)
def test_control_nl_top_limits(
    many: Path, runner: CliRunner, top: list[str], expected: int
) -> None:
    result = runner.invoke(main, ["query", "--json", *top, MANY_PHRASE_STRUCTURED])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert len(_json_rows(result.stdout)) == expected, result.stdout


@pytest.mark.parametrize(
    ("top", "expected"),
    [(["--top", "1"], 1), (["--top", "2"], 2), ([], N_ITEMS), (["--top", "1000"], N_ITEMS)],
    ids=["top1", "top2", "default", "large"],
)
def test_control_semantic_top_limits(
    many: Path, runner: CliRunner, fake_semantic: None, top: list[str], expected: int
) -> None:
    result = runner.invoke(main, ["query", "--json", *top, "--semantic", SEMANTIC_TEXT])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert len(_json_rows(result.stdout)) == expected, result.stdout


@pytest.mark.parametrize(
    ("top", "expected"),
    [(["--top", "1"], 1), (["--top", "2"], 2), ([], N_ITEMS), (["--top", "1000"], N_ITEMS)],
    ids=["top1", "top2", "default", "large"],
)
def test_control_sparql_top_limits_table(
    many: Path, runner: CliRunner, top: list[str], expected: int
) -> None:
    result = runner.invoke(main, ["query", *top, "--sparql", SPARQL_ALL_IDS])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    assert _sparql_table_rows(result.stdout) == expected, result.stdout
