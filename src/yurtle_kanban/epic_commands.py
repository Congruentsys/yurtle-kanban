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
from rich.table import Table

from ._click import Group, pull_note, refuse, safe
from ._logging import escape_nonprintable
from .models import PRIORITIES, WorkItemStatus, WorkItemType, fold_id, yaml_flow_list
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


def _links(related: object, epic_id: str) -> bool:
    """True when `related` lists epic_id under any spelling (`fold_id`, #868); a
    malformed `related:` such as a number or a mapping never does (#188)."""
    if not isinstance(related, list):
        return False
    return fold_id(epic_id) in {fold_id(str(r)) for r in related}


def _update_item_related(service, item_id: str, epic_id: str) -> bool:
    """Add epic_id to an item's related list in its frontmatter.

    Both IDs are looked up folded (#868); the link written is the epic's own ID.
    Returns True if the file was updated, False if epic_id was already present.
    """
    item = service.get_item(item_id)
    if item is None:
        console.print(f"[yellow]Warning: Item {safe(fold_id(item_id))} not found[/yellow]")
        return False
    try:  # which copy would get the link is ambiguous (#742, #754)
        service.refuse_duplicate(item, "a link")
    except ValueError as e:
        console.print(f"[yellow]Warning: {safe(e)}[/yellow]", soft_wrap=True)
        return False

    # keep the item file's own line endings (#151)
    content, eol = KanbanService._read_item_text(item.file_path)
    # the service's own frontmatter reader and writer (#169): they handle a
    # block-style `related:` list, `--- # comment` openers and continuation lines
    fm = service._parse_frontmatter(content)
    if not isinstance(fm, dict):
        reason = service._unparseable_reason(item.file_path, content)
        if reason is None:
            console.print(f"[yellow]Warning: No frontmatter in {safe(item.id)}[/yellow]")
        else:  # it's there but broken: say why, as the scan does (#139, #188)
            console.print(
                f"[yellow]Warning: {safe(item.id)}'s frontmatter doesn't parse "
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
            f"[yellow]Warning: {safe(item.id)}'s `related:` is a "
            f"{type(related).__name__}, not a list of IDs; not linked[/yellow]",
            soft_wrap=True,
        )
        return False
    related = [str(r) for r in related]

    if _links(related, epic_id):
        return False  # Already linked, under any spelling (#868)

    epic_item = service.get_item(epic_id)
    related.append(epic_item.id if epic_item is not None else epic_id)
    # the shared writer keeps elements like `"a, b"` one element (#121, #148) and
    # replaces the whole old value, block-list lines included (#169)
    new_content = service._add_or_update_frontmatter_field(
        content, "related", yaml_flow_list(related)
    )

    KanbanService._write_item_text(item.file_path, new_content, eol)
    item.related = related
    return True


def _already_linked(service, item_id: str, epic_id: str) -> bool:
    """True when the item's `related` already lists epic_id, folded (#868; a
    malformed `related:` such as a number or a mapping never does, #188)."""
    item = service.get_item(item_id)
    return item is not None and _links(item.related, epic_id)


# ---------------------------------------------------------------------------
# Shared implementation functions
# ---------------------------------------------------------------------------


def _do_create(title: str, priority: str, items: str | None, push: bool, group: str = "epic"):
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

    if push:
        # create_item_and_push returns {success, item, message}, not the item (#593)
        result = service.create_item_and_push(
            item_type=item_type,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )
        if not result.get("success") or result.get("item") is None:
            refuse(result.get("message", "push failed"), console=console)
        item = result["item"]
    else:
        item = service.create_item(
            item_type=item_type,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )

    console.print(
        f"Created {type_label} [bold green]{safe(item.id)}[/bold green]: "
        f"{safe(title)}"
    )
    item_ids = [i.strip() for i in items.split(",") if i.strip()] if items else []
    if push and not result.get("local", True):
        # landed on the remote's default branch, not in this checkout (#603): no link
        # to an epic this checkout doesn't have (#625)
        console.print(pull_note(result))
        if item_ids:
            console.print(
                f"[yellow]  Items not linked: after pulling, run "
                f"`{safe(group)} add {safe(item.id)} <item>` for "
                f"{safe(', '.join(item_ids))}[/yellow]"
            )
        return
    console.print(f"  File: {safe(str(item.file_path))}")

    if push and item_ids:
        console.print(
            f"[yellow]Warning: --push committed only the {safe(type_label.lower())}; the item "
            "links below are local changes. Commit and push them yourself.[/yellow]"
        )

    # Link items if provided
    if item_ids:
        for linked_id in item_ids:
            linked = service.get_item(linked_id)
            shown = linked.id if linked is not None else linked_id
            if _update_item_related(service, linked_id, item.id):
                console.print(f"  Linked {safe(shown)} → {safe(item.id)}")
            elif _already_linked(service, linked_id, item.id):
                console.print(f"  {safe(shown)} already linked")
            # otherwise _update_item_related printed why it didn't link


def _do_show(epic_id: str):
    """Show an epic/voyage with its linked items and progress."""
    service = _get_service()

    # the folded lookup and membership (#868): `epic-001`, a decomposed spelling
    epic_item = service.get_item(epic_id)
    if epic_item is None:
        raise click.ClickException(f"{escape_nonprintable(fold_id(epic_id))} not found")
    epic_id = epic_item.id
    folded_epic = fold_id(epic_id)

    # Find all items with this epic in their related list
    linked_items = [
        item
        for item in service._items.values()
        if fold_id(item.id) != folded_epic and _links(item.related, epic_id)
    ]
    linked_items.sort(key=lambda i: (-i.priority_score, i.id))

    # Also check if the epic itself lists items in its related field
    # (bidirectional linking)
    linked_ids = {fold_id(i.id) for i in linked_items}
    # a malformed `related:` (a mapping, #188) links nothing: its keys aren't IDs (#904)
    for rel_id in epic_item.related if isinstance(epic_item.related, list) else []:
        rel_item = service.get_item(str(rel_id))
        if rel_item is not None and fold_id(rel_item.id) not in linked_ids | {folded_epic}:
            linked_items.append(rel_item)
            linked_ids.add(fold_id(rel_item.id))

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
        f"\n[bold]{type_label} {safe(epic_item.id)}[/bold]: {safe(epic_item.title)} "
        f"[{color}]({epic_item.status.value})[/{color}]"
    )

    if not linked_items:
        cmd = "voyage" if epic_item.item_type == WorkItemType.VOYAGE else "epic"
        console.print("  No linked items found.")
        console.print(
            f"  [dim]Link items with: yurtle-kanban {cmd} add {safe(epic_id)} ITEM-ID[/dim]"
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
            safe(item.id),
            safe(item.title[:40]),
            status_str,
            safe(item.assignee or "-"),
            safe(item.priority or "medium"),
        )

    console.print(table)

    # Research interlinks for HDD items
    from .research_interlinks import has_research_items, render_research_interlinks

    if has_research_items(linked_items):
        render_research_interlinks(linked_items, console)


def _do_add(epic_id: str, item_id: str, push: bool = False):
    """Link an item to an epic/voyage by adding it to the item's related field.
    With `push`, one compare-and-swap commit on origin's item (#1251)."""
    service = _get_service()
    if push:  # every refusal comes back as an outcome (#825), as `update --push`
        outcome = service.link_related_push(fold_id(item_id), fold_id(epic_id))
        if (code := 8 if outcome.halted else int(outcome.exit_code)) != 0:
            refuse(outcome.message, console=console, exit_code=code)
        console.print(f"[green]{safe(outcome.message)}[/green]", soft_wrap=True)
        return

    # Verify epic exists: the folded lookup (#868)
    epic_item = service.get_item(epic_id)
    if epic_item is None:
        raise click.ClickException(f"{escape_nonprintable(fold_id(epic_id))} not found")
    epic_id = epic_item.id
    if (item := service.get_item(item_id)) is not None:
        try:  # refused, exit 1, before anything is written (#754)
            service.refuse_duplicate(item, "a link")
        except ValueError as e:
            raise click.ClickException(escape_nonprintable(str(e))) from None

    if _update_item_related(service, item_id, epic_id):
        console.print(f"Linked [bold]{safe(item.id)}[/bold] → [bold]{safe(epic_id)}[/bold]")
    else:
        if item is None:
            raise click.ClickException(f"Item {escape_nonprintable(fold_id(item_id))} not found")
        elif _already_linked(service, item_id, epic_id):
            console.print(f"{safe(item.id)} is already linked to {safe(epic_id)}")
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
@click.option(
    "--push",
    is_flag=True,
    help="Link the item as origin's default branch has it and push, as `update --push` does",
)
def epic_add(epic_id: str, item_id: str, push: bool):
    """Link an item to an epic."""
    _do_add(epic_id, item_id, push)


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
    _do_create(title, priority, items, push, group="voyage")


@voyage.command("show")
@click.argument("epic_id")
def voyage_show(epic_id: str):
    """Show a voyage with linked items and progress."""
    _do_show(epic_id)


@voyage.command("add")
@click.argument("epic_id")
@click.argument("item_id")
@click.option(
    "--push",
    is_flag=True,
    help="Link the item as origin's default branch has it and push, as `update --push` does",
)
def voyage_add(epic_id: str, item_id: str, push: bool):
    """Link an item to a voyage."""
    _do_add(epic_id, item_id, push)
