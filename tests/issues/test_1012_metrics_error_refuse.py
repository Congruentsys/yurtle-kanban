"""Issue #1012: a `get_flow_metrics` error is a refusal, JSON under `--json`.

The CLI finds the item first, so the service's `error` result can't be reached
today; if it ever is, `metrics --json` must still print one JSON object.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_905_json_unknown_items import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    board,
)
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "plain"])
def test_metrics_error_is_a_refusal(
    board: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    as_json: bool,
) -> None:
    monkeypatch.setattr(
        KanbanService, "get_flow_metrics", lambda self, item_id: {"error": "boom"}
    )
    args = ["metrics", "FEAT-001"] + (["--json"] if as_json else [])
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1, result.output
    if as_json:
        obj = json.loads(result.stdout)
        assert obj == {"success": False, "error": "boom"}, obj
    else:
        assert "Error: boom" in result.output, result.output
