# ruff: noqa: F811  -- the `graph` fixture imported from #577's module is re-bound as an arg
"""Issue #1066: MCP ``kanban_get_blocked`` returns what ``blocked --json`` returns.

Spec ([steer] on #1066): the tool returns exactly ``blocked --json``'s payload (#577):
status-blocked items plus ready/in_progress/review items with unmet dependencies,
each with its ``unmet`` tree, and never hdd ``abandoned``. It shares one service
function with the CLI, so the two can't drift.

Pinned here, on #577's fixture (``graph``):

- ``kanban_get_blocked`` with no arguments equals ``blocked --json`` (the whole
  payload, ``{"items": [...]}``, after the JSON round trip ``run_server`` does).
- The CLI's ``--board B`` and ``--all`` are the tool's ``board`` (string) and ``all``
  (boolean) arguments, each alone and together; neither is required.
- A non-boolean ``all`` is refused with the #719 boolean message.

Left open: the order of items (compared as the CLI emits them, since both come from
one function; a reorder in one is a reorder in both).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.issues.test_575_pickable_next import Repo
from tests.issues.test_577_blocked import DEFAULT_LISTED, NEVER_LISTED, graph, payload  # noqa: F401
from yurtle_kanban.mcp import server as mcp_server

pytestmark = pytest.mark.usefixtures("claim_env")

TOOL = "kanban_get_blocked"


def call(repo: Repo, args: dict[str, Any] | None = None) -> dict[str, Any]:
    out = mcp_server.KanbanMCPServer(repo_root=repo.root).handle_tool_call(TOOL, args or {})
    return json.loads(json.dumps(out))  # as run_server sends it


def test_schema_declares_board_and_all() -> None:
    tools = {t["name"]: t for t in mcp_server.KanbanMCPServer().get_tools()}
    schema = tools[TOOL]["inputSchema"]
    props = schema["properties"]
    assert props["board"]["type"] == "string", props
    assert props["all"]["type"] == "boolean", props
    assert not schema.get("required"), schema


def test_default_equals_cli_json(graph: Repo) -> None:
    got = call(graph)
    assert got == payload([]), got
    ids = {e["id"] for e in got["items"]}
    assert ids == DEFAULT_LISTED
    assert not ids & NEVER_LISTED  # incl. hdd abandoned H1.1 / H1.4


def test_dependency_blocked_ready_item_has_its_unmet_tree(graph: Repo) -> None:
    items = {e["id"]: e for e in call(graph)["items"]}
    exp1 = items["EXP-1"]
    assert exp1["status_blocked"] is False
    assert [n["id"] for n in exp1["unmet"]] == ["EXP-2"]
    assert [n["id"] for n in exp1["unmet"][0]["children"]] == ["EXP-3"]


@pytest.mark.parametrize(
    ("mcp_args", "cli_args"),
    [
        ({"all": True}, ["--all"]),
        ({"all": False}, []),
        ({"board": "research"}, ["--board", "research"]),
        ({"board": "development"}, ["--board", "development"]),
        ({"board": "development", "all": True}, ["--board", "development", "--all"]),
        ({"board": None, "all": None}, []),
    ],
)
def test_arguments_match_cli_options(
    graph: Repo, mcp_args: dict[str, Any], cli_args: list[str]
) -> None:
    assert call(graph, mcp_args) == payload(cli_args)


def test_all_adds_backlog_item(graph: Repo) -> None:
    assert "EXP-50" not in {e["id"] for e in call(graph)["items"]}
    assert "EXP-50" in {e["id"] for e in call(graph, {"all": True})["items"]}


@pytest.mark.parametrize("value", ["true", 1, [], {}])
def test_non_boolean_all_is_refused(graph: Repo, value: Any) -> None:
    assert call(graph, {"all": value}) == {
        "error": "all must be true or false (a JSON boolean)"
    }
