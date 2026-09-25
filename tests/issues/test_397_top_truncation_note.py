# ruff: noqa: F811  -- pytest fixtures imported from the #346 / #371 / #377 / #387 test modules are re-bound
"""Issue #397 — `query` truncation to `--top` is silent (follow-up from PR #394 / #387).

Decided behaviour ([steer] on #397): in every `query` mode (NL, `--semantic`,
`--sparql`), with and without `--json`, when more than `--top` results exist
(N > K) the CLI prints exactly one note line on **stderr**, e.g. "showing the first
20 results; use --top for more". Matched loosely here: the line mentions "--top"
and the number K. stdout still carries exactly K rows; under `--json` stdout stays
pure JSON (#371). N <= K → no note; N == K + 1 → note (the boundary); default
`--top` (20) with N > 20 → note.

Green controls: row content and order are unchanged; empty results give no note.

The `lots` repo (#387) has 24 experiments + 1 hypothesis: NL "experiments" finds
N = 24, `--semantic` (fake #371 index over every item) and `--sparql` all-ids find
N = 25.
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
    _FakeIndex,
    many,
)
from tests.issues.test_377_top_intrange import (  # noqa: F401 (fixtures)
    SEMANTIC_TEXT,
    _json_rows,
    fake_semantic,
)
from tests.issues.test_387_sparql_json_top import (  # noqa: F401 (fixtures)
    SPARQL_ALL_IDS,
    SPARQL_NONE,
    lots,
)
from yurtle_kanban.cli import main
from yurtle_kanban.query import EmbeddingIndex

DEFAULT_TOP = 20
ID_RE = re.compile(r"\b(?:EXPR|H)-\d{3}\b")

# mode -> (CLI args after `query [--json] [--top K]`, N results in `lots`)
MODES: dict[str, tuple[list[str], int]] = {
    "nl": ([MANY_PHRASE_STRUCTURED], 24),
    "semantic": (["--semantic", SEMANTIC_TEXT], 25),
    "sparql": (["--sparql", SPARQL_ALL_IDS], 25),
}
EMPTY_MODES: dict[str, list[str]] = {
    "nl": ["blocked experiments"],
    "semantic": ["--semantic", SEMANTIC_TEXT],  # with the `empty_semantic` index
    "sparql": ["--sparql", SPARQL_NONE],
}
MODE_IDS = list(MODES)
JSON_FLAGS = pytest.mark.parametrize("as_json", [True, False], ids=["json", "table"])


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def modes(lots: Path, fake_semantic: None) -> Path:
    """`lots` with a working (fake) semantic index, so every mode can run."""
    return lots


@pytest.fixture
def empty_semantic(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--semantic` gets a working index that finds nothing."""

    def from_service(cls: type, service: Any, cache_dir: Path | None = None) -> _FakeIndex:
        return _FakeIndex([])

    monkeypatch.setattr(EmbeddingIndex, "from_service", classmethod(from_service))


def _run(runner: CliRunner, mode_args: list[str], as_json: bool, top: list[str]) -> Result:
    json_flag = ["--json"] if as_json else []
    result = runner.invoke(main, ["query", *json_flag, *top, *mode_args])
    _assert_clean(result)
    assert result.exit_code == 0, result.output
    return result


def _ids(result: Result, as_json: bool) -> list[str]:
    """The result ids on stdout, in order (JSON rows, or one id cell per table row)."""
    if as_json:
        return [str(row["id"]) for row in _json_rows(result.stdout)]
    ids: list[str] = []
    for line in result.stdout.splitlines():
        ids.extend(ID_RE.findall(line))
    return ids


def _note_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if "--top" in line]


def _assert_note(result: Result, k: int) -> None:
    notes = _note_lines(result.stderr)
    assert len(notes) == 1, f"want exactly one --top note on stderr, got:\n{result.stderr!r}"
    assert re.search(rf"\b{k}\b", notes[0]), f"note does not mention {k}: {notes[0]!r}"
    assert "--top" not in result.stdout, f"note leaked to stdout:\n{result.stdout}"


def _assert_no_note(result: Result) -> None:
    assert _note_lines(result.stderr) == [], f"unexpected --top note:\n{result.stderr!r}"
    assert "--top" not in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# 0. Premise: N really exceeds the K values used below
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODE_IDS)
def test_premise_result_counts(modes: Path, runner: CliRunner, mode: str) -> None:
    args, n = MODES[mode]
    result = _run(runner, args, True, ["--top", "1000"])
    assert len(_ids(result, True)) == n, result.stdout
    assert n > DEFAULT_TOP


# ---------------------------------------------------------------------------
# 1. N > K → one note on stderr, K rows on stdout (RED today: no note)
# ---------------------------------------------------------------------------


@JSON_FLAGS
@pytest.mark.parametrize("mode", MODE_IDS)
@pytest.mark.parametrize("k", [1, 3, 7], ids=["top1", "top3", "top7"])
def test_truncated_prints_note(
    modes: Path, runner: CliRunner, mode: str, as_json: bool, k: int
) -> None:
    args, _ = MODES[mode]
    result = _run(runner, args, as_json, ["--top", str(k)])
    assert len(_ids(result, as_json)) == k, result.stdout
    _assert_note(result, k)


@JSON_FLAGS
@pytest.mark.parametrize("mode", MODE_IDS)
def test_boundary_n_equals_k_plus_one_prints_note(
    modes: Path, runner: CliRunner, mode: str, as_json: bool
) -> None:
    args, n = MODES[mode]
    k = n - 1
    result = _run(runner, args, as_json, ["--top", str(k)])
    assert len(_ids(result, as_json)) == k, result.stdout
    _assert_note(result, k)


@JSON_FLAGS
@pytest.mark.parametrize("mode", MODE_IDS)
def test_default_top_truncated_prints_note(
    modes: Path, runner: CliRunner, mode: str, as_json: bool
) -> None:
    args, _ = MODES[mode]
    result = _run(runner, args, as_json, [])
    assert len(_ids(result, as_json)) == DEFAULT_TOP, result.stdout
    _assert_note(result, DEFAULT_TOP)


@pytest.mark.parametrize("mode", MODE_IDS)
def test_json_stdout_stays_pure_json_with_note(
    modes: Path, runner: CliRunner, mode: str
) -> None:
    args, _ = MODES[mode]
    result = _run(runner, args, True, ["--top", "5"])
    rows = _json_rows(result.stdout)  # json.loads on the whole stdout
    assert len(rows) == 5, result.stdout
    _assert_note(result, 5)


# ---------------------------------------------------------------------------
# 2. N <= K → no note (green today)
# ---------------------------------------------------------------------------


@JSON_FLAGS
@pytest.mark.parametrize("mode", MODE_IDS)
@pytest.mark.parametrize("extra", [0, 1, 975], ids=["k-eq-n", "k-eq-n+1", "large"])
def test_not_truncated_no_note(
    modes: Path, runner: CliRunner, mode: str, as_json: bool, extra: int
) -> None:
    args, n = MODES[mode]
    result = _run(runner, args, as_json, ["--top", str(n + extra)])
    assert len(_ids(result, as_json)) == n, result.stdout
    _assert_no_note(result)


# ---------------------------------------------------------------------------
# 3. Green controls: content/order unchanged, empty results give no note
# ---------------------------------------------------------------------------


@JSON_FLAGS
@pytest.mark.parametrize("mode", MODE_IDS)
@pytest.mark.parametrize("top", [["--top", "4"], []], ids=["top4", "default20"])
def test_control_rows_are_prefix_of_full_result(
    modes: Path, runner: CliRunner, mode: str, as_json: bool, top: list[str]
) -> None:
    args, _ = MODES[mode]
    full = _ids(_run(runner, args, as_json, ["--top", "1000"]), as_json)
    k = int(top[1]) if top else DEFAULT_TOP
    assert _ids(_run(runner, args, as_json, top), as_json) == full[:k]


@JSON_FLAGS
@pytest.mark.parametrize("mode", list(EMPTY_MODES))
@pytest.mark.parametrize("top", [["--top", "1"], []], ids=["top1", "default20"])
def test_control_empty_results_no_note(
    lots: Path,
    runner: CliRunner,
    empty_semantic: None,
    mode: str,
    as_json: bool,
    top: list[str],
) -> None:
    result = _run(runner, EMPTY_MODES[mode], as_json, top)
    assert _ids(result, as_json) == [], result.stdout
    _assert_no_note(result)
