# ruff: noqa: F811  -- pytest fixtures imported from the #346 / #358 / #371 test modules are re-bound
"""Issue #908 — `query --json --semantic` with the search extra missing or broken
prints its install hint as a `--json` refusal on stdout (#877's convention).

Today the hint goes to stderr with stdout empty, so a script parsing stdout gets
nothing (pinned by #371's two `..._error_on_stderr` tests, which this changes).

Decided behaviour (#877 / PR #927, `json_refusal` in `_click.py`):
1. `query --json --semantic x` without the search extra: exit 1, stdout is exactly
   one JSON object `{"success": false, "error": <install hint>}` and nothing else.
2. The same with the extra installed but broken (its import raises ImportError),
   including a broken torch underneath: the error keeps the original text (#371).
Green control: without `--json` the hint is still shown and the exit code stays 1.

click 8.5: `result.stdout` / `result.stderr` are separate, `result.output` is both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner, Result

from tests.issues.test_346_semantic_extras_fallback import (  # noqa: F401 (fixtures)
    _assert_clean,
    no_extras,
    repo,
    runner,
)
from tests.issues.test_358_query_json_and_broken_extra import (  # noqa: F401 (fixtures)
    broken_extra,
)
from tests.issues.test_371_query_stderr import (  # noqa: F401 (fixtures)
    TORCH_ERROR,
    torch_broken,
)
from yurtle_kanban.cli import main

ARGS = ["query", "--json", "--semantic", "latency"]


def _has_install_hint(text: str) -> bool:
    return "[search]" in text or "sentence-transformers" in text.lower()


def _refusal(result: Result) -> dict[str, Any]:
    """stdout is exactly one JSON object `{"success": false, "error": <str>}`."""
    try:
        obj = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(
            f"stdout is not one JSON object ({e}):\nstdout={result.stdout!r}\n"
            f"stderr={result.stderr!r}"
        ) from e
    assert isinstance(obj, dict), result.stdout
    assert obj.get("success") is False, obj
    assert isinstance(obj.get("error"), str), obj
    return obj


# ---------------------------------------------------------------------------
# 1. Extra missing
# ---------------------------------------------------------------------------


def test_json_semantic_missing_extra_is_json_refusal(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ARGS)
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    obj = _refusal(result)
    assert _has_install_hint(obj["error"]), obj


# ---------------------------------------------------------------------------
# 2. Extra installed but broken
# ---------------------------------------------------------------------------


def test_json_semantic_broken_extra_is_json_refusal(
    broken_extra: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ARGS)
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    obj = _refusal(result)
    assert _has_install_hint(obj["error"]), obj


def test_json_semantic_torch_broken_keeps_original_error(
    repo: Path, torch_broken: None, runner: CliRunner
) -> None:
    result = runner.invoke(main, ARGS)
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    obj = _refusal(result)
    assert TORCH_ERROR in obj["error"], obj
    assert "[search]" in obj["error"], obj


# ---------------------------------------------------------------------------
# Green controls: without --json the hint is still shown, exit 1
# ---------------------------------------------------------------------------


def test_control_plain_semantic_missing_extra_hint_shown(
    repo: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", "--semantic", "latency"])
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    assert _has_install_hint(result.output), result.output
    assert '"success"' not in result.output, result.output


def test_control_plain_semantic_broken_extra_hint_shown(
    broken_extra: Path, runner: CliRunner
) -> None:
    result = runner.invoke(main, ["query", "--semantic", "latency"])
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    assert _has_install_hint(result.output), result.output
    assert '"success"' not in result.output, result.output
