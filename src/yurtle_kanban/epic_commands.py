"""
Epic CLI commands for yurtle-kanban.

``epic`` is the primary command group for managing multi-item work groupings.
``voyage`` is a nautical-theme alias that behaves identically.

Both auto-detect the current theme and create the appropriate item type:
- Nautical theme → VOY- items (voyages)
- Software theme → EPIC- items (epics)
"""

from __future__ import annotations

from datetime import date

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ._click import Group, safe
from .models import PRIORITIES, WorkItemStatus, WorkItemType, yaml_flow_list
from .service import KanbanService
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
        console.print(f"[yellow]Warning: Item {safe(item_id)} not found[/yellow]")
        return False

    # keep the item file's own line endings (#151)
    content, eol = KanbanService._read_item_text(item.file_path)
    # the service's own frontmatter reader and writer (#169): they handle a
    # block-style `related:` list, `--- # comment` openers and continuation lines
    fm = service._parse_frontmatter(content)
    if not isinstance(fm, dict):
        reason = service._unparseable_reason(item.file_path, content)
        if reason is None:
            console.print(f"[yellow]Warning: No frontmatter in {safe(item_id)}[/yellow]")
        else:  # it's there but broken: say why, as the scan does (#139, #188)
            console.print(
                f"[yellow]Warning: {safe(item_id)}'s frontmatter doesn't parse "
                f"({safe(reason)}); not linked[/yellow]",
                soft_wrap=True,
            )
        return False

    related = fm.get("related") or []  # `related: null` / empty → []
    if isinstance(related, str):
        related = [r.strip() for r in related.split(",") if r.strip()]
    elif not isinstance(related, list):
        # a mapping or a number isn't a list of IDs; writing it back as
        # `["{...}"]` would corrupt it (#188)
        console.print(
            f"[yellow]Warning: {safe(item_id)}'s `related:` is a "
            f"{type(related).__name__}, not a list of IDs; not linked[/yellow]",
            soft_wrap=True,
        )
        return False
    related = [str(r) for r in related]

    if epic_id in related:
        return False  # Already linked

    related.append(epic_id)
    # the shared writer keeps elements like `"a, b"` one element (#121, #148) and
    # replaces the whole old value, block-list lines included (#169)
    new_content = service._add_or_update_frontmatter_field(
        content, "related", yaml_flow_list(related)
    )

    KanbanService._write_item_text(item.file_path, new_content, eol)
    item.related = related
    return True


def _already_linked(service, item_id: str, epic_id: str) -> bool:
    """True when the item's `related` already lists epic_id (a malformed
    `related:` such as a number or a mapping never does, #188)."""
    item = service._items.get(item_id)
    related = item.related if item is not None else None
    return isinstance(related, list) and epic_id in related


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

    console.print(
        f"Created {type_label} [bold green]{escape(item.id)}[/bold green]: "
        f"{escape(title)}"
    )
    console.print(f"  File: {escape(str(item.file_path))}")

    # Link items if provided
    if items:
        item_ids = [i.strip() for i in items.split(",") if i.strip()]
        for linked_id in item_ids:
            if _update_item_related(service, linked_id, item.id):
                console.print(f"  Linked {escape(linked_id)} → {escape(item.id)}")
            elif _already_linked(service, linked_id, item.id):
                console.print(f"  {escape(linked_id)} already linked")
            # otherwise _update_item_related printed why it didn't link


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
        f"\n[bold]{type_label} {escape(epic_item.id)}[/bold]: {escape(epic_item.title)} "
        f"[{color}]({epic_item.status.value})[/{color}]"
    )

    if not linked_items:
        cmd = "voyage" if epic_item.item_type == WorkItemType.VOYAGE else "epic"
        console.print("  No linked items found.")
        console.print(
            f"  [dim]Link items with: yurtle-kanban {cmd} add {escape(epic_id)} ITEM-ID[/dim]"
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
            escape(item.id),
            escape(item.title[:40]),
            status_str,
            escape(str(item.assignee or "-")),
            escape(str(item.priority or "medium")),
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
        console.print(f"Linked [bold]{escape(item_id)}[/bold] → [bold]{escape(epic_id)}[/bold]")
    else:
        item = service._items.get(item_id)
        if item is None:
            raise click.ClickException(f"Item {item_id} not found")
        elif _already_linked(service, item_id, epic_id):
            console.print(f"{escape(item_id)} is already linked to {escape(epic_id)}")
        # otherwise _update_item_related printed why it didn't link


# ---------------------------------------------------------------------------
# ``epic`` command group (primary)
# ---------------------------------------------------------------------------


@click.group(cls=Group)
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


@click.group(cls=Group)
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
