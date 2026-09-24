"""
Epic CLI commands for yurtle-kanban.

``epic`` is the primary command group for managing multi-item work groupings.
``voyage`` is a nautical-theme alias that behaves identically.

Both auto-detect the current theme and create the appropriate item type:
- Nautical theme → VOY- items (voyages)
- Software theme → EPIC- items (epics)
"""

from __future__ import annotations

import re
from datetime import date

import click
import yaml
from rich.console import Console
from rich.table import Table

from .models import PRIORITIES, WorkItemStatus, WorkItemType
from .template_engine import TemplateEngine

console = Console()

# Map theme names to their epic item type
_EPIC_TYPES: dict[str, WorkItemType] = {
    "nautical": WorkItemType.VOYAGE,
    "software": WorkItemType.EPIC,
}

_EPIC_TEMPLATES: dict[str, tuple[str, str]] = {
    # theme_name -> (template_theme, template_item_type)
    "nautical": ("nautical", "voyage"),
    "software": ("software", "epic"),
}

_TYPE_LABELS: dict[WorkItemType, str] = {
    WorkItemType.VOYAGE: "Voyage",
    WorkItemType.EPIC: "Epic",
}


def _get_service():
    """Get the kanban service for the current directory."""
    from .cli import get_service

    return get_service()


def _get_engine() -> TemplateEngine:
    """Get the template engine."""
    from .cli import _get_templates_dir

    return TemplateEngine(_get_templates_dir())


def _detect_epic_type(service) -> tuple[WorkItemType, str]:
    """Detect the epic/voyage item type from the current theme.

    Returns:
        (WorkItemType, theme_name) tuple.

    Raises:
        click.ClickException if no epic type for the current theme.
    """
    config = service.config

    # Check multi-board: look for a board with an epic-supporting theme
    if config.is_multi_board:
        for board in config.boards:
            preset = getattr(board, "preset", None)
            if preset in _EPIC_TYPES:
                return _EPIC_TYPES[preset], preset
        # Fall back to first board's preset
        first_preset = getattr(config.boards[0], "preset", None) if config.boards else None
        if first_preset in _EPIC_TYPES:
            return _EPIC_TYPES[first_preset], first_preset

    # Single-board: check theme
    theme_name = getattr(config, "theme", None)
    if theme_name in _EPIC_TYPES:
        return _EPIC_TYPES[theme_name], theme_name

    # Try loading theme config to get the name
    theme = config.get_theme()
    if theme:
        name = theme.get("theme", {}).get("name", "")
        if name in _EPIC_TYPES:
            return _EPIC_TYPES[name], name

    raise click.ClickException(
        "Epics are supported in 'nautical' (as voyages) and 'software' themes. "
        "Current theme does not support epics."
    )


def _update_item_related(service, item_id: str, epic_id: str) -> bool:
    """Add epic_id to an item's related list in its frontmatter.

    Returns True if the file was updated, False if epic_id was already present.
    """
    if not service._items:
        service.scan()
    item = service._items.get(item_id)
    if item is None:
        console.print(f"[yellow]Warning: Item {item_id} not found[/yellow]")
        return False

    content = item.file_path.read_text()
    # Match frontmatter: opening --- through closing ---
    frontmatter_match = re.match(r"^---\n(.*?\n)---", content, re.DOTALL)
    if not frontmatter_match:
        console.print(f"[yellow]Warning: No frontmatter in {item_id}[/yellow]")
        return False

    fm_text = frontmatter_match.group(1)
    fm_end = frontmatter_match.end()  # position of closing ---'s last char
    fm = yaml.safe_load(fm_text) or {}

    related = fm.get("related", [])
    if isinstance(related, str):
        related = [r.strip() for r in related.split(",")]

    if epic_id in related:
        return False  # Already linked

    related.append(epic_id)
    related_line = f"related: [{', '.join(related)}]"

    # Update the frontmatter in the file
    if re.search(r"^related:", fm_text, re.MULTILINE):
        # Replace existing related line
        new_content = re.sub(
            r"^(related:\s*).*$",
            f"\\g<1>[{', '.join(related)}]",
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        # Insert related before the closing --- using the match position
        # fm_end points to the end of "---\n...---", so the closing ---
        # starts at fm_end - 3
        close_pos = fm_end - 3
        new_content = content[:close_pos] + related_line + "\n" + content[close_pos:]

    item.file_path.write_text(new_content)
    item.related = related
    return True


# ---------------------------------------------------------------------------
# Shared implementation functions
# ---------------------------------------------------------------------------


def _do_create(title: str, priority: str, items: str | None, push: bool):
    """Create a new epic/voyage based on the current theme."""
    service = _get_service()
    item_type, theme_name = _detect_epic_type(service)

    engine = _get_engine()
    template_theme, template_type = _EPIC_TEMPLATES[theme_name]

    # Allocate ID
    prefix = service._get_type_prefix(item_type)
    next_num = service._get_next_id_number(prefix)
    item_id = f"{prefix}-{next_num:03d}"

    # Render template
    variables = {"id": item_id, "title": title, "date": date.today().isoformat()}
    try:
        content = engine.render(template_theme, template_type, variables)
    except FileNotFoundError:
        content = None

    if content:
        content = content.replace("{{NUMBER}}", str(next_num))
        content = content.replace("{{DATE}}", date.today().isoformat())
        content = content.replace("{{TITLE}}", title)

    type_label = _TYPE_LABELS.get(item_type, "Epic")

    if push and items:
        console.print(
            "[yellow]Warning: --items with --push only commits the epic file. "
            "Item link changes are local-only. Run 'git add' + 'git commit' "
            "to include them.[/yellow]"
        )

    if push:
        item = service.create_item_and_push(
            item_type=item_type,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )
    else:
        item = service.create_item(
            item_type=item_type,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )

    console.print(f"Created {type_label} [bold green]{item.id}[/bold green]: {title}")
    console.print(f"  File: {item.file_path}")

    # Link items if provided
    if items:
        item_ids = [i.strip() for i in items.split(",") if i.strip()]
        for linked_id in item_ids:
            if _update_item_related(service, linked_id, item.id):
                console.print(f"  Linked {linked_id} → {item.id}")
            else:
                console.print(f"  {linked_id} already linked or not found")


def _do_show(epic_id: str):
    """Show an epic/voyage with its linked items and progress."""
    service = _get_service()
    if not service._items:
        service.scan()

    epic_item = service._items.get(epic_id)
    if epic_item is None:
        raise click.ClickException(f"{epic_id} not found")

    # Find all items with this epic in their related list
    linked_items = [
        item
        for item in service._items.values()
        if epic_id in item.related and item.id != epic_id
    ]
    linked_items.sort(key=lambda i: (-i.priority_score, i.id))

    # Also check if the epic itself lists items in its related field
    # (bidirectional linking)
    linked_ids = {i.id for i in linked_items}
    for rel_id in epic_item.related:
        if rel_id not in linked_ids:
            rel_item = service._items.get(rel_id)
            if rel_item:
                linked_items.append(rel_item)

    status_colors = {
        WorkItemStatus.DONE: "green",
        WorkItemStatus.IN_PROGRESS: "cyan",
        WorkItemStatus.REVIEW: "yellow",
        WorkItemStatus.BLOCKED: "red",
        WorkItemStatus.BACKLOG: "dim",
        WorkItemStatus.READY: "blue",
    }

    type_label = _TYPE_LABELS.get(epic_item.item_type, "Epic")
    color = status_colors.get(epic_item.status, "white")
    console.print(
        f"\n[bold]{type_label} {epic_item.id}[/bold]: {epic_item.title} "
        f"[{color}]({epic_item.status.value})[/{color}]"
    )

    if not linked_items:
        cmd = "voyage" if epic_item.item_type == WorkItemType.VOYAGE else "epic"
        console.print("  No linked items found.")
        console.print(
            f"  [dim]Link items with: yurtle-kanban {cmd} add {epic_id} ITEM-ID[/dim]"
        )
        return

    # Progress summary
    done_count = sum(1 for i in linked_items if i.status == WorkItemStatus.DONE)
    total = len(linked_items)
    console.print(f"  Progress: {done_count}/{total} items complete\n")

    # Items table
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", width=14)
    table.add_column("Title", min_width=30)
    table.add_column("Status", width=12)
    table.add_column("Assignee", width=10)
    table.add_column("Priority", width=8)

    for item in linked_items:
        color = status_colors.get(item.status, "white")
        status_str = f"[{color}]{item.status.value}[/{color}]"
        table.add_row(
            item.id,
            item.title[:40],
            status_str,
            item.assignee or "-",
            item.priority or "medium",
        )

    console.print(table)

    # Research interlinks for HDD items
    from .research_interlinks import has_research_items, render_research_interlinks

    if has_research_items(linked_items):
        render_research_interlinks(linked_items, console)


def _do_add(epic_id: str, item_id: str):
    """Link an item to an epic/voyage by adding it to the item's related field."""
    service = _get_service()
    if not service._items:
        service.scan()

    # Verify epic exists
    if epic_id not in service._items:
        raise click.ClickException(f"{epic_id} not found")

    if _update_item_related(service, item_id, epic_id):
        console.print(f"Linked [bold]{item_id}[/bold] → [bold]{epic_id}[/bold]")
    else:
        item = service._items.get(item_id)
        if item is None:
            raise click.ClickException(f"Item {item_id} not found")
        else:
            console.print(f"{item_id} is already linked to {epic_id}")


# ---------------------------------------------------------------------------
# ``epic`` command group (primary)
# ---------------------------------------------------------------------------


@click.group()
def epic():
    """Manage epics — multi-item groupings of related work."""
    pass


@epic.command("create")
@click.argument("title")
@click.option(
    "--priority", "-p", default="high",
    type=click.Choice(PRIORITIES, case_sensitive=False),
    help="Priority",
)
@click.option("--items", help="Comma-separated item IDs to link")
@click.option("--push", is_flag=True, help="Commit and push (atomic)")
def epic_create(title: str, priority: str, items: str | None, push: bool):
    """Create a new epic (or voyage in nautical theme)."""
    _do_create(title, priority, items, push)


@epic.command("show")
@click.argument("epic_id")
def epic_show(epic_id: str):
    """Show an epic with linked items and progress."""
    _do_show(epic_id)


@epic.command("add")
@click.argument("epic_id")
@click.argument("item_id")
def epic_add(epic_id: str, item_id: str):
    """Link an item to an epic."""
    _do_add(epic_id, item_id)


# ---------------------------------------------------------------------------
# ``voyage`` command group (nautical alias)
# ---------------------------------------------------------------------------


@click.group()
def voyage():
    """Manage voyages — nautical alias for 'epic'."""
    pass


@voyage.command("create")
@click.argument("title")
@click.option(
    "--priority", "-p", default="high",
    type=click.Choice(PRIORITIES, case_sensitive=False),
    help="Priority",
)
@click.option("--items", help="Comma-separated item IDs to link")
@click.option("--push", is_flag=True, help="Commit and push (atomic)")
def voyage_create(title: str, priority: str, items: str | None, push: bool):
    """Create a new voyage (or epic in software theme)."""
    _do_create(title, priority, items, push)


@voyage.command("show")
@click.argument("epic_id")
def voyage_show(epic_id: str):
    """Show a voyage with linked items and progress."""
    _do_show(epic_id)


@voyage.command("add")
@click.argument("epic_id")
@click.argument("item_id")
def voyage_add(epic_id: str, item_id: str):
    """Link an item to a voyage."""
    _do_add(epic_id, item_id)
