<!-- section: Added -->
- `move` refuses an item someone else holds in progress (`--force` does not override it); `--take-over` with an explicit `--agent`/`$YURTLE_AGENT` moves it anyway and records `kb:takenOverFrom`, and MCP `kanban_move_item` takes an optional `agent` (#574).
