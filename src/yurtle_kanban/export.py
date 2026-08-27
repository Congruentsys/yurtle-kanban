"""
Export functionality for yurtle-kanban.

Provides export to:
- HTML (standalone, GitHub Pages compatible)
- Markdown (table format)
- JSON (for integrations)
"""

import json
from collections import Counter
from datetime import datetime
from html import escape as html_escape
from typing import Any

from .models import Board, WorkItem, WorkItemStatus


def export_html(board: Board) -> str:
    """Export board to standalone HTML."""
    # Group items by status
    items_by_status: dict[str, list[WorkItem]] = {}
    for col in board.columns:
        try:
            status = WorkItemStatus.from_string(col.id)
            items_by_status[col.id] = board.get_items_by_status(status)
        except ValueError:
            items_by_status[col.id] = []

    # Build HTML
    columns_html = []
    for col in board.columns:
        items = items_by_status.get(col.id, [])
        items_html = "\n".join(_render_html_card(item) for item in items)

        wip_class = ""
        wip_badge = ""
        if col.type_wip_limits is not None:
            # Per-type WIP: check each type independently
            type_counts = Counter(item.item_type.value for item in items)
            any_exceeded = any(
                col.is_over_wip(type_counts[t], item_type=t)
                for t in type_counts
            )
            if any_exceeded:
                wip_class = "wip-exceeded"
            # Show aggregate count only in badge
            wip_badge = f'<span class="wip-badge">{len(items)}</span>'
        elif col.wip_limit:
            if len(items) > col.wip_limit:
                wip_class = "wip-exceeded"
            wip_badge = f'<span class="wip-badge">{len(items)}/{col.wip_limit}</span>'

        columns_html.append(f"""
        <div class="column {wip_class}">
            <h2>{html_escape(col.name)} <span class="count">({len(items)})</span> {wip_badge}</h2>
            <div class="cards">
                {items_html or '<p class="empty">No items</p>'}
            </div>
        </div>
        """)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{board.name}</title>
    <style>
        :root {{
            --bg-color: #1a1a2e;
            --card-bg: #16213e;
            --text-color: #eee;
            --border-color: #0f3460;
            --accent-color: #e94560;
        }}

        * {{ box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 20px;
        }}

        h1 {{
            text-align: center;
            margin-bottom: 30px;
        }}

        .board {{
            display: flex;
            gap: 20px;
            overflow-x: auto;
            padding-bottom: 20px;
        }}

        .column {{
            flex: 0 0 300px;
            background: var(--card-bg);
            border-radius: 8px;
            padding: 15px;
        }}

        .column.wip-exceeded {{
            border: 2px solid var(--accent-color);
        }}

        .column h2 {{
            font-size: 1rem;
            margin: 0 0 15px 0;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
        }}

        .count {{
            opacity: 0.6;
            font-weight: normal;
        }}

        .wip-badge {{
            background: var(--accent-color);
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.8rem;
            margin-left: 5px;
        }}

        .cards {{
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}

        .card {{
            background: var(--bg-color);
            border-radius: 6px;
            padding: 12px;
            border-left: 3px solid var(--border-color);
        }}

        .card.priority-critical {{ border-left-color: #ef4444; }}
        .card.priority-high {{ border-left-color: #f59e0b; }}
        .card.priority-medium {{ border-left-color: #3b82f6; }}
        .card.priority-low {{ border-left-color: #6b7280; }}

        .card-id {{
            font-size: 0.8rem;
            color: #888;
            margin-bottom: 5px;
        }}

        .card-title {{
            font-weight: 500;
            margin-bottom: 8px;
        }}

        .card-meta {{
            font-size: 0.8rem;
            color: #888;
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}

        .tag {{
            background: var(--border-color);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.75rem;
        }}

        .empty {{
            color: #666;
            text-align: center;
            padding: 20px;
        }}

        footer {{
            text-align: center;
            margin-top: 30px;
            color: #666;
            font-size: 0.8rem;
        }}

        @media (max-width: 768px) {{
            .board {{
                flex-direction: column;
            }}
            .column {{
                flex: none;
                width: 100%;
            }}
        }}
    </style>
</head>
<body>
    <h1>{board.name}</h1>

    <div class="board">
        {"".join(columns_html)}
    </div>

    <footer>
        Generated by yurtle-kanban on {datetime.now().strftime("%Y-%m-%d %H:%M")}
    </footer>
</body>
</html>"""


def _render_html_card(item: WorkItem) -> str:
    """Render a work item as an HTML card."""
    tags_html = " ".join(
        f'<span class="tag">{html_escape(tag)}</span>' for tag in item.tags[:3]
    )
    type_label = html_escape(item.item_type.value.upper())
    id_label = html_escape(item.id)

    return f"""
    <div class="card priority-{html_escape(item.priority or "medium")}">
        <div class="card-id">{type_label} {id_label}</div>
        <div class="card-title">{html_escape(item.title)}</div>
        <div class="card-meta">
            {f"<span>@{html_escape(item.assignee)}</span>" if item.assignee else ""}
            {tags_html}
        </div>
    </div>
    """


def export_markdown(board: Board) -> str:
    """Export board to markdown table format."""
    lines = [
        f"# {board.name}",
        "",
        f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
    ]

    # Create table header
    header = "| " + " | ".join(col.name for col in board.columns) + " |"
    separator = "| " + " | ".join("---" for _ in board.columns) + " |"

    lines.append(header)
    lines.append(separator)

    # Group items by status
    items_by_status: dict[str, list[WorkItem]] = {}
    for col in board.columns:
        try:
            status = WorkItemStatus.from_string(col.id)
            items_by_status[col.id] = board.get_items_by_status(status)
        except ValueError:
            items_by_status[col.id] = []

    # Find max items
    max_items = max(len(items) for items in items_by_status.values()) if items_by_status else 0

    # Add rows
    for i in range(max(max_items, 1)):
        row = []
        for col in board.columns:
            items = items_by_status.get(col.id, [])
            if i < len(items):
                item = items[i]
                row.append(f"**{item.id}** {item.title[:20]}...")
            else:
                row.append("")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")

    # Add statistics
    lines.append("## Statistics")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("| --- | --- |")

    counts = board.get_column_counts()
    for col in board.columns:
        count = counts.get(col.id, 0)
        lines.append(f"| {col.name} | {count} |")

    lines.append(f"| **Total** | **{sum(counts.values())}** |")

    return "\n".join(lines)


def export_expedition_index(board: Board, min_id: int = 600) -> str:
    """Export an enhanced development board index with all item types.

    Features:
    - Expeditions table with Agent, Depends, For columns
    - Chores and Voyages tables
    - Work Trail (items >= min_id) with dependency hierarchy
    - Dependency Tree for active development items
    - Summary counts by type

    Args:
        board: The kanban board with all items
        min_id: Minimum item number for Work Trail section (default: 600)

    Returns:
        Markdown formatted development board index
    """
    lines = [
        "# Development Board",
        "",
        f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
    ]

    # Group items by type
    expeditions = [
        item for item in board.items if item.item_type.value in ("expedition", "feature", "epic")
    ]
    expeditions.sort(key=lambda x: _extract_id_number(x.id), reverse=True)

    chores = [item for item in board.items if item.item_type.value == "chore"]
    chores.sort(key=lambda x: _extract_id_number(x.id), reverse=True)

    voyages = [item for item in board.items if item.item_type.value == "voyage"]
    voyages.sort(key=lambda x: _extract_id_number(x.id), reverse=True)

    signals = [item for item in board.items if item.item_type.value == "signal"]
    signals.sort(key=lambda x: _extract_id_number(x.id), reverse=True)

    # =========================================================================
    # Section 1: Expeditions
    # =========================================================================
    lines.extend(
        [
            f"## Expeditions ({len(expeditions)})",
            "",
            "| # | Title | Status | Pri | Agent | Depends | For |",
            "|---|-------|--------|-----|-------|---------|-----|",
        ]
    )

    for item in expeditions:
        num = _extract_id_number(item.id)
        title = item.title[:50] + "..." if len(item.title) > 50 else item.title
        status = _status_emoji(item.status)
        priority = str(item.priority or "medium").upper()[:4]
        agent = _format_assignee(item.assignee)
        depends = _format_dependencies(item.depends_on)
        purpose = _infer_purpose(item.tags)

        lines.append(
            f"| {num} | {title} | {status} | {priority} | {agent} | {depends} | {purpose} |"
        )

    lines.append("")

    # =========================================================================
    # Section 2: Voyages
    # =========================================================================
    if voyages:
        lines.extend(
            [
                f"## Voyages ({len(voyages)})",
                "",
                "| # | Title | Status | Pri | Items |",
                "|---|-------|--------|-----|-------|",
            ]
        )

        for item in voyages:
            num = _extract_id_number(item.id)
            title = item.title[:55] + "..." if len(item.title) > 55 else item.title
            status = _status_emoji(item.status)
            priority = str(item.priority or "medium").upper()[:4]
            related_count = len(item.related) if item.related else "-"

            lines.append(f"| {num} | {title} | {status} | {priority} | {related_count} |")

        lines.append("")

    # =========================================================================
    # Section 3: Chores
    # =========================================================================
    if chores:
        lines.extend(
            [
                f"## Chores ({len(chores)})",
                "",
                "| # | Title | Status | Pri |",
                "|---|-------|--------|-----|",
            ]
        )

        for item in chores:
            num = _extract_id_number(item.id)
            title = item.title[:60] + "..." if len(item.title) > 60 else item.title
            status = _status_emoji(item.status)
            priority = str(item.priority or "medium").upper()[:4]

            lines.append(f"| {num} | {title} | {status} | {priority} |")

        lines.append("")

    # =========================================================================
    # Section 4: Signals
    # =========================================================================
    if signals:
        lines.extend(
            [
                f"## Signals ({len(signals)})",
                "",
                "| # | Title | Status | Pri |",
                "|---|-------|--------|-----|",
            ]
        )

        for item in signals:
            num = _extract_id_number(item.id)
            title = item.title[:60] + "..." if len(item.title) > 60 else item.title
            status = _status_emoji(item.status)
            priority = str(item.priority or "medium").upper()[:4]

            lines.append(f"| {num} | {title} | {status} | {priority} |")

        lines.append("")

    lines.extend(
        [
            "**Legend:** 🔄 Active | 🟢 Ready | 🟡 Blocked/Harbor | ✅ Done | ❌ Stranded",
            "",
        ]
    )

    # =========================================================================
    # Section 2: Work Trail (items >= min_id)
    # =========================================================================
    recent_items = [item for item in expeditions if _extract_id_number(item.id) >= min_id]

    if recent_items:
        lines.extend(
            [
                f"## Work Trail ({min_id} and up)",
                "",
                "*What we were trying to do → what we had to do first. Indentation = sidetrack.*",
                "",
            ]
        )

        # Build dependency graph for indentation
        dep_graph = _build_dependency_graph(recent_items)

        # Group by date if available, otherwise by status
        done_items = [i for i in recent_items if i.status == WorkItemStatus.DONE]
        active_items = [i for i in recent_items if i.status == WorkItemStatus.IN_PROGRESS]
        ready_items = [i for i in recent_items if i.status == WorkItemStatus.READY]

        # Show active items first
        for item in active_items:
            indent = _get_dependency_indent(item, dep_graph)
            num = _extract_id_number(item.id)
            status_emoji = "🔄"
            short_title = item.title.split(":")[0] if ":" in item.title else item.title[:40]
            deps = _format_dependencies(item.depends_on) or "-"
            lines.append(
                f"{indent}- **{item.created or 'WIP'}** {num} {status_emoji} {short_title}"
            )
            if item.depends_on:
                lines.append(f"{indent}  - Depends: {deps}")

        # Show ready items
        for item in ready_items:
            indent = _get_dependency_indent(item, dep_graph)
            num = _extract_id_number(item.id)
            status_emoji = "🟢"
            short_title = item.title.split(":")[0] if ":" in item.title else item.title[:40]
            lines.append(f"{indent}- **READY** {num} {status_emoji} {short_title}")

        # Show recently done items (last 10)
        for item in done_items[:10]:
            indent = _get_dependency_indent(item, dep_graph)
            num = _extract_id_number(item.id)
            status_emoji = "✅"
            short_title = item.title.split(":")[0] if ":" in item.title else item.title[:40]
            lines.append(f"{indent}- **DONE** {num} {status_emoji} {short_title}")

        lines.append("")

    # =========================================================================
    # Section 3: Dependency Tree (active development)
    # =========================================================================
    active_or_ready = [
        item
        for item in expeditions
        if item.status in (WorkItemStatus.IN_PROGRESS, WorkItemStatus.READY, WorkItemStatus.REVIEW)
    ]

    if active_or_ready:
        lines.extend(
            [
                "## Dependency Tree (Active Development)",
                "",
                "```",
                "Active Work Dependencies",
                "│",
            ]
        )

        # Build tree structure
        for item in active_or_ready[:15]:  # Limit to 15 items
            num = _extract_id_number(item.id)
            short_title = item.title.split(":")[0] if ":" in item.title else item.title[:35]
            status_str = _status_bracket(item.status)
            deps = _format_dependencies(item.depends_on) or "none"

            lines.append(f"├── {num} {short_title} [{status_str}]")
            lines.append(f"│     Depends: {deps}")

        lines.extend(
            [
                "```",
                "",
            ]
        )

    # =========================================================================
    # Section 6: Summary
    # =========================================================================
    type_counts = [
        ("Expeditions", len(expeditions)),
        ("Voyages", len(voyages)),
        ("Chores", len(chores)),
        ("Signals", len(signals)),
    ]
    total = sum(c for _, c in type_counts)

    lines.extend(
        [
            "## Summary",
            "",
            "| Type | Count |",
            "| --- | --- |",
        ]
    )
    for name, count in type_counts:
        if count > 0:
            lines.append(f"| {name} | {count} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")

    return "\n".join(lines)


def _extract_id_number(item_id: str) -> int:
    """Extract numeric ID from item ID like 'EXP-732'."""
    import re

    match = re.search(r"\d+", item_id)
    return int(match.group()) if match else 0


def _status_emoji(status: WorkItemStatus) -> str:
    """Convert status to emoji indicator."""
    emoji_map = {
        WorkItemStatus.DONE: "✅",
        WorkItemStatus.IN_PROGRESS: "🔄",
        WorkItemStatus.READY: "🟢",
        WorkItemStatus.REVIEW: "🔍",
        WorkItemStatus.BLOCKED: "🟡",
        WorkItemStatus.BACKLOG: "📋",
    }
    return emoji_map.get(status, "⬜")


def _status_bracket(status: WorkItemStatus) -> str:
    """Convert status to bracket indicator."""
    status_map = {
        WorkItemStatus.DONE: "DONE",
        WorkItemStatus.IN_PROGRESS: "ACTIVE",
        WorkItemStatus.READY: "READY",
        WorkItemStatus.REVIEW: "REVIEW",
        WorkItemStatus.BLOCKED: "BLOCKED",
        WorkItemStatus.BACKLOG: "BACKLOG",
    }
    return status_map.get(status, "UNKNOWN")


def _format_assignee(assignee: str | None) -> str:
    """Format assignee for table display."""
    if not assignee:
        return "-"
    # Handle common formats
    assignee_str = str(assignee)
    if "agent-a" in assignee_str.lower():
        return "A"
    if "agent-b" in assignee_str.lower():
        return "B"
    if assignee_str.startswith("<") and assignee_str.endswith(">"):
        # Handle URIs like <agent-b>
        inner = assignee_str[1:-1]
        if "agent-a" in inner.lower():
            return "A"
        if "agent-b" in inner.lower():
            return "B"
        return inner[:10]
    return assignee_str[:10]


def _format_dependencies(depends_on: list[str]) -> str:
    """Format dependencies as comma-separated numbers."""
    if not depends_on:
        return "-"
    nums = []
    for dep in depends_on:
        num = _extract_id_number(str(dep))
        if num > 0:
            status_suffix = ""  # Could add ✅ if we knew the status
            nums.append(f"{num}{status_suffix}")
    return ",".join(nums) if nums else "-"


def _infer_purpose(tags: list[str]) -> str:
    """Infer purpose/project from tags."""
    if not tags:
        return "-"
    # Common tag mappings
    purpose_map = {
        "v9": "V9",
        "p108": "P108",
        "p111": "P111",
        "p115": "P115",
        "cog": "COG",
        "y-layer": "Y-Layer",
        "training": "Train",
        "testing": "Test",
        "mac": "Mac",
        "gpu": "GPU",
        "performance": "Perf",
        "tooling": "Tool",
    }
    purposes = []
    for tag in tags:
        tag_lower = tag.lower()
        for key, val in purpose_map.items():
            if key in tag_lower:
                purposes.append(val)
                break
    return ",".join(purposes[:2]) if purposes else tags[0][:6] if tags else "-"


def _build_dependency_graph(items: list[WorkItem]) -> dict[str, list[str]]:
    """Build a graph of item dependencies."""
    graph: dict[str, list[str]] = {}
    for item in items:
        item_num = str(_extract_id_number(item.id))
        dep_nums = [str(_extract_id_number(d)) for d in (item.depends_on or [])]
        graph[item_num] = dep_nums
    return graph


def _get_dependency_indent(item: WorkItem, dep_graph: dict[str, list[str]]) -> str:
    """Calculate indent based on dependency depth."""
    # Simple heuristic: if this item is depended on by others, no indent
    # If this item depends on others, indent
    item_num = str(_extract_id_number(item.id))

    # Check if any item depends on this one
    is_depended_on = any(item_num in deps for deps in dep_graph.values())

    if is_depended_on:
        return ""
    elif item.depends_on:
        return "  "
    else:
        return ""


def export_json(board: Board) -> str:
    """Export board to JSON format."""
    data: dict[str, Any] = {
        "board": {
            "id": board.id,
            "name": board.name,
            "generated_at": datetime.now().isoformat(),
        },
        "columns": [],
        "items": [],
        "statistics": {},
    }

    # Add columns
    for col in board.columns:
        col_data: dict[str, Any] = {
            "id": col.id,
            "name": col.name,
            "order": col.order,
            "wip_limit": col.wip_limit,
        }
        if col.type_wip_limits is not None:
            col_data["type_wip_limits"] = col.type_wip_limits
        data["columns"].append(col_data)

    # Add items
    for item in board.items:
        data["items"].append(item.to_dict())

    # Add statistics
    counts = board.get_column_counts()
    data["statistics"] = {
        "by_status": counts,
        "total": sum(counts.values()),
    }

    return json.dumps(data, indent=2)


# Status emoji for research items
_RESEARCH_STATUS_EMOJI = {
    "complete": "\u2705",     # ✅
    "done": "\u2705",         # ✅
    "active": "\U0001F4DD",   # 📝
    "draft": "\U0001F4DD",    # 📝
    "writing": "\u270D\uFE0F",  # ✍️
    "running": "\U0001F3C3",  # 🏃
    "planned": "\U0001F4CB",  # 📋
    "abandoned": "\u274C",    # ❌
    "captured": "\U0001F4A1", # 💡
    "validated": "\u2705",    # ✅
    "rejected": "\u274C",     # ❌
    "preliminary": "\U0001F50D",  # 🔍
}


def export_research_index(board: Board) -> str:
    """Export a research board index grouped by item type.

    Groups items into sections: Papers, Hypotheses, Experiments, Measures, Ideas,
    Literature. Each section has a table with type-appropriate columns.

    Args:
        board: The research board with all items

    Returns:
        Markdown formatted research index
    """
    lines = [
        "# Research Index",
        "",
        f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
    ]

    # Group items by type
    type_groups: dict[str, list[WorkItem]] = {}
    for item in board.items:
        type_key = item.item_type.value
        type_groups.setdefault(type_key, []).append(item)

    # Section order and display config
    sections = [
        ("paper", "Papers", ["#", "Title", "Status", "Pri", "Venue", "Assignee"]),
        ("hypothesis", "Hypotheses", ["#", "Title", "Status", "Paper"]),
        ("experiment", "Experiments", ["#", "Title", "Status", "Assignee"]),
        ("measure", "Measures", ["#", "Title", "Status", "Unit", "Category"]),
        ("idea", "Ideas", ["#", "Title", "Status", "Pri"]),
        ("literature", "Literature", ["#", "Title", "Status", "Assignee"]),
    ]

    total = 0

    for type_key, section_name, columns in sections:
        items = type_groups.get(type_key, [])
        if not items:
            continue

        total += len(items)

        # Sort by ID number
        items.sort(key=lambda x: _extract_id_number(x.id))

        lines.append(f"## {section_name} ({len(items)})")
        lines.append("")

        # Table header
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join("---" for _ in columns) + " |"
        lines.append(header)
        lines.append(separator)

        for item in items:
            # Use original status string for research-specific emoji
            original_status = item.metadata.get("_original_status", item.status.value)
            status_emoji = _RESEARCH_STATUS_EMOJI.get(
                original_status, _status_emoji(item.status)
            )
            row = _research_row(item, type_key, status_emoji)
            lines.append("| " + " | ".join(row) + " |")

        lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Type | Count |")
    lines.append("| --- | --- |")
    for type_key, section_name, _ in sections:
        count = len(type_groups.get(type_key, []))
        if count > 0:
            lines.append(f"| {section_name} | {count} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")

    lines.append(
        "**Legend:** "
        "\u2705 Complete | "
        "\U0001F4DD Draft/Active | "
        "\u270D\uFE0F Writing | "
        "\U0001F4CB Planned | "
        "\U0001F4A1 Captured | "
        "\u274C Abandoned"
    )
    lines.append("")

    return "\n".join(lines)


def _research_row(item: WorkItem, type_key: str, status_emoji: str) -> list[str]:
    """Build a table row for a research item based on its type."""
    # Extract numeric ID for display
    id_num = _extract_id_number(item.id) or item.id
    title = item.title[:60] + ("..." if len(item.title) > 60 else "")

    if type_key == "paper":
        venue = item.metadata.get("venue", "-")
        assignee = item.assignee or "-"
        pri = (item.priority or "-")[:4].upper()
        return [str(id_num), title, status_emoji, pri, str(venue)[:25], assignee]

    elif type_key == "hypothesis":
        paper = item.metadata.get("paper", "-")
        return [str(item.id), title, status_emoji, str(paper)]

    elif type_key == "experiment":
        assignee = item.assignee or "-"
        return [str(item.id), title, status_emoji, assignee]

    elif type_key == "measure":
        unit = item.metadata.get("unit", "-")
        category = item.metadata.get("category", "-")
        return [str(item.id), title, status_emoji, str(unit), str(category)]

    elif type_key == "idea":
        pri = (item.priority or "-")[:4].upper()
        return [str(item.id), title, status_emoji, pri]

    else:
        assignee = item.assignee or "-"
        return [str(item.id), title, status_emoji, assignee]
