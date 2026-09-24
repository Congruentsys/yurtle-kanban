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

from ..config import KanbanConfig
from ..models import PRIORITIES, WorkItemStatus, WorkItemType
from ..service import KanbanService

logger = logging.getLogger("yurtle-kanban-mcp")


class KanbanMCPServer:
    """MCP Server providing kanban tools for AI agents."""

    def __init__(self, repo_root: Path | None = None):
        if repo_root is None:
            repo_root = Path.cwd()

        self.repo_root = repo_root
        self._service: KanbanService | None = None

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
                            "description": "Filter by type: feature, bug, epic, issue, task, idea",
                            "enum": ["feature", "bug", "epic", "issue", "task", "idea"],
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
                            "description": "The new status",
                            "enum": [
                                "backlog",
                                "ready",
                                "in_progress",
                                "review",
                                "done",
                                "blocked",
                            ],
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
                "description": "Get all blocked work items.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "kanban_suggest_next",
                "description": "Suggest the next highest-priority item to work on.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "assignee": {
                            "type": "string",
                            "description": "Optional: prefer items assigned to this person",
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
                            "description": "Comment author name",
                            "default": "agent",
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
                    " tags). Use move_item for status changes."
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
                    " commits an allocation lock."
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

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle a tool call and return the result."""
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
        except Exception as e:
            logger.exception(f"Error handling tool call {name}")
            return {"error": str(e)}

    def _list_items(self, args: dict[str, Any]) -> dict[str, Any]:
        """List work items with optional filters."""
        status = None
        if "status" in args:
            status = WorkItemStatus.from_string(args["status"])

        item_type = None
        if "item_type" in args:
            item_type = WorkItemType.from_string(args["item_type"])

        items = self.service.get_items(
            status=status,
            item_type=item_type,
            assignee=args.get("assignee"),
        )

        return {
            "items": [item.to_dict() for item in items],
            "count": len(items),
        }

    def _get_item(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get a specific work item."""
        item_id = args["item_id"].upper()
        item = self.service.get_item(item_id)

        if not item:
            return {"error": f"Item not found: {item_id}"}

        return {"item": item.to_dict()}

    @staticmethod
    def _check_priority(priority: str | None) -> dict[str, Any] | None:
        """Reject a priority outside PRIORITIES, any case like the CLI (#106, #125);
        the schema enum is not enforced. The service lowercases what it writes."""
        if priority is not None and priority.strip().lower() not in PRIORITIES:
            return {"error": f"Unknown priority: {priority}. Valid: {', '.join(PRIORITIES)}"}
        return None

    def _create_item(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a new work item."""
        if error := self._check_priority(args.get("priority")):
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
        item_id = args["item_id"].upper()
        new_status = WorkItemStatus.from_string(args["new_status"])

        item = self.service.move_item(item_id, new_status)

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
            try:
                status = WorkItemStatus.from_string(col.id)
                items = board.get_items_by_status(status)
            except ValueError:
                items = []

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
        """Get all blocked items."""
        items = self.service.get_blocked_items()

        return {
            "blocked_items": [item.to_dict() for item in items],
            "count": len(items),
        }

    def _suggest_next(self, args: dict[str, Any]) -> dict[str, Any]:
        """Suggest the next item to work on."""
        item = self.service.suggest_next_item(assignee=args.get("assignee"))

        if not item:
            return {"suggestion": None, "message": "No ready items to work on"}

        return {
            "suggestion": item.to_dict(),
            "message": f"Suggested: {item.id} - {item.title}",
        }

    def _add_comment(self, args: dict[str, Any]) -> dict[str, Any]:
        """Add a comment to an item."""
        item_id = args["item_id"].upper()
        comment = args["comment"]
        author = args.get("author", "agent")

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
        item_id = args["item_id"].upper()

        item = self.service.update_item(
            item_id=item_id,
            title=args.get("title"),
            priority=args.get("priority"),
            assignee=args.get("assignee"),
            description=args.get("description"),
            tags=args.get("tags"),
        )

        return {
            "success": True,
            "item": item.to_dict(),
            "message": f"Updated {item.id}",
        }

    def _next_id(self, args: dict[str, Any]) -> dict[str, Any]:
        """Allocate the next available ID for a prefix."""
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
    async def handle_request(request: dict) -> dict:
        """Handle an MCP request."""
        method = request.get("method", "")

        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "yurtle-kanban",
                    "version": "0.1.0",
                },
            }

        elif method == "tools/list":
            return {"tools": server.get_tools()}

        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            result = server.handle_tool_call(tool_name, arguments)

            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2),
                    }
                ]
            }

        elif method == "notifications/initialized":
            return None  # No response for notifications

        else:
            return {"error": {"code": -32601, "message": f"Unknown method: {method}"}}

    async def main():
        """Main server loop."""
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                request = json.loads(line)
                response = await handle_request(request)

                if response is not None:
                    response["jsonrpc"] = "2.0"
                    if "id" in request:
                        response["id"] = request["id"]
                    print(json.dumps(response), flush=True)

            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.exception("Error in main loop")
                error_response = {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": str(e)},
                }
                print(json.dumps(error_response), flush=True)

    asyncio.run(main())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_server()
