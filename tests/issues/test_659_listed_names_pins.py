"""Issue #659 — pin the names a `move` refusal lists against the name `move` writes.

Decided ([steer] on #659, bucket 2):
1. `status_mappings` targets are folded to a canonical status FIRST, so `in-progress`
   and `in_progress` are the same target. Among the names that map to one status the
   LAST in YAML order wins: that is the name `move` writes, whatever spelling the
   target uses, and the name the refusal lists.
2. Pin more cases: the spec theme, and a custom theme with two names for one status.

Covered (helpers from test_643 / test_615):
(a) spec: the refusal list (CLI `move` and MCP `kanban_move_item`) is spec's native
    names, one per status, canonical order, derived from themes/spec.yaml.
(b) custom theme `doing: in_progress` then `wip: in_progress`: the list shows `wip`,
    `move X in_progress` writes `wip`, and the two agree.
(c) alias-spelt targets: `doing: in_progress` then `wip: in-progress` -> `wip`;
    `wip: in-progress` then `doing: in_progress` -> `doing`; a lone
    `wip: in-progress` -> `wip`. In every case the listed name is what `move` writes.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.issues._snapshot import glob_outside_git
from tests.issues.test_615_folded_status_keys import (
    LAYOUTS,
    MOVE,
    _repo,
)
from tests.issues.test_643_native_status_refusal import (
    CANONICAL_ORDER,
    THEMES_DIR,
    _clear_theme_cache,
    _listed,
    _mcp,
    _move,
    _ok,
    _run,
    buf,  # noqa: F401 (fixture)
    repo,  # noqa: F401 (fixture)
)
from yurtle_kanban.models import WorkItemStatus

BOGUS = "nosuchstatus"


def _expected(mappings: dict[str, str]) -> list[str]:
    """Each canonical status once, canonical order: the LAST name whose target folds
    to it, else the canonical name (the [steer] rule on #659)."""
    native: dict[str, str] = {}
    for name, target in mappings.items():
        native[WorkItemStatus.from_string(str(target)).value] = str(name)
    return [native.get(c, c) for c in CANONICAL_ORDER]


def _written(root: Path, item_id: str) -> str:
    files = list(glob_outside_git(root, f"{item_id}-*.md"))
    assert len(files) == 1, files
    front = files[0].read_text().split("---", 2)[1]
    return str(yaml.safe_load(front)["status"])


def _refusal_cli(b: io.StringIO, item_id: str) -> list[str]:
    result, text = _move(b, item_id, BOGUS)
    assert result.exit_code != 0, f"`move {item_id} {BOGUS}` accepted:\n{text}"
    return _listed(text)


def _refusal_mcp(root: Path, item_id: str) -> list[str]:
    result = _mcp(root, item_id, BOGUS)
    assert "error" in result and not result.get("success"), result
    return _listed(str(result["error"]))


def _create(b: io.StringIO, item_type: str) -> str:
    text = _ok(b, ["create", item_type, "an item"])
    match = re.search(r"Created (\S+):", text)
    assert match, text
    return match.group(1)


# ---------------------------------------------------------------------------
# (a) spec theme
# ---------------------------------------------------------------------------

SPEC_MAPPINGS: dict[str, str] = yaml.safe_load(
    (THEMES_DIR / "spec.yaml").read_text()
)["status_mappings"]


def test_a_spec_derivation_non_vacuous() -> None:
    expected = _expected(SPEC_MAPPINGS)
    assert expected != CANONICAL_ORDER
    assert len(set(expected)) == len(CANONICAL_ORDER)
    assert {"draft", "proposed", "implementing", "accepted"} <= set(expected)


@pytest.mark.parametrize("via", ["cli", "mcp"])
def test_a_spec_refusal_lists_native_names(
    repo: Path, buf: io.StringIO, via: str  # noqa: F811
) -> None:
    _ok(buf, ["init", "--theme", "spec"])
    item_id = _create(buf, "issue")  # a spec-theme type `create` knows (test_588)
    listed = _refusal_cli(buf, item_id) if via == "cli" else _refusal_mcp(repo, item_id)
    assert listed == _expected(SPEC_MAPPINGS), listed


@pytest.mark.parametrize("status", ["in_progress", "done", "backlog"])
def test_a_spec_listed_name_is_written(
    repo: Path, buf: io.StringIO, status: str  # noqa: F811
) -> None:
    _ok(buf, ["init", "--theme", "spec"])
    item_id = _create(buf, "issue")  # a spec-theme type `create` knows (test_588)
    listed = _refusal_cli(buf, item_id)
    if status == "backlog":  # leave backlog first so the move is a real write
        _ok(buf, ["move", item_id, "done", *MOVE])
    _ok(buf, ["move", item_id, status, *MOVE])
    assert _written(repo, item_id) == listed[CANONICAL_ORDER.index(status)]


# ---------------------------------------------------------------------------
# (b), (c) custom themes with two names for in_progress
# ---------------------------------------------------------------------------

BASE: dict[str, str] = {"todo": "backlog", "shipped": "done"}

CASES: dict[str, tuple[dict[str, str], str]] = {
    # (b) same spelling, two names: the last wins
    "b-same-target": ({**BASE, "doing": "in_progress", "wip": "in_progress"}, "wip"),
    # (c) the second target spelt with a dash: still the last wins
    "c-dash-last": ({**BASE, "doing": "in_progress", "wip": "in-progress"}, "wip"),
    # (c) reverse order: `doing` is last
    "c-dash-first": ({**BASE, "wip": "in-progress", "doing": "in_progress"}, "doing"),
    # (c) a single name whose target is spelt with a dash
    "c-dash-only": ({**BASE, "wip": "in-progress"}, "wip"),
}


def _theme(mappings: dict[str, str]) -> dict[str, Any]:
    return {
        "theme": {"name": "acme"},
        "item_types": {"task": {"id_prefix": "TASK", "path": "work/tasks/"}},
        "columns": {
            "todo": {"name": "Todo", "order": 1},
            "doing": {"name": "Doing", "order": 2},
            "wip": {"name": "WIP", "order": 3},
            "shipped": {"name": "Shipped", "order": 4},
        },
        "status_mappings": dict(mappings),
    }


@pytest.fixture
def custom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> tuple[Path, str]:
    case, layout = request.param
    root = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(CASES[case][0]))
    _clear_theme_cache()
    monkeypatch.chdir(root)
    return root, case


PARAMS = [(c, lay) for c in CASES for lay in LAYOUTS]
IDS = [f"{c}-{lay}" for c, lay in PARAMS]


@pytest.mark.parametrize("case", list(CASES))
def test_bc_fixture_keeps_order_and_rule(case: str) -> None:
    mappings, winner = CASES[case]
    dumped = yaml.safe_load(yaml.safe_dump(_theme(mappings), sort_keys=False))
    assert list(dumped["status_mappings"].items()) == list(mappings.items())
    assert _expected(mappings)[CANONICAL_ORDER.index("in_progress")] == winner


@pytest.mark.parametrize("custom", PARAMS, ids=IDS, indirect=True)
def test_bc_refusal_lists_winner(custom: tuple[Path, str], buf: io.StringIO) -> None:  # noqa: F811
    root, case = custom
    mappings, winner = CASES[case]
    item_id = _create(buf, "task")
    cli_list = _refusal_cli(buf, item_id)
    mcp_list = _refusal_mcp(root, item_id)
    assert cli_list == _expected(mappings), cli_list
    assert mcp_list == cli_list, (cli_list, mcp_list)
    assert winner in cli_list


@pytest.mark.parametrize("target", ["in_progress", "in-progress"])
@pytest.mark.parametrize("custom", PARAMS, ids=IDS, indirect=True)
def test_bc_move_writes_winner(
    custom: tuple[Path, str], buf: io.StringIO, target: str  # noqa: F811
) -> None:
    root, case = custom
    _, winner = CASES[case]
    item_id = _create(buf, "task")
    result, text = _run(buf, ["move", item_id, target, *MOVE])
    assert result.exit_code == 0, text
    assert _written(root, item_id) == winner


@pytest.mark.parametrize("custom", PARAMS, ids=IDS, indirect=True)
def test_bc_listed_equals_written(custom: tuple[Path, str], buf: io.StringIO) -> None:  # noqa: F811
    root, _ = custom
    item_id = _create(buf, "task")
    listed = _refusal_cli(buf, item_id)
    _ok(buf, ["move", item_id, "in_progress", *MOVE])
    assert _written(root, item_id) == listed[CANONICAL_ORDER.index("in_progress")], (
        listed,
        _written(root, item_id),
    )
