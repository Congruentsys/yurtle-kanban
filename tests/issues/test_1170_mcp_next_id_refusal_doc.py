"""Issue #1170: MCP `kanban_next_id`'s description names both refusal shapes (#847
rule 3, #1162): `{"error": ...}` for a local refusal, and the refusal dict with
`success` false for a corrupt origin."""
from __future__ import annotations

from yurtle_kanban.mcp import server as mcp_server


def test_next_id_description_names_both_refusal_shapes() -> None:
    tools = {t["name"]: t for t in mcp_server.KanbanMCPServer().get_tools()}
    said = tools["kanban_next_id"]["description"]
    assert '"error"' in said, said
    assert '"success": false' in said, said
