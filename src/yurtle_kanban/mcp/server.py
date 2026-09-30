"""
MCP Server for yurtle-kanban.

Provides Model Context Protocol tools for AI agents to manage kanban boards.

Usage:
    # Start the MCP server
    yurtle-kanban-mcp

    # Or use as a module
    python -m yurtle_kanban.mcp
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from .._logging import get_logger
from ..config import KanbanConfig
from ..inputs import advisory_actor, resolve_actor
from ..models import (
    PRIORITIES,
    RESOLUTIONS,
    InputRefused,
    WorkItemStatus,
    WorkItemType,
    check_encodable,
    fold_id,
    unknown_priority_message,
)
from ..service import KanbanService

logger = get_logger("yurtle-kanban-mcp")  # escapes control characters (#215)


class KanbanMCPServer:
    """MCP Server providing kanban tools for AI agents."""

    def __init__(self, repo_root: Path | None = None):
        if repo_root is None:
            repo_root = Path.cwd()

        self.repo_root = repo_root
        self._service: KanbanService | None = None
        self._names: frozenset[str] | None = None
        self._required: dict[str, tuple[str, ...]] | None = None
        self._strings: dict[str, tuple[str, ...]] | None = None

    @property
    def service(self) -> KanbanService:
        """Get or create the kanban service."""
        if self._service is None:
            config_path = self.repo_root / ".kanban" / "config.yaml"
            if config_path.exists():
                config = KanbanConfig.load(config_path)
            else:
                config = KanbanConfig()
            self._service = KanbanService(config, self.repo_root)
        return self._service

    def get_tools(self) -> list[dict[str, Any]]:
        """Return the list of available MCP tools."""
        return [
            {
                "name": "kanban_list_items",
                "description": (
                    "List work items from the kanban board."
                    " Can filter by status, type, or assignee."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": (
                                "Filter by status: backlog, ready,"
                                " in_progress, review, done, blocked"
                            ),
                            "enum": [
                                "backlog",
                                "ready",
                                "in_progress",
                                "review",
                                "done",
                                "blocked",
                            ],
                        },
                        "item_type": {
                            "type": "string",
                            "description": (
                                "Filter by type: a canonical type (feature, bug, epic,"
                                " issue, task, idea, ...) or a type an item declares"
                                " (e.g. spec), case-insensitive"
                            ),
                        },
                        "assignee": {
                            "type": "string",
                            "description": "Filter by assignee name",
                        },
                    },
                },
            },
            {
                "name": "kanban_get_item",
                "description": "Get details of a specific work item by ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "The work item ID (e.g., FEAT-001, BUG-042)",
                        },
                    },
                    "required": ["item_id"],
                },
            },
            {
                "name": "kanban_create_item",
                "description": "Create a new work item.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_type": {
                            "type": "string",
                            "description": "Type of work item",
                            "enum": ["feature", "bug", "epic", "issue", "task", "idea"],
                        },
                        "title": {
                            "type": "string",
                            "description": "Title of the work item",
                        },
                        "priority": {
                            "type": "string",
                            "description": "Priority level",
                            "enum": list(PRIORITIES),
                            "default": "medium",
                        },
                        "assignee": {
                            "type": "string",
                            "description": "Person to assign the item to",
                        },
                        "description": {
                            "type": "string",
                            "description": "Detailed description of the work item",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tags for categorization",
                        },
                    },
                    "required": ["item_type", "title"],
                },
            },
            {
                "name": "kanban_move_item",
                "description": "Move a work item to a new status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "The work item ID",
                        },
                        "new_status": {
                            "type": "string",
                            "description": (
                                "The new status: a canonical name (backlog, ready,"
                                " in_progress, review, done, blocked) or one of the"
                                " item's theme's own names (hdd `active`)"
                            ),
                        },
                        "agent": {
                            "type": "string",
                            "description": (
                                "Who is moving it (recorded as kb:by); default"
                                " $YURTLE_AGENT, then the server's git user.name."
                                " An item someone else holds in progress is refused"
                                " unless this names its holder"
                            ),
                        },
                        "resolution": {
                            "type": "string",
                            "enum": list(RESOLUTIONS),
                            "description": (
                                "How it was finished, on a finished status only"
                                " (completed on done only); a move to any other"
                                " status clears it"
                            ),
                        },
                        "superseded_by": {
                            "type": "string",
                            "description": (
                                "The item replacing it, with resolution superseded"
                                " or duplicate"
                            ),
                        },
                    },
                    "required": ["item_id", "new_status"],
                },
            },
            {
                "name": "kanban_get_board",
                "description": (
                    "Get the current kanban board state"
                    " with all items organized by column."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "kanban_get_my_items",
                "description": "Get work items assigned to a specific person.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "assignee": {
                            "type": "string",
                            "description": "The assignee name",
                        },
                    },
                    "required": ["assignee"],
                },
            },
            {
                "name": "kanban_get_blocked",
                "description": (
                    "List blocked items, as `blocked --json` does: status-blocked "
                    "items (never hdd abandoned) plus ready, in_progress and review "
                    "items with unmet dependencies, each with its `unmet` tree."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "board": {
                            "type": "string",
                            "description": (
                                "Only this board's items (dependencies are found on "
                                "every board)"
                            ),
                        },
                        "all": {
                            "type": "boolean",
                            "description": "Also backlog items with unmet dependencies",
                        },
                    },
                },
            },
            {
                "name": "kanban_suggest_next",
                "description": (
                    "Suggest the next item to work on, as `next` does: your own "
                    "in-progress item first, else the top pickable item (ready, "
                    "unassigned or yours, every dependency met)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "assignee": {
                            "type": "string",
                            "description": (
                                "Optional: who is asking; default $YURTLE_AGENT, "
                                "then git user.name"
                            ),
                        },
                    },
                },
            },
            {
                "name": "kanban_add_comment",
                "description": "Add a comment to a work item.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "The work item ID",
                        },
                        "comment": {
                            "type": "string",
                            "description": "The comment text",
                        },
                        "author": {
                            "type": "string",
                            "description": (
                                "Who is commenting; omitted = $YURTLE_AGENT, "
                                "then git user.name"
                            ),
                        },
                    },
                    "required": ["item_id", "comment"],
                },
            },
            {
                "name": "kanban_update_item",
                "description": (
                    "Update a work item's properties"
                    " (title, priority, assignee, description,"
                    " tags, depends_on, related). Use move_item for status changes."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "The work item ID to update",
                        },
                        "title": {
                            "type": "string",
                            "description": "New title for the work item",
                        },
                        "priority": {
                            "type": "string",
                            "description": "New priority level",
                            "enum": list(PRIORITIES),
                        },
                        "assignee": {
                            "type": "string",
                            "description": "New assignee for the item",
                        },
                        "description": {
                            "type": "string",
                            "description": "New description for the item",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "New tags (replaces existing tags)",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Item IDs this item depends on (replaces the list);"
                                " a self-dependency, an unknown or duplicated ID, or a"
                                " cycle is refused"
                            ),
                        },
                        "related": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Related item IDs (replaces the list)",
                        },
                        "allow_unknown": {
                            "type": "boolean",
                            "description": "Accept depends_on IDs that are on no board",
                        },
                    },
                    "required": ["item_id"],
                },
            },
            {
                "name": "kanban_next_id",
                "description": (
                    "Allocate the next available ID for a"
                    " prefix. IMPORTANT: Call this before"
                    " creating a new work item to prevent"
                    " duplicate IDs when multiple agents work"
                    " concurrently. This fetches from remote,"
                    " finds the highest existing ID, and"
                    " commits an allocation lock. A refusal"
                    ' comes back as {"error": ...} for bad'
                    " input (a malformed prefix) or, when"
                    " allocating locally (no remote, or"
                    " sync_remote false), a corrupt allocations"
                    " file in this checkout; every other refusal"
                    ' is {"success": false, "id": null,'
                    ' "message": ...}: a corrupt allocations'
                    " file when syncing or on origin, no actor,"
                    " a refused commit or push. Check both."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": (
                                "The ID prefix (e.g., 'EXP' for"
                                " expeditions, 'FEAT' for features,"
                                " 'BUG' for bugs, 'VOY' for voyages)"
                            ),
                        },
                        "sync_remote": {
                            "type": "boolean",
                            "description": (
                                "Whether to fetch/push to remote git"
                                " (default: true). Set false for"
                                " local-only allocation."
                            ),
                            "default": True,
                        },
                    },
                    "required": ["prefix"],
                },
            },
        ]

    def _tool_names(self) -> frozenset[str]:
        """The tool names `get_tools` offers, built once (#728)."""
        if self._names is None:
            self._names = frozenset(tool["name"] for tool in self.get_tools())
        return self._names

    def _required_args(self, name: str) -> tuple[str, ...]:
        """The arguments tool `name`'s schema requires, read once (#735)."""
        if self._required is None:
            self._required = {
                tool["name"]: tuple(tool.get("inputSchema", {}).get("required", ()))
                for tool in self.get_tools()
            }
        return self._required.get(name, ())

    def _string_args(self, name: str) -> tuple[str, ...]:
        """The arguments tool `name`'s schema types `string`, read once (#801)."""
        if self._strings is None:
            self._strings = {
                tool["name"]: tuple(
                    key for key, spec in tool.get("inputSchema", {}).get("properties", {}).items()
                    if spec.get("type") == "string"
                )
                for tool in self.get_tools()
            }
        return self._strings.get(name, ())

    def handle_tool_call(self, name: Any, arguments: Any) -> dict[str, Any]:
        """Handle a tool call and return the result."""
        # an unknown (or non-string) tool first, then the arguments' shape (#728):
        # never hash a list/dict `name`, which would crash out of the call
        if not isinstance(name, str) or name not in self._tool_names():
            return {"error": f"Unknown tool: {name}"}
        # `"arguments": null` is no arguments; anything else must be an object
        if arguments is None:
            arguments = {}
        elif not isinstance(arguments, dict):
            return {"error": "arguments must be an object"}
        # an explicit null for an optional argument means "omitted" (#728, #801)
        required = self._required_args(name)
        arguments = {k: v for k, v in arguments.items() if v is not None or k in required}
        # a missing (or null) required argument is named, not a KeyError (#735);
        # so is a blank one, `""` or whitespace (#768)
        for key in required:
            value = arguments.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                return {"error": f"{key} is required"}
        # a string argument of another JSON type is refused, not a crash (#801);
        # `priority` keeps its one message everywhere (#190)
        for key in self._string_args(name):
            if key != "priority" and key in arguments and not isinstance(arguments[key], str):
                return {"error": f"{key} must be a string"}
        try:
            if name == "kanban_list_items":
                return self._list_items(arguments)
            elif name == "kanban_get_item":
                return self._get_item(arguments)
            elif name == "kanban_create_item":
                return self._create_item(arguments)
            elif name == "kanban_move_item":
                return self._move_item(arguments)
            elif name == "kanban_get_board":
                return self._get_board(arguments)
            elif name == "kanban_get_my_items":
                return self._get_my_items(arguments)
            elif name == "kanban_get_blocked":
                return self._get_blocked(arguments)
            elif name == "kanban_suggest_next":
                return self._suggest_next(arguments)
            elif name == "kanban_add_comment":
                return self._add_comment(arguments)
            elif name == "kanban_update_item":
                return self._update_item(arguments)
            elif name == "kanban_next_id":
                return self._next_id(arguments)
            else:
                return {"error": f"Unknown tool: {name}"}
        except InputRefused as e:
            # an expected refusal (bad input): one line, no traceback (#728); any
            # other ValueError is a bug and keeps its traceback below (#786)
            logger.warning(f"Refused {name}: {e}")
            return {"error": str(e)}
        except Exception as e:
            logger.exception(f"Error handling tool call {name}")
            return {"error": str(e)}

    def _list_items(self, args: dict[str, Any]) -> dict[str, Any]:
        """List work items with optional filters."""
        status = None
        if "status" in args:
            status = WorkItemStatus.from_string(args["status"])

        # loaded once, unfiltered: `item_type`'s valid list reads every item (#1141)
        loaded = self.service.get_items()
        items = self.service.filter_items(loaded, status=status, assignee=args.get("assignee"))
        if "item_type" in args:  # canonical or declared, like `list --type` (#1131)
            type_match = self.service.type_filter(args["item_type"], loaded)
            items = [i for i in items if type_match(i)]

        return {
            "items": [item.to_dict() for item in items],
            "count": len(items),
        }

    def _get_item(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get a specific work item."""
        item_id = fold_id(args["item_id"])
        item = self.service.get_item(item_id)

        if not item:
            return {"error": f"Item not found: {item_id}"}

        return {"item": item.to_dict()}

    @staticmethod
    def _check_priority(priority: object) -> dict[str, Any] | None:
        """Reject a priority outside PRIORITIES, any case like the CLI (#106, #125);
        the schema enum is not enforced. The service lowercases what it writes."""
        if priority is None:
            return None
        # a JSON number / bool / list is refused too, never `.strip()`ed (#171)
        if not isinstance(priority, str) or priority.strip().lower() not in PRIORITIES:
            return {"error": unknown_priority_message(priority)}
        return None

    @staticmethod
    def _check_string_lists(args: dict[str, Any], *keys: str) -> dict[str, Any] | None:
        """The schema says these are arrays of strings: a string or other type is
        refused, never split into characters (#719)."""
        for key in keys:
            value = args.get(key)
            if value is not None and not (
                isinstance(value, list) and all(isinstance(v, str) for v in value)
            ):
                return {"error": f"{key} must be an array of strings"}
        return None

    @staticmethod
    def _check_booleans(args: dict[str, Any], *keys: str) -> dict[str, Any] | None:
        """The schema says these are booleans: "false" is not true (#719)."""
        for key in keys:
            if key in args and not isinstance(args[key], bool):
                return {"error": f"{key} must be true or false (a JSON boolean)"}
        return None

    def _create_item(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a new work item."""
        if error := self._check_priority(args.get("priority")):
            return error
        if error := self._check_string_lists(args, "tags"):
            return error
        item_type = WorkItemType.from_string(args["item_type"])

        item = self.service.create_item(
            item_type=item_type,
            title=args["title"],
            priority=args.get("priority", "medium"),
            assignee=args.get("assignee"),
            description=args.get("description"),
            tags=args.get("tags"),
        )

        return {
            "success": True,
            "item": item.to_dict(),
            "message": f"Created {item.id}: {item.title}",
        }

    def _move_item(self, args: dict[str, Any]) -> dict[str, Any]:
        """Move a work item to a new status."""
        item_id = fold_id(args["item_id"])
        # the same resolver as the CLI's --agent (#580); a blank `agent` is refused,
        # not treated as absent. It is kb:by and whom the holder guard asks (#574)
        actor = resolve_actor(args.get("agent"), cwd=self.repo_root, flag="agent")
        # a long-lived server: see duplicates as the files are now (#732, #742)
        self.service.scan()
        target = self.service.get_item(item_id)
        if target is None:
            return {"error": f"Item not found: {item_id}"}
        # before its status is read off one of the copies (#742)
        self.service.refuse_duplicate(target, "a move")
        # the item's own theme's names only, as `move` resolves them (#587, #604)
        new_status = self.service.resolve_status_name(target, str(args["new_status"]))
        if new_status is None:
            valid = ", ".join(self.service.listed_status_names(target))
            return {"error": f"Unknown status: {args['new_status']}. Valid statuses: {valid}"}

        item = self.service.move_item(
            item_id, new_status, actor=actor, resolution=args.get("resolution"),
            superseded_by=args.get("superseded_by"),
        )

        return {
            "success": True,
            "item": item.to_dict(),
            "message": f"Moved {item.id} to {new_status.value}",
        }

    def _get_board(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get the full board state."""
        board = self.service.get_board()

        columns = []
        for col in board.columns:
            items = board.get_column_items(col.id)  # as the counts see it (#87)

            columns.append(
                {
                    "id": col.id,
                    "name": col.name,
                    "wip_limit": col.wip_limit,
                    "items": [item.to_dict() for item in items],
                    "count": len(items),
                }
            )

        return {
            "board": {
                "id": board.id,
                "name": board.name,
                "columns": columns,
                "total_items": len(board.items),
            }
        }

    def _get_my_items(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get items for a specific assignee."""
        assignee = args["assignee"]
        items = self.service.get_my_items(assignee)

        return {
            "assignee": assignee,
            "items": [item.to_dict() for item in items],
            "count": len(items),
        }

    def _get_blocked(self, args: dict[str, Any]) -> dict[str, Any]:
        """What `blocked --json` gives, from the same service function (#1066)."""
        if error := self._check_booleans(args, "all"):
            return error
        return self.service.blocked_report(args.get("board"), args.get("all", False))

    def _suggest_next(self, args: dict[str, Any]) -> dict[str, Any]:
        """Suggest the next item to work on: the item `next` gives (#575)."""
        actor = advisory_actor(args.get("assignee"), cwd=self.repo_root, flag="assignee")
        control = self.service.control_state(fresh=True)  # a long-lived server (#1067)
        if control.halted:  # as `next`: nothing, even work in progress (#582)
            return {"suggestion": None, "message": control.refusal(), "halted": True}
        found = self.service.next_item(actor)
        if found is None:
            return {"suggestion": None, "message": "Nothing pickable"}
        item, kind, reason = found
        return {
            "suggestion": {**item.to_dict(), "kind": kind, "reason": reason},
            "message": f"Suggested: {item.id} - {item.title} ({reason})",
        }

    def _add_comment(self, args: dict[str, Any]) -> dict[str, Any]:
        """Add a comment to an item."""
        item_id = fold_id(args["item_id"])
        comment = args["comment"]
        # the same resolver as the CLI's --agent: no "agent" default (#580)
        author = resolve_actor(args.get("author"), cwd=self.repo_root)
        # the text is checked before the rescan: a refusal scans nothing (#740, #755),
        # with the public check the CLI uses (#767)
        check_encodable("comment", comment)
        check_encodable("author", author)
        self.service.scan()  # see duplicates as the files are now (#732, #742)

        item = self.service.add_comment(item_id, comment, author)

        return {
            "success": True,
            "item_id": item.id,
            "message": f"Added comment to {item.id}",
        }

    def _update_item(self, args: dict[str, Any]) -> dict[str, Any]:
        """Update a work item's properties."""
        if error := self._check_priority(args.get("priority")):
            return error
        if error := self._check_string_lists(args, "tags", "depends_on", "related"):
            return error
        if error := self._check_booleans(args, "allow_unknown"):
            return error
        # a long-lived server: see duplicates as the files are now (#732); a deps
        # edit is rescanned by the service itself (#638), so once either way (#740)
        if args.get("depends_on") is None:
            self.service.scan()
        item_id = fold_id(args["item_id"])

        item = self.service.update_item(
            item_id=item_id,
            title=args.get("title"),
            priority=args.get("priority"),
            assignee=args.get("assignee"),
            description=args.get("description"),
            tags=args.get("tags"),
            depends_on=args.get("depends_on"),
            related=args.get("related"),
            allow_unknown=args.get("allow_unknown", False),
        )

        return {
            "success": True,
            "item": item.to_dict(),
            "message": f"Updated {item.id}",
        }

    def _next_id(self, args: dict[str, Any]) -> dict[str, Any]:
        """Allocate the next available ID for a prefix."""
        if error := self._check_booleans(args, "sync_remote"):
            return error
        prefix = args["prefix"].upper()
        sync_remote = args.get("sync_remote", True)

        result = self.service.allocate_next_id(
            prefix=prefix,
            sync_remote=sync_remote,
            commit_allocation=True,
        )

        return result


def run_server():
    """Run the MCP server using stdio transport."""
    import asyncio

    server = KanbanMCPServer()
    logger.info("Starting yurtle-kanban MCP server")

    # Simple stdio-based MCP server
    class RpcError(Exception):
        """A JSON-RPC error reply: the explicit signal, never a guessed key (#568)."""

        def __init__(self, code: int, message: str):
            super().__init__(message)
            self.code, self.message = code, message

    async def handle_request(request: dict) -> dict | None:
        """The success payload for `request` (None: nothing to send), or raise
        RpcError."""
        method = request.get("method", "")

        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "yurtle-kanban",
                    "version": __version__,  # the package's, one source of truth (#561)
                },
            }

        elif method == "tools/list":
            return {"tools": server.get_tools()}

        elif method == "tools/call":
            params = request.get("params")
            if params is None:
                params = {}  # null or missing: no params (#745)
            elif not isinstance(params, dict):
                raise RpcError(-32602, "Invalid params: params must be an object")
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            result = server.handle_tool_call(tool_name, arguments)

            payload: dict[str, Any] = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2),
                    }
                ]
            }
            # a failed tool is a result the client must see as failed (MCP), not a
            # protocol error (#568)
            if isinstance(result, dict) and "error" in result:
                payload["isError"] = True
            return payload

        elif method == "notifications/initialized":
            return None  # No response for notifications

        else:
            raise RpcError(-32601, f"Unknown method: {method}")

    def send_error(req_id: Any, code: int, message: str) -> None:
        reply = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        print(json.dumps(reply), flush=True)

    def line_is_blank(raw: bytes | str) -> bool:
        return not (raw.strip() if isinstance(raw, (bytes, str)) else raw)

    async def main():
        """Main server loop."""
        while True:
            # bytes, decoded one line at a time: a bad line costs only itself,
            # never the requests after it (#568)
            stream = getattr(sys.stdin, "buffer", sys.stdin)
            raw = stream.readline()
            if not raw:
                break
            try:
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                request = json.loads(line)
            except (ValueError, RecursionError) as e:  # bad UTF-8 or JSON, too deep
                if line_is_blank(raw):
                    continue
                send_error(None, -32700, f"Parse error: {e}")
                continue
            if not isinstance(request, dict):
                send_error(None, -32600, "Invalid Request: not a JSON object")
                continue
            # JSON-RPC 2.0: a request without an `id` is a notification, and a
            # notification is never answered, not even with an error (#568)
            notification = "id" not in request
            try:
                payload = await handle_request(request)
                # a success payload goes under `result` (#563)
                reply = None if payload is None else {"result": payload}
            except RpcError as e:
                reply = {"error": {"code": e.code, "message": e.message}}
            except Exception as e:
                logger.exception("Error in main loop")
                reply = {"error": {"code": -32603, "message": str(e)}}
            if reply is None or notification:
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], **reply}), flush=True)

    asyncio.run(main())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_server()
