"""Issue #905 — under ``--json``, an unknown item is a JSON refusal, not text or ``[]``.

Found by #877's test partner: ``metrics FEAT-999 --json`` prints plain text
("No status history found") and exits 0; ``experiment status EXPR-999 --json``
prints ``[]`` and exits 0.

Decided ([steer] on #905, bucket 1, under #877's contract):
- ``metrics <unknown> --json`` and ``experiment status <unknown> --json`` refuse:
  exit 1, stdout exactly one JSON object ``{"success": false, "error": "… not
  found …"}`` (via ``json_refusal``). Without ``--json`` they exit 1 with a plain
  not-found message.
- ``metrics <known item with no history> --json`` prints the command's normal
  JSON shape (the keys of a with-history item's output), empty metrics, exit 0.

An "unknown" experiment here has neither an item on the board nor a runs folder.

Controls: a known item with history (``metrics``), a known experiment with and
without runs (``experiment status``), with and without ``--json``, succeed as today.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

SOFTWARE_CONFIG = """\
kanban:
  theme: software
  paths:
    root: kanban-work/
    scan_paths:
    - "kanban-work/"
    ignore:
      - "**/_TEMPLATE*"
"""

HDD_CONFIG = """\
kanban:
  theme: hdd
  paths:
    root: research/
    scan_paths:
    - "research/experiments/"
"""

FEAT_NO_HISTORY = """\
---
id: FEAT-001
title: "Hello"
type: feature
status: backlog
priority: medium
created: 2026-01-01
---

# FEAT-001: Hello
"""

FEAT_WITH_HISTORY = """\
---
id: FEAT-002
title: "Moved"
type: feature
status: done
priority: medium
created: 2026-01-01
---

# FEAT-002: Moved

```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<> kb:statusChange [
    kb:status kb:ready ;
    kb:at "2026-01-01T10:00:00"^^xsd:dateTime ;
    kb:by "t" ;
] .
<> kb:statusChange [
    kb:status kb:in_progress ;
    kb:at "2026-01-02T10:00:00"^^xsd:dateTime ;
    kb:by "t" ;
] .
<> kb:statusChange [
    kb:status kb:done ;
    kb:at "2026-01-03T10:00:00"^^xsd:dateTime ;
    kb:by "t" ;
] .
```
"""


def _experiment(expr_id: str) -> str:
    return (
        f'---\nid: {expr_id}\ntitle: "Known Experiment"\ntype: experiment\n'
        f"status: draft\ncreated: 2026-01-01\npriority: medium\ntags: []\n---\n\n"
        f"# {expr_id}: Known Experiment\n"
    )


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A software board: FEAT-001 (no status history), FEAT-002 (history); the cwd."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    repo = tmp_path / "repo"
    (repo / ".kanban").mkdir(parents=True)
    (repo / ".kanban" / "config.yaml").write_text(SOFTWARE_CONFIG)
    features = repo / "kanban-work" / "features"
    features.mkdir(parents=True)
    (features / "FEAT-001-hello.md").write_text(FEAT_NO_HISTORY)
    (features / "FEAT-002-moved.md").write_text(FEAT_WITH_HISTORY)
    _init_repo(repo)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def hdd_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An HDD board: EXPR-001 (no runs) and EXPR-002 (one run); the cwd."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    repo = tmp_path / "repo"
    (repo / ".kanban").mkdir(parents=True)
    (repo / ".kanban" / "config.yaml").write_text(HDD_CONFIG)
    experiments = repo / "research" / "experiments"
    experiments.mkdir(parents=True)
    (experiments / "EXPR-001-known.md").write_text(_experiment("EXPR-001"))
    (experiments / "EXPR-002-known.md").write_text(_experiment("EXPR-002"))
    run_dir = repo / "research" / "runs" / "EXPR-002" / "2026-01-01T10-00-00_being-v1"
    run_dir.mkdir(parents=True)
    (run_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment": "EXPR-002",
                "being": "being-v1",
                "status": "complete",
                "created": "2026-01-01T10:00:00",
            }
        )
    )
    _init_repo(repo)
    monkeypatch.chdir(repo)
    return repo


def _run(args: list[str]) -> Result:
    return CliRunner().invoke(main, args)


def _shown(result: Result) -> str:
    return (
        f"exit {result.exit_code}\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n--- exception ---\n{result.exception!r}"
    )


def _assert_json_refusal(result: Result) -> dict[str, Any]:
    """Exit 1; stdout is exactly one JSON object, success false, error says not found."""
    shown = _shown(result)
    assert result.exit_code == 1, shown
    try:
        obj = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"--json refusal: stdout is not one JSON object ({e})\n{shown}")
    assert isinstance(obj, dict), shown
    assert obj.get("success") is False, shown
    error = obj.get("error")
    assert isinstance(error, str) and "not found" in error.lower(), shown
    return obj


def _assert_plain_not_found(result: Result) -> None:
    """Exit 1 with a plain (non-JSON) not-found message."""
    shown = _shown(result)
    assert result.exit_code == 1, shown
    assert "not found" in result.output.lower(), shown
    try:
        json.loads(result.stdout)
    except json.JSONDecodeError:
        pass
    else:
        pytest.fail(f"without --json the refusal must not be JSON\n{shown}")


def _json_ok(result: Result) -> Any:
    shown = _shown(result)
    assert result.exit_code == 0, shown
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"--json success: stdout is not JSON ({e})\n{shown}")


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


class TestMetricsUnknownItem:
    def test_unknown_item_json_is_refusal(self, board: Path) -> None:
        """Red: today plain 'No status history found', exit 0."""
        obj = _assert_json_refusal(_run(["metrics", "FEAT-999", "--json"]))
        assert "FEAT-999" in obj["error"], obj

    def test_unknown_item_plain_exits_1_not_found(self, board: Path) -> None:
        """Red: today 'No status history found', exit 0."""
        _assert_plain_not_found(_run(["metrics", "FEAT-999"]))

    def test_unknown_item_lowercase_id_json_is_refusal(self, board: Path) -> None:
        """The id is folded (``fold_id``) before lookup: same refusal."""
        _assert_json_refusal(_run(["metrics", "feat-999", "--json"]))


class TestMetricsKnownItemNoHistory:
    def test_no_history_json_is_normal_shape(self, board: Path) -> None:
        """Red: today plain 'No status history found' on stdout (not JSON).

        The normal shape is the with-history item's keys (the success path)."""
        normal = _json_ok(_run(["metrics", "FEAT-002", "--json"]))
        assert isinstance(normal, dict), normal

        result = _run(["metrics", "FEAT-001", "--json"])
        obj = _json_ok(result)
        shown = _shown(result)
        assert isinstance(obj, dict), shown
        assert set(obj) == set(normal), (sorted(obj), sorted(normal), shown)
        assert obj["item_id"] == "FEAT-001", shown
        assert obj["transitions"] == 0, shown
        assert obj["time_in_status"] == {}, shown
        assert obj["cycle_time_hours"] is None, shown
        assert obj["lead_time_hours"] is None, shown
        assert "error" not in obj and obj.get("success") is not False, shown


class TestMetricsControls:
    def test_known_item_with_history_json(self, board: Path) -> None:
        obj = _json_ok(_run(["metrics", "FEAT-002", "--json"]))
        assert obj["item_id"] == "FEAT-002", obj
        assert obj["transitions"] == 3, obj
        assert obj["cycle_time_hours"] == pytest.approx(24.0), obj
        assert obj["lead_time_hours"] == pytest.approx(48.0), obj

    def test_known_item_with_history_plain(self, board: Path) -> None:
        result = _run(["metrics", "FEAT-002"])
        assert result.exit_code == 0, _shown(result)
        assert "Flow Metrics: FEAT-002" in result.output, _shown(result)
        assert "Transitions: 3" in result.output, _shown(result)

    def test_known_item_no_history_plain_exits_0(self, board: Path) -> None:
        """A known item without history is not a refusal (exit 0) without --json."""
        result = _run(["metrics", "FEAT-001"])
        assert result.exit_code == 0, _shown(result)

    def test_board_metrics_json(self, board: Path) -> None:
        obj = _json_ok(_run(["metrics", "--json"]))
        assert obj["total_items"] == 2, obj


# ---------------------------------------------------------------------------
# experiment status
# ---------------------------------------------------------------------------


class TestExperimentStatusUnknown:
    def test_unknown_experiment_json_is_refusal(self, hdd_board: Path) -> None:
        """Red: today prints `[]`, exit 0."""
        obj = _assert_json_refusal(_run(["experiment", "status", "EXPR-999", "--json"]))
        assert "EXPR-999" in obj["error"], obj

    def test_unknown_experiment_unprefixed_json_is_refusal(self, hdd_board: Path) -> None:
        """`999` is normalized to EXPR-999: same refusal."""
        _assert_json_refusal(_run(["experiment", "status", "999", "--json"]))

    def test_unknown_experiment_plain_exits_1_not_found(self, hdd_board: Path) -> None:
        """Red: today 'EXPR-999: EXPR-999 / No runs found.', exit 0."""
        _assert_plain_not_found(_run(["experiment", "status", "EXPR-999"]))


class TestExperimentStatusControls:
    def test_known_no_runs_json_is_empty_list(self, hdd_board: Path) -> None:
        assert _json_ok(_run(["experiment", "status", "EXPR-001", "--json"])) == []

    def test_known_no_runs_plain(self, hdd_board: Path) -> None:
        result = _run(["experiment", "status", "EXPR-001"])
        assert result.exit_code == 0, _shown(result)
        assert "No runs found" in result.output, _shown(result)

    def test_known_with_runs_json(self, hdd_board: Path) -> None:
        runs = _json_ok(_run(["experiment", "status", "EXPR-002", "--json"]))
        assert isinstance(runs, list) and len(runs) == 1, runs
        assert runs[0]["being"] == "being-v1", runs
        assert runs[0]["status"] == "complete", runs

    def test_known_with_runs_plain(self, hdd_board: Path) -> None:
        result = _run(["experiment", "status", "EXPR-002"])
        assert result.exit_code == 0, _shown(result)
        assert "being-v1" in result.output, _shown(result)
