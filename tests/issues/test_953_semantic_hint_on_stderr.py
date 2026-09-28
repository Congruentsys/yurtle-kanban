# ruff: noqa: F811  -- pytest fixtures imported from the #346 / #358 test modules are re-bound
"""Issue #953: plain `query --semantic` without a working search extra prints its
install hint on STDERR, stdout empty, so `query --semantic x > out.txt` still shows
the error. (#908's controls check the combined output only.)"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from tests.issues.test_346_semantic_extras_fallback import (  # noqa: F401 (fixtures)
    _assert_clean,
    no_extras,
    repo,
    runner,
)
from tests.issues.test_358_query_json_and_broken_extra import (  # noqa: F401 (fixtures)
    broken_extra,
)
from tests.issues.test_908_semantic_json_refusal import _has_install_hint
from yurtle_kanban.cli import main


def _assert_hint_on_stderr_only(result) -> None:
    _assert_clean(result)
    assert result.exit_code == 1, result.output
    assert _has_install_hint(result.stderr), f"stderr:\n{result.stderr!r}"
    assert result.stdout == "", f"stdout not empty:\n{result.stdout!r}"


def test_missing_extra_hint_is_on_stderr(repo: Path, runner: CliRunner) -> None:
    _assert_hint_on_stderr_only(runner.invoke(main, ["query", "--semantic", "latency"]))


def test_broken_extra_hint_is_on_stderr(broken_extra: Path, runner: CliRunner) -> None:
    _assert_hint_on_stderr_only(runner.invoke(main, ["query", "--semantic", "latency"]))
