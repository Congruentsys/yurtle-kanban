"""Issue #1283, review r1's two MINORs (PR #1299), as the driver ruled them.

1. A successful PUSHED ``kanban_move_item`` / ``kanban_add_comment`` reply also carries
   ``item``: the item as ``kanban_get_item`` returns it after the rescan, so clients
   reading ``result["item"]`` (the local path's shape) keep working.
2. The reply's ``item_id`` is the item's canonical id, the file's own spelling
   (``EXP-001`` for input ``exp-1``), on the pushed and the local path alike.

Readings chosen by the test partner (the driver may challenge):

a. "As ``kanban_get_item`` would return it" is pinned on a clean default-branch
   checkout (the one the push fast-forwards): ``reply["item"]`` equals the same
   server's ``kanban_get_item`` reply's ``item`` right after, and for a move its
   ``status`` is the one a fresh service reads off the pushed file. A non-default
   checkout (not fast-forwarded) is not pinned here.
b. The local move reply has no ``item_id`` key today; ruling 2 is pinned there as
   ``item["id"] == "EXP-001"`` and, if an ``item_id`` key is present, it is
   ``EXP-001`` too. The comment reply has ``item_id`` on both paths.

Harness: test_1283_mcp_push.py's (the #585 ``World`` with EXP-001 ready on origin).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.issues import test_1283_mcp_push as first
from tests.issues.test_1283_mcp_push import AGENT, ITEM_ID, args_for, head, server, service

# the first file's fixtures, reused (its autouse env, its seeded world)
_env = first._env
world = first.world

TOOLS = ["kanban_move_item", "kanban_add_comment"]


def assert_pushed(world: Any) -> None:
    assert world.remote_sha() == head(world.a), "not pushed and fast-forwarded"


# --- 1. a pushed reply carries `item` ------------------------------------------------


def test_pushed_move_reply_carries_item_with_new_status(world) -> None:
    srv = server(world.a)
    before = srv.handle_tool_call("kanban_get_item", {"item_id": ITEM_ID})["item"]["status"]

    reply = srv.handle_tool_call("kanban_move_item", args_for("kanban_move_item"))

    assert reply.get("success") is True, reply
    assert_pushed(world)
    assert "item" in reply, f"the pushed move reply has no `item`: {reply}"
    fresh = service(world.a).get_item(ITEM_ID)
    assert fresh is not None
    assert reply["item"]["id"] == ITEM_ID, reply["item"]
    assert reply["item"]["status"] == fresh.to_dict()["status"] != before, (
        reply["item"]["status"], fresh.to_dict()["status"], before,
    )
    got = srv.handle_tool_call("kanban_get_item", {"item_id": ITEM_ID})
    assert reply["item"] == got["item"], "reply's item is not what kanban_get_item returns"


def test_pushed_comment_reply_carries_item(world) -> None:
    srv = server(world.a)

    reply = srv.handle_tool_call("kanban_add_comment", args_for("kanban_add_comment"))

    assert reply.get("success") is True, reply
    assert_pushed(world)
    assert "item" in reply, f"the pushed comment reply has no `item`: {reply}"
    assert reply["item"]["id"] == ITEM_ID, reply["item"]
    got = srv.handle_tool_call("kanban_get_item", {"item_id": ITEM_ID})
    assert reply["item"] == got["item"], "reply's item is not what kanban_get_item returns"


# --- 2. item_id is the canonical id, on both paths ---------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
def test_pushed_reply_item_id_is_canonical(world, tool) -> None:
    reply = server(world.a).handle_tool_call(tool, args_for(tool, item_id="exp-1"))

    assert reply.get("success") is True, reply
    assert_pushed(world)
    assert reply.get("item_id") == "EXP-001", reply
    if "item" in reply:
        assert reply["item"]["id"] == "EXP-001", reply["item"]


def test_local_comment_reply_item_id_is_canonical(world) -> None:
    base = world.remote_sha()

    reply = server(world.a).handle_tool_call(
        "kanban_add_comment", args_for("kanban_add_comment", item_id="exp-1", push=False)
    )

    assert reply.get("success") is True, reply
    assert world.remote_sha() == base
    assert reply.get("item_id") == "EXP-001", reply


def test_local_move_reply_id_is_canonical(world) -> None:
    base = world.remote_sha()

    reply = server(world.a).handle_tool_call(
        "kanban_move_item", args_for("kanban_move_item", item_id="exp-1", push=False)
    )

    assert reply.get("success") is True, reply
    assert world.remote_sha() == base
    assert reply["item"]["id"] == "EXP-001", reply
    if "item_id" in reply:
        assert reply["item_id"] == "EXP-001", reply


def test_control_agent_constant() -> None:
    assert AGENT and ITEM_ID == "EXP-001"
