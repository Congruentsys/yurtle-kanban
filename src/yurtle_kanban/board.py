"""
Terminal board rendering using the rich library.

Provides beautiful terminal-based kanban board visualization.
"""

from collections.abc import Callable
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ._click import safe
from .models import Board, WorkItem

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
    console.print(f"[bold]{safe(str(board.name))}[/bold]", justify="center")
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
            f"[{color}]{safe(str(col.name))}{wip_str}[/{color}]", width=25, overflow="fold"
        )

    # Group items by status
    # the same column→status lookup the header counts use (#87)
    items_by_status: dict[str, list[WorkItem]] = {
        col.id: board.get_column_items(col.id) for col in board.columns
    }

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
                    f"  - {safe(str(col.name))} ({safe(str(item_type))}): {count}/{limit}"
                )
            else:
                console.print(
                    f"  - {safe(str(col.name))}: {count}/{col.wip_limit}"
                )

    console.print()


def _safe_lines(text: str) -> str:
    """Multi-line repo text (a description, a comment) for Rich markup: each line
    through `safe()`, so ESC and other controls show as `\\x1b` but the text keeps
    its real line breaks (#1093)."""
    return "\n".join(safe(ln) for ln in text.split("\n"))


def render_card(item: WorkItem) -> Panel:
    """Render a single work item card."""
    # Build card content. The ID and title fold in a narrow column, so they stay
    # whole and a title is never cut mid-escape (#179); other lines wrap between
    # words and cut a word too long for the card with an ellipsis, so a tag or
    # assignee is never split across lines (#206)
    lines: list[Text] = []

    def line(markup: str, overflow: str = "ellipsis") -> None:
        lines.append(Text.from_markup(markup, overflow=overflow))

    # ID and type icon
    icon = TYPE_ICONS.get(item.item_type.value, "•")
    line(f"[dim]{icon}[/dim] [bold]{safe(item.id)}[/bold]", overflow="fold")

    # Title (truncate if too long)
    title = item.title
    if len(title) > 20:
        title = title[:17] + "..."
    line(safe(title), overflow="fold")

    # Priority badge
    if item.priority:
        color = PRIORITY_COLORS.get(item.priority, "white")
        line(f"[{color}]●[/{color}] {safe(str(item.priority))}")

    # Assignee
    if item.assignee:
        line(f"[dim]@{safe(str(item.assignee))}[/dim]")

    # Tags
    if item.tags:
        line(" ".join(f"[cyan]#{safe(str(t))}[/cyan]" for t in item.tags[:2]))

    content = Group(*lines)

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


def render_item_detail(
    item: WorkItem,
    console: Console | None = None,
    status_label: Callable[[WorkItem], str] | None = None,
    next_statuses: list[str] | None = None,
) -> None:
    """Render detailed view of a single work item; `next_statuses` (display names)
    adds a `Can move to` row (#573)."""
    if console is None:
        console = Console()

    icon = TYPE_ICONS.get(item.item_type.value, "•")

    console.print()
    console.print(
        Panel(
            f"[bold]{icon} {safe(item.id)}: {safe(item.title)}[/bold]",
            border_style="cyan",
        )
    )

    # Details table
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Field", style="dim")
    table.add_column("Value")

    table.add_row("Type", item.item_type.value)
    # the theme's name for it (hdd `draft`) when the caller knows the theme (#448)
    table.add_row("Status", safe(status_label(item) if status_label else item.status.value))
    if next_statuses is not None:
        table.add_row("Can move to", safe(", ".join(next_statuses) or "none"))
    table.add_row("Priority", safe(str(item.priority or "medium")))
    table.add_row("Assignee", safe(str(item.assignee or "unassigned")))
    if item.bounces:  # (#578)
        at = item.metadata.get("bounced_at")
        at = at.isoformat() if hasattr(at, "isoformat") else at
        by = item.metadata.get("bounced_by")
        table.add_row("Bounced", safe(f"Bounced {item.bounces}× (last by {by} at {at})"))
    if item.resolution:  # (#581)
        table.add_row("Resolution", safe(str(item.resolution)))
    if item.superseded_by:
        table.add_row("Superseded by", safe(", ".join(str(t) for t in item.superseded_by)))

    if item.created:
        table.add_row("Created", item.created.isoformat())

    if item.tags:
        table.add_row("Tags", safe(", ".join(str(t) for t in item.tags)))

    if item.depends_on:
        table.add_row("Depends On", safe(", ".join(str(d) for d in item.depends_on)))

    if item.graph and len(item.graph) > 0:
        table.add_row("Triples", str(len(item.graph)))

    table.add_row("File", safe(str(item.file_path)))

    console.print(table)

    # Description
    if item.description:
        console.print()
        console.print("[bold]Description[/bold]")
        console.print(Panel(_safe_lines(item.description), border_style="dim"))

    # Comments
    if item.comments:
        console.print()
        console.print("[bold]Comments[/bold]")
        for comment in item.comments:
            # text before the first heading has no date or author (#644)
            if comment.created_at is not None or comment.author:
                timestamp = (
                    comment.created_at.strftime("%Y-%m-%d %H:%M") if comment.created_at else ""
                )
                console.print(
                    f"  [dim]{timestamp}[/dim] [bold]{safe(str(comment.author))}[/bold]"
                )
            # every line at the text column, not just the first (#644)
            lines = str(comment.content).split("\n")
            text = "\n".join(f"    {ln}" if ln.strip() else "" for ln in lines)
            console.print(_safe_lines(text))

    console.print()


def _format_age(seconds: int | None) -> str:
    """An age for people: `3d 4h`, `5h 12m`, `7m`; `unknown` for None (#579)."""
    if seconds is None:
        return "unknown"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {rest // 60}m" if hours else f"{rest // 60}m"


def render_list(
    items: list[WorkItem],
    console: Console | None = None,
    status_label: Callable[[WorkItem], str] | None = None,
    ages: list[dict[str, Any]] | None = None,
) -> None:
    """Render a list of work items; `status_label` names each status the way the
    item's theme does (hdd `draft`), else the canonical value (#439). With `ages`
    (`KanbanService.aging`, one per item), Age and Since columns too (#579)."""
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
    if ages is not None:
        table.add_column("Age")
        table.add_column("Since", style="dim")

    for n, item in enumerate(items):
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

        cells = [
            f"{icon} {safe(item.id)}",
            safe(title),
            f"[{status_color}]"
            f"{safe(status_label(item) if status_label else item.status.value)}"
            f"[/{status_color}]",
            f"[{priority_color}]{safe(str(item.priority or 'medium'))}[/{priority_color}]",
            safe(assignee),
        ]
        if ages is not None:
            row = ages[n]
            since = f"{row['since'] or '-'} ({row['since_source']})"
            cells += [_format_age(row["age_seconds"]), safe(since)]
        table.add_row(*cells)

    console.print(table)


def render_roadmap(
    items: list[WorkItem],
    console: Console | None = None,
    by_type: bool = False,
    ranked: bool = False,
    status_label: Callable[[WorkItem], str] | None = None,
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
            console.print(f"\n[bold]{icon} {safe(type_name.title())}s[/bold]")
            _render_roadmap_table(group_items, console, ranked=ranked, status_label=status_label)
    else:
        _render_roadmap_table(items, console, ranked=ranked, status_label=status_label)

    console.print()


def _render_roadmap_table(
    items: list[WorkItem],
    console: Console,
    ranked: bool = False,
    status_label: Callable[[WorkItem], str] | None = None,
) -> None:
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
            safe(item.id),
            safe(title),
            f"[{priority_color}]{safe(str(item.priority or 'medium'))}[/{priority_color}]",
            f"[{status_color}]"
            f"{safe(status_label(item) if status_label else item.status.value)}"
            f"[/{status_color}]",
            safe(assignee),
        ])
        if ranked:
            summary = item.value_summary or ""
            if len(summary) > 35:
                summary = summary[:32] + "..."
            row.append(safe(summary))

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
                f"\n[bold]@{safe(str(assignee_name))}[/bold] "
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
                f"\n[bold]{icon} {safe(type_name.title())}s[/bold] "
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
                f"{safe(', '.join(str(a) for a in sorted(assignees)))}"
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

        table.add_row(safe(item.id), safe(title), completed, safe(assignee))

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
            f"[{color}]{safe(str(col.name))}[/{color}]",
            str(count),
            f"[{color}]{bar}[/{color}]",
        )

    table.add_row("", "", "")
    table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]", "")

    console.print(table)
    console.print()
