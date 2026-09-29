# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#801: a wrong-JSON-type MCP argument is refused; a null optional argument is omitted.

Spec ([steer] on #801): before dispatch, `handle_tool_call` reads each tool's
`inputSchema.properties`.

- A null value for an OPTIONAL argument means omitted: the call behaves exactly as if the
  key were absent (the #728 null-boolean rule, generalized).
- A property typed `"string"` whose value is not a string (number, bool, list, object) is
  refused with `{"error": "<arg> must be a string"}` -- no ERROR record, no traceback,
  nothing written or committed. (Today `status: 5`, `title: 5`, `author: 5` ... crash
  inside a handler and are logged as bugs since #786.)
- The required-argument rules (#735, #768) still come first: missing / null / blank is
  "<arg> is required".
- `array` and `boolean` properties keep their checks (#719, #728).

`priority` is string-typed but keeps its own refusal: #171/#190 pin the unknown-priority
message for a non-string value ("Unknown priority: 5; valid: ...", byte-identical on
every surface). That is an existing ruling, so this file expects that message for
`priority` (see `_expected_refusal`); every other string property gets
"<arg> must be a string".

The single `"type": "text"` in server.py is the MCP tools/call *content* item built in
`run_server` (`{"type": "text", "text": json.dumps(result)}`), not an argument schema;
`test_every_schema_property_type_is_one_the_check_handles` pins that no argument is
typed anything but string / array / boolean.

The (tool, string-arg) pairs are read from `get_tools()` at collection time AND
hard-coded below, so a schema change is noticed. Fixtures come from #576 / #735.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401
from tests.issues.test_735_mcp_required_args import VALID, _mcp, _status
from yurtle_kanban.mcp import server as mcp_server
from yurtle_kanban.models import unknown_priority_message

# every string-typed argument of every tool, as the schema declares it today
STRING_PROPS: dict[str, list[str]] = {
    "kanban_list_items": ["status", "item_type", "assignee"],
    "kanban_get_item": ["item_id"],
    "kanban_create_item": ["item_type", "title", "priority", "assignee", "description"],
    "kanban_move_item": ["item_id", "new_status", "agent", "resolution", "superseded_by"],
    "kanban_get_my_items": ["assignee"],
    "kanban_get_blocked": ["board"],  # #1066: `blocked --board`, as the CLI
    "kanban_suggest_next": ["assignee"],
    "kanban_add_comment": ["item_id", "comment", "author"],
    "kanban_update_item": ["item_id", "title", "priority", "assignee", "description"],
    "kanban_next_id": ["prefix"],
}

BAD_VALUES: list[Any] = [5, True, [], {}]
BAD_IDS = ["int", "bool", "list", "object"]


def _schemas() -> dict[str, dict[str, Any]]:
    tools = mcp_server.KanbanMCPServer().get_tools()  # no I/O: the schemas only
    return {t["name"]: t["inputSchema"] for t in tools}


def _string_props_from_schema() -> dict[str, list[str]]:
    return {
        name: [k for k, p in schema.get("properties", {}).items() if p.get("type") == "string"]
        for name, schema in _schemas().items()
        if any(p.get("type") == "string" for p in schema.get("properties", {}).values())
    }


def _optional_string_props_from_schema() -> list[tuple[str, str]]:
    return [
        (name, k)
        for name, schema in _schemas().items()
        for k, p in schema.get("properties", {}).items()
        if p.get("type") == "string" and k not in schema.get("required", [])
    ]


LIVE_PAIRS = [(t, a) for t, args in _string_props_from_schema().items() for a in args]
OPTIONAL_PAIRS = _optional_string_props_from_schema()


def _base_args(tool: str) -> dict[str, Any]:
    """A valid value for every required argument of `tool`."""
    return {a: VALID[a] for a in _schemas()[tool].get("required", [])}


def _expected_refusal(arg: str, value: Any) -> dict[str, str]:
    if arg == "priority":  # #171 / #190: its own message, byte-identical everywhere
        return {"error": unknown_priority_message(value)}
    return {"error": f"{arg} must be a string"}


def _call_logged(repo: Repo, caplog, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call `tool`; assert no traceback and no ERROR record were logged."""
    log = mcp_server.logger  # "yurtle-kanban-mcp"
    log.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger=log.name)
    try:
        out = _mcp(repo).handle_tool_call(tool, args)
    finally:
        log.removeHandler(caplog.handler)
    tracebacks = [r.getMessage() for r in caplog.records if r.exc_info is not None]
    assert not tracebacks, f"logged a traceback: {tracebacks} -> {out}"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        [r.getMessage() for r in caplog.records],
        out,
    )
    caplog.clear()
    return out


def _refused_cleanly(repo: Repo, caplog, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    before, head, status = repo.snapshot(), repo.head(), _status(repo)
    out = _call_logged(repo, caplog, tool, args)
    assert repo.snapshot() == before, f"a refused call wrote a file: {out}"
    assert repo.head() == head, f"a refused call committed: {out}"
    assert _status(repo) == status, f"a refused call changed the working tree: {out}"
    return out


# --- the schema is what this file thinks it is ---------------------------------------


def test_schema_string_props_match_the_hard_coded_list() -> None:
    assert _string_props_from_schema() == STRING_PROPS


def test_every_schema_property_type_is_one_the_check_handles() -> None:
    """string / array / boolean only; `"type": "text"` is the MCP content item, not an arg."""
    types = {
        (name, k): p.get("type")
        for name, schema in _schemas().items()
        for k, p in schema.get("properties", {}).items()
    }
    assert set(types.values()) <= {"string", "array", "boolean"}, types


# --- a wrong-type string argument is refused ------------------------------------------


@pytest.mark.parametrize("value", BAD_VALUES, ids=BAD_IDS)
@pytest.mark.parametrize(("tool", "arg"), LIVE_PAIRS, ids=[f"{t}-{a}" for t, a in LIVE_PAIRS])
def test_wrong_type_string_arg_is_refused(
    repo: Repo, caplog, tool: str, arg: str, value: Any
) -> None:
    args = _base_args(tool)
    args[arg] = value
    out = _refused_cleanly(repo, caplog, tool, args)
    assert out == _expected_refusal(arg, value), out


@pytest.mark.parametrize(
    ("tool", "arg"),
    [
        ("kanban_list_items", "status"),
        ("kanban_list_items", "item_type"),
        ("kanban_create_item", "item_type"),
        ("kanban_create_item", "title"),
        ("kanban_create_item", "description"),
        ("kanban_add_comment", "comment"),
        ("kanban_add_comment", "author"),
        ("kanban_move_item", "new_status"),
    ],
)
def test_the_issue_examples_are_refused(repo: Repo, caplog, tool: str, arg: str) -> None:
    """The crashes named in #801, spelled out (a number)."""
    args = _base_args(tool)
    args[arg] = 5
    out = _refused_cleanly(repo, caplog, tool, args)
    assert out == {"error": f"{arg} must be a string"}, out


# --- a null optional argument is omitted ----------------------------------------------

READ_ONLY = {"kanban_list_items", "kanban_suggest_next"}
# an item with no date in its file is stamped "now" on every read: not a difference
VOLATILE = {"created", "updated"}


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


@pytest.mark.parametrize(
    ("tool", "arg"),
    [p for p in OPTIONAL_PAIRS if p[0] in READ_ONLY],
    ids=[f"{t}-{a}" for t, a in OPTIONAL_PAIRS if t in READ_ONLY],
)
def test_null_optional_arg_on_a_read_equals_omitting_it(
    repo: Repo, caplog, tool: str, arg: str
) -> None:
    omitted = _call_logged(repo, caplog, tool, _base_args(tool))
    assert "error" not in omitted, omitted
    nulled = _call_logged(repo, caplog, tool, {**_base_args(tool), arg: None})
    assert _stable(nulled) == _stable(omitted)


def test_list_items_null_status_equals_no_status(repo: Repo, caplog) -> None:
    omitted = _call_logged(repo, caplog, "kanban_list_items", {})
    nulled = _call_logged(repo, caplog, "kanban_list_items", {"status": None})
    assert _stable(nulled) == _stable(omitted)
    assert omitted["count"] > 0, omitted


def test_list_items_null_item_type_equals_no_item_type(repo: Repo, caplog) -> None:
    omitted = _call_logged(repo, caplog, "kanban_list_items", {"status": "ready"})
    nulled = _call_logged(repo, caplog, "kanban_list_items", {"status": "ready", "item_type": None})
    assert _stable(nulled) == _stable(omitted)


def test_update_item_null_title_succeeds_and_keeps_the_title(repo: Repo, caplog) -> None:
    out = _call_logged(repo, caplog, "kanban_update_item", {"item_id": "EXP-5", "title": None})
    assert out.get("success") is True, out
    assert repo.fm("EXP-5")["title"] == "Item EXP-5"
    assert out["item"]["title"] == "Item EXP-5"


@pytest.mark.parametrize("arg", ["title", "priority", "assignee", "description"])
def test_update_item_null_optional_changes_nothing_of_it(repo: Repo, caplog, arg: str) -> None:
    before = repo.fm("EXP-5")
    out = _call_logged(repo, caplog, "kanban_update_item", {"item_id": "EXP-5", arg: None})
    assert out.get("success") is True, out
    assert repo.fm("EXP-5").get(arg) == before.get(arg)


@pytest.mark.parametrize("arg", ["priority", "assignee", "description"])
def test_create_item_null_optional_is_the_default(repo: Repo, caplog, arg: str) -> None:
    base = {"item_type": "expedition", "title": "A new thing"}
    omitted = _call_logged(repo, caplog, "kanban_create_item", base)
    nulled = _call_logged(repo, caplog, "kanban_create_item", {**base, arg: None})
    assert omitted.get("success") is True, omitted
    assert nulled.get("success") is True, nulled
    assert nulled["item"].get(arg) == omitted["item"].get(arg)


def test_add_comment_null_author_is_the_resolved_actor(repo: Repo, caplog) -> None:
    out = _call_logged(
        repo, caplog, "kanban_add_comment", {"item_id": "EXP-5", "comment": "hi", "author": None}
    )
    assert out.get("success") is True, out
    assert "### tester (" in repo.path("EXP-5").read_text(encoding="utf-8")


# --- controls: these pass on the current src and must keep passing --------------------


def test_valid_calls_still_work(repo: Repo, caplog) -> None:
    for tool, args in [
        ("kanban_list_items", {"status": "ready", "item_type": "feature", "assignee": "x"}),
        ("kanban_get_item", {"item_id": "EXP-5"}),
        ("kanban_update_item", {"item_id": "EXP-5", "title": "Renamed", "priority": "high"}),
        ("kanban_create_item", {"item_type": "expedition", "title": "T", "description": "d"}),
        ("kanban_add_comment", {"item_id": "EXP-5", "comment": "c", "author": "me"}),
        ("kanban_suggest_next", {"assignee": "tester"}),
    ]:
        out = _call_logged(repo, caplog, tool, args)
        assert "error" not in out, (tool, out)
    assert repo.fm("EXP-5")["title"] == "Renamed"


@pytest.mark.parametrize("value", [None, "", "  "], ids=["null", "empty", "blank"])
def test_required_rule_still_comes_first(repo: Repo, caplog, value: Any) -> None:
    out = _refused_cleanly(
        repo, caplog, "kanban_create_item", {"item_type": "expedition", "title": value}
    )
    assert out == {"error": "title is required"}, out


@pytest.mark.parametrize("value", ["a-tag", 5, {"a": 1}], ids=["string", "int", "object"])
def test_array_error_is_unchanged(repo: Repo, caplog, value: Any) -> None:
    out = _refused_cleanly(repo, caplog, "kanban_update_item", {"item_id": "EXP-5", "tags": value})
    assert out == {"error": "tags must be an array of strings"}, out


@pytest.mark.parametrize("value", ["false", 0, "true"], ids=["str-false", "zero", "str-true"])
def test_boolean_error_is_unchanged(repo: Repo, caplog, value: Any) -> None:
    out = _refused_cleanly(
        repo, caplog, "kanban_update_item", {"item_id": "EXP-5", "allow_unknown": value}
    )
    assert out == {"error": "allow_unknown must be true or false (a JSON boolean)"}, out


def test_null_array_and_boolean_are_still_omitted(repo: Repo, caplog) -> None:
    out = _call_logged(
        repo,
        caplog,
        "kanban_update_item",
        {"item_id": "EXP-5", "tags": None, "related": None, "allow_unknown": None},
    )
    assert out.get("success") is True, out
