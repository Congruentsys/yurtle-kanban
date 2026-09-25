# ruff: noqa: F811  -- pytest fixtures imported from the #346 / #371 / #377 test modules are re-bound
"""Issue #387 — `query --sparql ... --json` ignores `--top` (follow-up from PR #386 / #377).

Decided behaviour: `query --sparql "<SELECT returning N rows>" --json --top K` prints
exactly `min(N, K)` rows as a JSON list — the same first K rows the table shows, in
the same order. Without `--top` the default (20) applies, as for the table.
Today the JSON path dumps all N rows.

The repo here has N = 25 items (> 20 and > every K used), so both the explicit and
the default limit are observable.

Green controls: the SPARQL table is unchanged; NL and `--semantic` `--json` already
honour `--top`; an empty SPARQL result under `--json` is `[]`.
"""

from __future__ import annotations

import re
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
    MANY_PHRASE_STRUCTURED,
    many,
)
from tests.issues.test_377_top_intrange import (  # noqa: F401 (fixtures)
    SEMANTIC_TEXT,
    _json_rows,
    fake_semantic,
)
from yurtle_kanban.cli import main

DEFAULT_TOP = 20
N_EXPERIMENTS = 24
N_ITEMS = 25  # 24 experiments + 1 hypothesis

SPARQL_ALL_IDS = "SELECT ?id WHERE { ?item kb:id ?id . }"
SPARQL_IDS_DESC = "SELECT ?id WHERE { ?item kb:id ?id . } ORDER BY DESC(?id)"
SPARQL_NONE = 'SELECT ?id WHERE { ?item kb:id ?id . FILTER(?id = "NOPE-999") }'

ID_RE = re.compile(r"\b(?:EXPR|H)-\d{3}\b")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def lots(many: Path, runner: CliRunner) -> Path:
    """The #371 `many` repo plus 17 experiments: 24 experiments + 1 hypothesis = 25."""
    for k in range(N_EXPERIMENTS - 7):
        result = runner.invoke(
            main, ["create", "experiment", f"Bulk experiment {k:02d}", "--assignee", "Mini"]
        )
        assert result.exit_code == 0, result.output
    return many


def _ok(result: Result) -> None:
    _assert_clean(result)
    assert result.exit_code == 0, result.output


def _json_ids(stdout: str) -> list[str]:
    return [str(row["id"]) for row in _json_rows(stdout)]


def _table_ids(stdout: str) -> list[str]:
    """Ids of the SPARQL results table, in row order (one id cell per row)."""
    ids: list[str] = []
    for line in stdout.splitlines():
        ids.extend(ID_RE.findall(line))
    return ids


def _sparql(runner: CliRunner, *args: str, query: str = SPARQL_ALL_IDS) -> Result:
    return runner.invoke(main, ["query", *args, "--sparql", query])


# ---------------------------------------------------------------------------
# 0. Premise: the SPARQL query really returns N > 20 rows
# ---------------------------------------------------------------------------


def test_premise_sparql_returns_more_than_default(lots: Path, runner: CliRunner) -> None:
    """With `--top` above N every row is printed, in both outputs: N = 25 > 20."""
    as_json = _sparql(runner, "--json", "--top", "1000")
    _ok(as_json)
    assert len(_json_rows(as_json.stdout)) == N_ITEMS, as_json.stdout
    table = _sparql(runner, "--top", "1000")
    _ok(table)
    assert len(_table_ids(table.stdout)) == N_ITEMS, table.stdout


# ---------------------------------------------------------------------------
# 1. --json honours --top (RED today: JSON returns all N rows)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("top", "expected"),
    [
        (["--top", "1"], 1),
        (["--top", "2"], 2),
        (["-n", "2"], 2),
        (["--top", "7"], 7),
        ([], DEFAULT_TOP),
    ],
    ids=["top1", "top2", "n2", "top7", "default20"],
)
def test_sparql_json_honours_top(
    lots: Path, runner: CliRunner, top: list[str], expected: int
) -> None:
    result = _sparql(runner, "--json", *top)
    _ok(result)
    rows = _json_rows(result.stdout)
    assert len(rows) == expected, f"{len(rows)} rows, want {expected}:\n{result.stdout}"


@pytest.mark.parametrize(
    "top",
    [["--top", "3"], ["--top", "10"], []],
    ids=["top3", "top10", "default20"],
)
@pytest.mark.parametrize(
    "query", [SPARQL_ALL_IDS, SPARQL_IDS_DESC], ids=["unordered", "order-desc"]
)
def test_sparql_json_rows_match_table_rows_in_order(
    lots: Path, runner: CliRunner, top: list[str], query: str
) -> None:
    """The JSON rows are the table's rows: same ids, same order."""
    table = _sparql(runner, *top, query=query)
    _ok(table)
    as_json = _sparql(runner, "--json", *top, query=query)
    _ok(as_json)
    assert _json_ids(as_json.stdout) == _table_ids(table.stdout), (
        f"json:\n{as_json.stdout}\ntable:\n{table.stdout}"
    )


def test_sparql_json_top_is_prefix_of_full_result(lots: Path, runner: CliRunner) -> None:
    """`--top K` keeps the first K rows of the query's own (ORDER BY) order."""
    full = _sparql(runner, "--json", "--top", "1000", query=SPARQL_IDS_DESC)
    _ok(full)
    full_ids = _json_ids(full.stdout)
    assert full_ids == sorted(full_ids, reverse=True), full.stdout
    top = _sparql(runner, "--json", "--top", "4", query=SPARQL_IDS_DESC)
    _ok(top)
    assert _json_ids(top.stdout) == full_ids[:4], top.stdout


# ---------------------------------------------------------------------------
# 2. Green controls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("top", "expected"),
    [
        (["--top", "1"], 1),
        (["--top", "2"], 2),
        (["-n", "7"], 7),
        ([], DEFAULT_TOP),
        (["--top", "1000"], N_ITEMS),
    ],
    ids=["top1", "top2", "n7", "default20", "large"],
)
def test_control_sparql_table_unchanged(
    lots: Path, runner: CliRunner, top: list[str], expected: int
) -> None:
    result = _sparql(runner, *top)
    _ok(result)
    assert "SPARQL" in result.stdout, result.stdout
    assert len(_table_ids(result.stdout)) == expected, result.stdout


def test_control_sparql_table_order_desc(lots: Path, runner: CliRunner) -> None:
    result = _sparql(runner, "--top", "5", query=SPARQL_IDS_DESC)
    _ok(result)
    ids = _table_ids(result.stdout)
    assert len(ids) == 5, result.stdout
    assert ids == sorted(ids, reverse=True), result.stdout


@pytest.mark.parametrize(
    ("top", "expected"),
    [(["--top", "2"], 2), (["--top", "7"], 7), ([], DEFAULT_TOP)],
    ids=["top2", "top7", "default20"],
)
def test_control_nl_json_honours_top(
    lots: Path, runner: CliRunner, top: list[str], expected: int
) -> None:
    result = runner.invoke(main, ["query", "--json", *top, MANY_PHRASE_STRUCTURED])
    _ok(result)
    assert len(_json_rows(result.stdout)) == expected, result.stdout


@pytest.mark.parametrize(
    ("top", "expected"),
    [(["--top", "2"], 2), (["--top", "7"], 7), ([], DEFAULT_TOP)],
    ids=["top2", "top7", "default20"],
)
def test_control_semantic_json_honours_top(
    lots: Path, runner: CliRunner, fake_semantic: None, top: list[str], expected: int
) -> None:
    result = runner.invoke(main, ["query", "--json", *top, "--semantic", SEMANTIC_TEXT])
    _ok(result)
    assert len(_json_rows(result.stdout)) == expected, result.stdout


@pytest.mark.parametrize("top", [["--top", "2"], []], ids=["top2", "default"])
def test_control_sparql_json_empty_is_empty_list(
    lots: Path, runner: CliRunner, top: list[str]
) -> None:
    result = _sparql(runner, "--json", *top, query=SPARQL_NONE)
    _ok(result)
    rows: Any = _json_rows(result.stdout)
    assert rows == [], result.stdout


def test_control_sparql_table_empty_says_no_results(lots: Path, runner: CliRunner) -> None:
    result = _sparql(runner, query=SPARQL_NONE)
    _ok(result)
    assert "No results." in result.stdout, result.stdout
