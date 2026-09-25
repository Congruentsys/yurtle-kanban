"""
Terminal board rendering using the rich library.

Provides beautiful terminal-based kanban board visualization.
"""

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .models import Board, WorkItem, WorkItemStatus

# Priority colors
PRIORITY_COLORS = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "blue",
    "backlog": "dim",
}

# Status colors for column headers
STATUS_COLORS = {
    "backlog": "dim white",
    "ready": "cyan",
    "in_progress": "green",
    "review": "yellow",
    "done": "bright_green",
    "blocked": "red",
    # Nautical theme
    "harbor": "dim white",
    "provisioning": "cyan",
    "underway": "green",
    "approaching": "yellow",
    "arrived": "bright_green",
}

# Type icons
TYPE_ICONS = {
    "feature": "+",
    "bug": "!",
    "epic": "E",
    "issue": "#",
    "task": "T",
    "idea": "?",
    # Nautical theme
    "expedition": "X",
    "voyage": "V",
    "directive": "D",
    "hazard": "!",
    "signal": "S",
}


def render_board(board: Board, console: Console | None = None) -> None:
    """Render a kanban board to the terminal."""
    if console is None:
        console = Console()

    # Title
    console.print()
    console.print(f"[bold]{escape(str(board.name))}[/bold]", justify="center")
    console.print()

    # Get column counts
    column_counts = board.get_column_counts()

    # Create table
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=True,
    )

    # Add columns
    for col in board.columns:
        count = column_counts.get(col.id, 0)
        wip_str = ""
        if col.type_wip_limits is not None:
            # Per-type limits: show aggregate count only
            wip_str = f" ({count})"
        elif col.wip_limit:
            if count > col.wip_limit:
                wip_str = f" [red]({count}/{col.wip_limit})[/red]"
            else:
                wip_str = f" ({count}/{col.wip_limit})"
        else:
            wip_str = f" ({count})"

        color = STATUS_COLORS.get(col.id, "white")
        # fold, don't crop: a narrow column cut `[bold]x[/bold]` to `[bold]x[…` (#179)
        table.add_column(
            f"[{color}]{escape(str(col.name))}{wip_str}[/{color}]", width=25, overflow="fold"
        )

    # Group items by status
    items_by_status: dict[str, list[WorkItem]] = {}
    for col in board.columns:
        try:
            status = WorkItemStatus.from_string(col.id)
            items_by_status[col.id] = board.get_items_by_status(status)
        except ValueError:
            items_by_status[col.id] = []

    # Find max items in any column
    max_items = max(len(items) for items in items_by_status.values()) if items_by_status else 0

    # Add rows
    for i in range(max(max_items, 1)):
        row = []
        for col in board.columns:
            items = items_by_status.get(col.id, [])
            if i < len(items):
                row.append(render_card(items[i]))
            else:
                row.append("")
        table.add_row(*row)

    console.print(table)

    # Show WIP violations
    violations = board.get_wip_violations()
    if violations:
        console.print()
        console.print("[bold red]WIP Limit Violations:[/bold red]")
        for col, count, item_type in violations:
            if item_type:
                limit = col.get_wip_limit(item_type)
                console.print(
                    f"  - {escape(str(col.name))} ({escape(str(item_type))}): {count}/{limit}"
                )
            else:
                console.print(
                    f"  - {escape(str(col.name))}: {count}/{col.wip_limit}"
                )

    console.print()


def render_card(item: WorkItem) -> Panel:
    """Render a single work item card."""
    # Build card content
    lines = []

    # ID and type icon
    icon = TYPE_ICONS.get(item.item_type.value, "•")
    lines.append(f"[dim]{icon}[/dim] [bold]{escape(item.id)}[/bold]")

    # Title (truncate if too long)
    title = item.title
    if len(title) > 20:
        title = title[:17] + "..."
    lines.append(escape(title))

    # Priority badge
    if item.priority:
        color = PRIORITY_COLORS.get(item.priority, "white")
        lines.append(f"[{color}]●[/{color}] {escape(str(item.priority))}")

    # Assignee
    if item.assignee:
        lines.append(f"[dim]@{escape(str(item.assignee))}[/dim]")

    # Tags
    if item.tags:
        tag_str = " ".join(f"[cyan]#{escape(str(t))}[/cyan]" for t in item.tags[:2])
        lines.append(tag_str)

    content = "\n".join(lines)

    # Determine border color based on priority
    border_color = "white"
    if item.priority == "critical":
        border_color = "red"
    elif item.priority == "high":
        border_color = "yellow"
    elif item.is_blocked:
        border_color = "red"

    return Panel(
        content,
        border_style=border_color,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def render_item_detail(item: WorkItem, console: Console | None = None) -> None:
    """Render detailed view of a single work item."""
    if console is None:
        console = Console()

    icon = TYPE_ICONS.get(item.item_type.value, "•")

    console.print()
    console.print(
        Panel(
            f"[bold]{icon} {escape(item.id)}: {escape(item.title)}[/bold]",
            border_style="cyan",
        )
    )

    # Details table
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Field", style="dim")
    table.add_column("Value")

    table.add_row("Type", item.item_type.value)
    table.add_row("Status", item.status.value)
    table.add_row("Priority", escape(str(item.priority or "medium")))
    table.add_row("Assignee", escape(str(item.assignee or "unassigned")))

    if item.created:
        table.add_row("Created", item.created.isoformat())

    if item.tags:
        table.add_row("Tags", escape(", ".join(str(t) for t in item.tags)))

    if item.depends_on:
        table.add_row("Depends On", escape(", ".join(str(d) for d in item.depends_on)))

    if item.graph and len(item.graph) > 0:
        table.add_row("Triples", str(len(item.graph)))

    table.add_row("File", escape(str(item.file_path)))

    console.print(table)

    # Description
    if item.description:
        console.print()
        console.print("[bold]Description[/bold]")
        console.print(Panel(escape(item.description), border_style="dim"))

    # Comments
    if item.comments:
        console.print()
        console.print("[bold]Comments[/bold]")
        for comment in item.comments:
            timestamp = comment.created_at.strftime("%Y-%m-%d %H:%M")
            console.print(f"  [dim]{timestamp}[/dim] [bold]{escape(str(comment.author))}[/bold]")
            console.print(f"    {escape(str(comment.content))}")

    console.print()


def render_list(items: list[WorkItem], console: Console | None = None) -> None:
    """Render a list of work items."""
    if console is None:
        console = Console()

    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold",
    )

    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Priority")
    table.add_column("Assignee", style="dim")

    for item in items:
        icon = TYPE_ICONS.get(item.item_type.value, "•")
        priority_color = PRIORITY_COLORS.get(item.priority or "medium", "white")
        status_color = STATUS_COLORS.get(item.status.value, "white")

        title = item.title
        if len(title) > 40:
            title = title[:37] + "..."

        # Handle assignee as string or list
        assignee = item.assignee
        if isinstance(assignee, list):
            assignee = ", ".join(str(a) for a in assignee if a)
        assignee = assignee or "-"

        table.add_row(
            f"{icon} {escape(item.id)}",
            escape(title),
            f"[{status_color}]{item.status.value}[/{status_color}]",
            f"[{priority_color}]{escape(str(item.priority or 'medium'))}[/{priority_color}]",
            escape(assignee),
        )

    console.print(table)


def render_roadmap(
    items: list[WorkItem],
    console: Console | None = None,
    by_type: bool = False,
    ranked: bool = False,
) -> None:
    """Render a prioritized roadmap view."""
    if console is None:
        console = Console()

    console.print()
    if ranked:
        console.print("[bold]Priority Queue[/bold] (Captain's rank order)")
    else:
        console.print("[bold]Roadmap[/bold]")
    console.print()

    if not items:
        console.print("[dim]No items to show.[/dim]")
        return

    if by_type:
        # Group by type
        groups: dict[str, list[WorkItem]] = {}
        for item in items:
            key = item.item_type.value
            groups.setdefault(key, []).append(item)

        for type_name, group_items in groups.items():
            icon = TYPE_ICONS.get(type_name, "•")
            console.print(f"\n[bold]{icon} {escape(type_name.title())}s[/bold]")
            _render_roadmap_table(group_items, console, ranked=ranked)
    else:
        _render_roadmap_table(items, console, ranked=ranked)

    console.print()


def _render_roadmap_table(items: list[WorkItem], console: Console, ranked: bool = False) -> None:
    """Render a roadmap table for a group of items."""
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")

    if ranked:
        table.add_column("Rank", style="bold yellow", width=4)
    table.add_column("#", style="dim", width=3)
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Priority")
    table.add_column("Status")
    table.add_column("Assignee", style="dim")
    if ranked:
        table.add_column("Value Summary", style="dim italic", max_width=35)

    for i, item in enumerate(items, 1):
        priority_color = PRIORITY_COLORS.get(item.priority or "medium", "white")
        status_color = STATUS_COLORS.get(item.status.value, "white")

        title = item.title
        if len(title) > 45:
            title = title[:42] + "..."

        assignee = item.assignee
        if isinstance(assignee, list):
            assignee = ", ".join(str(a) for a in assignee if a)
        assignee = assignee or "-"

        row = []
        if ranked:
            rank_str = str(item.priority_rank) if item.priority_rank is not None else "-"
            row.append(rank_str)
        row.extend([
            str(i),
            escape(item.id),
            escape(title),
            f"[{priority_color}]{escape(str(item.priority or 'medium'))}[/{priority_color}]",
            f"[{status_color}]{item.status.value}[/{status_color}]",
            escape(assignee),
        ])
        if ranked:
            summary = item.value_summary or ""
            if len(summary) > 35:
                summary = summary[:32] + "..."
            row.append(escape(summary))

        table.add_row(*row)

    console.print(table)


def render_history(
    items: list[WorkItem],
    console: Console | None = None,
    by_assignee: bool = False,
    by_type: bool = False,
) -> None:
    """Render completed work history."""
    if console is None:
        console = Console()

    console.print()
    console.print("[bold]Work History (Done)[/bold]")
    console.print()

    if not items:
        console.print("[dim]No completed items found.[/dim]")
        return

    if by_assignee:
        groups: dict[str, list[WorkItem]] = {}
        for item in items:
            key = item.assignee or "unassigned"
            groups.setdefault(key, []).append(item)

        for assignee_name, group_items in groups.items():
            console.print(
                f"\n[bold]@{escape(str(assignee_name))}[/bold] "
                f"({len(group_items)} items)"
            )
            _render_history_table(group_items, console)
    elif by_type:
        groups2: dict[str, list[WorkItem]] = {}
        for item in items:
            key = item.item_type.value
            groups2.setdefault(key, []).append(item)

        for type_name, group_items in groups2.items():
            icon = TYPE_ICONS.get(type_name, "•")
            console.print(
                f"\n[bold]{icon} {escape(type_name.title())}s[/bold] "
                f"({len(group_items)} items)"
            )
            _render_history_table(group_items, console)
    else:
        _render_history_table(items, console)

    # Summary stats
    console.print()
    console.print(f"[bold]Total completed:[/bold] {len(items)}")
    if items:
        assignees = set(i.assignee for i in items if i.assignee)
        if assignees:
            console.print(
                "[bold]Contributors:[/bold] "
                f"{escape(', '.join(str(a) for a in sorted(assignees)))}"
            )

    console.print()


def _render_history_table(items: list[WorkItem], console: Console) -> None:
    """Render a history table for a group of items."""
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Completed", style="dim")
    table.add_column("Assignee", style="dim")

    for item in items:
        title = item.title
        if len(title) > 45:
            title = title[:42] + "..."

        completed = ""
        if item.updated:
            completed = item.updated.strftime("%Y-%m-%d")
        elif item.created:
            completed = str(item.created)

        assignee = item.assignee
        if isinstance(assignee, list):
            assignee = ", ".join(str(a) for a in assignee if a)
        assignee = assignee or "-"

        table.add_row(escape(item.id), escape(title), completed, escape(assignee))

    console.print(table)


def render_stats(board: Board, console: Console | None = None) -> None:
    """Render board statistics."""
    if console is None:
        console = Console()

    counts = board.get_column_counts()
    total = sum(counts.values())

    console.print()
    console.print("[bold]Board Statistics[/bold]")
    console.print()

    # Status breakdown
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Status")
    table.add_column("Count", justify="right")
    table.add_column("Bar")

    max_count = max(counts.values()) if counts else 1

    for col in board.columns:
        count = counts.get(col.id, 0)
        bar_width = int((count / max_count) * 20) if max_count > 0 else 0
        bar = "█" * bar_width
        color = STATUS_COLORS.get(col.id, "white")

        table.add_row(
            f"[{color}]{escape(str(col.name))}[/{color}]",
            str(count),
            f"[{color}]{bar}[/{color}]",
        )

    table.add_row("", "", "")
    table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]", "")

    console.print(table)
    console.print()
