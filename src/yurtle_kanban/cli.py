"""
yurtle-kanban CLI

File-based kanban using Yurtle (Turtle RDF in Markdown). Git is your database.

Usage:
    yurtle-kanban init [--theme THEME] [--path PATH]
    yurtle-kanban list [--status STATUS] [--type TYPE] [--assignee ASSIGNEE]
    yurtle-kanban create TYPE TITLE [--priority PRIORITY] [--assignee ASSIGNEE] [--push]
    yurtle-kanban move ID STATUS
    yurtle-kanban show ID
    yurtle-kanban board
    yurtle-kanban stats
    yurtle-kanban rank ID RANK [--summary TEXT]
    yurtle-kanban roadmap [--by-type] [--type TYPE] [--ranked] [--export md]
    yurtle-kanban history [--week] [--month] [--since DATE] [--by-assignee]
    yurtle-kanban next [--assignee ASSIGNEE]
    yurtle-kanban export --format FORMAT [--output FILE]
"""

import json
import shutil
import sys
from pathlib import Path

import click
from rich.console import Console

from .board import (
    render_board,
    render_history,
    render_item_detail,
    render_list,
    render_roadmap,
    render_stats,
)
from .config import KanbanConfig
from .epic_commands import epic, voyage
from .export import export_expedition_index, export_html, export_json, export_markdown, export_research_index
from .hdd_commands import experiment, hdd, hypothesis, idea, literature, measure, paper
from .models import WorkItemStatus, WorkItemType
from .service import KanbanService


def _get_shared_data_dir(subdir: str) -> Path:
    """Get the path to a shared data directory (templates, skills, themes).

    Searches in priority order:
    1. Package share directory (pip installed via Hatchling → share/yurtle-kanban/)
    2. Development source repo (subdir at repo root)
    3. Fallback relative to this file
    """
    import sys

    # Priority 1: sys.prefix (standard for venv/conda — Hatchling installs here)
    share_path = Path(sys.prefix) / "share" / "yurtle-kanban" / subdir
    if share_path.exists():
        return share_path

    # Priority 2: Development source repo (subdir at repo root)
    try:
        import yurtle_kanban

        package_dir = Path(yurtle_kanban.__file__).parent.parent.parent
        dev_dir = package_dir / subdir
        if dev_dir.exists():
            return dev_dir
    except Exception:
        pass

    # Fallback: relative to this file
    return Path(__file__).parent.parent.parent / subdir


def _get_templates_dir() -> Path:
    """Get the path to the templates directory in the package."""
    return _get_shared_data_dir("templates")


def _get_skills_dir() -> Path:
    """Get the path to the skills directory in the package."""
    return _get_shared_data_dir("skills")


console = Console()


def get_service() -> KanbanService:
    """Get the kanban service for the current directory."""
    repo_root = Path.cwd()

    # Check for config in priority order:
    # 1. .yurtle-kanban/config.yaml (preferred)
    # 2. .kanban/config.yaml (legacy/fallback)
    config_paths = [
        repo_root / ".yurtle-kanban" / "config.yaml",
        repo_root / ".kanban" / "config.yaml",
    ]

    config_path = None
    for path in config_paths:
        if path.exists():
            config_path = path
            break

    if config_path:
        config = KanbanConfig.load(config_path)
    else:
        config = KanbanConfig()  # Use defaults

    return KanbanService(config, repo_root)


@click.group()
@click.version_option(package_name="yurtle-kanban")
def main():
    """File-based kanban using Yurtle. Git is your database."""
    pass


def _generate_template(prefix: str, type_name: str, sections: list[str]) -> str:
    """Generate a _TEMPLATE.md file for an item type."""
    section_text = "\n\n".join(f"## {s}\n" for s in sections)
    return f"""---
id: {prefix}-XXX
title: ""
type: {type_name}
status: backlog
created: YYYY-MM-DD
priority: medium
assignee:
tags: []
related: []
---

# {prefix}-XXX: Title

{section_text}"""


# Template sections per item type
_TEMPLATE_SECTIONS: dict[str, list[str]] = {
    # Software theme
    "feature": ["Goal", "Acceptance Criteria"],
    "bug": ["Description", "Steps to Reproduce", "Expected Behavior", "Actual Behavior"],
    "epic": ["Goal", "Scope", "Milestones"],
    "issue": ["Description", "Context"],
    "task": ["Goal", "Steps", "Acceptance Criteria"],
    "idea": ["Description", "Motivation"],
    # Nautical theme
    "expedition": ["Context", "Plan", "Definition of Done"],
    "voyage": ["Vision", "Expeditions", "Success Criteria"],
    "chore": ["Description"],
    "hazard": ["Description", "Impact", "Mitigation"],
    "signal": ["Observation", "Potential Value"],
    # HDD theme
    "literature": ["Topic", "Search Strategy", "Key Findings", "Gaps", "References"],
    "paper": ["Abstract", "Introduction", "Methodology", "Results", "Conclusion"],
    "hypothesis": ["Hypothesis Statement", "Target", "Rationale", "Testable Predictions"],
    "experiment": ["Purpose", "Method", "Results", "Conclusion"],
    "measure": ["Description", "Specification", "Collection Method"],
}


@main.command()
@click.option("--theme", default="software", help="Theme: software, nautical, or custom")
@click.option("--path", default="work/", help="Path for work items")
def init(theme: str, path: str):
    """Initialize yurtle-kanban in the current directory."""
    from .config import _load_builtin_theme

    repo_root = Path.cwd()

    # Create .kanban directory structure
    kanban_dir = repo_root / ".kanban"
    kanban_dir.mkdir(exist_ok=True)
    (kanban_dir / "workflows").mkdir(exist_ok=True)
    (kanban_dir / "templates").mkdir(exist_ok=True)

    # Load theme to discover item types and paths
    theme_data = _load_builtin_theme(theme, repo_root)
    item_types = theme_data.get("item_types", {}) if theme_data else {}

    # Scaffold type-specific directories with templates
    scan_paths = []
    dirs_created = []
    for type_id, type_def in item_types.items():
        type_path = type_def.get("path")
        if type_path:
            type_dir = repo_root / type_path
            type_dir.mkdir(parents=True, exist_ok=True)
            scan_paths.append(type_path)
            dirs_created.append(type_path)

            # Create _TEMPLATE.md in each directory
            prefix = type_def.get("id_prefix", type_id[:4].upper())
            sections = _TEMPLATE_SECTIONS.get(type_id, ["Description"])
            template_path = type_dir / "_TEMPLATE.md"
            if not template_path.exists():
                template_path.write_text(_generate_template(prefix, type_id, sections))

    # Create config.yaml with auto-populated scan_paths
    scan_paths_yaml = "\n".join(f'    - "{p}"' for p in scan_paths)
    config_content = f"""# yurtle-kanban configuration
kanban:
  theme: {theme}

  paths:
    root: {path}
    scan_paths:
{scan_paths_yaml}

    ignore:
      - "**/archive/**"
      - "**/templates/**"
      - "**/_TEMPLATE*"
"""
    config_path = kanban_dir / "config.yaml"
    config_path.write_text(config_content)

    # Copy any additional theme templates
    templates_src = _get_templates_dir()
    theme_templates = templates_src / theme
    templates_copied = 0
    if theme_templates.exists():
        templates_dst = kanban_dir / "templates"
        for template_file in theme_templates.glob("*.md"):
            shutil.copy(template_file, templates_dst / template_file.name)
            templates_copied += 1

    # Create root work directory (fallback)
    work_dir = repo_root / path
    work_dir.mkdir(parents=True, exist_ok=True)

    # Install theme-matched Claude Code skills
    skills_src = _get_skills_dir()
    skills_installed = 0
    if skills_src.exists():
        skills_dst = repo_root / ".claude" / "skills"
        skills_dst.mkdir(parents=True, exist_ok=True)

        # Theme directories contain sub-skills (e.g., nautical/expedition/SKILL.md).
        # Detect theme dirs and their owned skills to avoid copying theme-specific
        # skills as theme-neutral (can happen with stale pip installs).
        theme_dirs: set[str] = set()
        theme_owned_skills: set[str] = set()
        for d in skills_src.iterdir():
            if d.is_dir() and any(sub.is_dir() for sub in d.iterdir()):
                theme_dirs.add(d.name)
                for sub in d.iterdir():
                    if sub.is_dir():
                        theme_owned_skills.add(sub.name)
        skip = theme_dirs | theme_owned_skills

        # Copy theme-neutral skills (sync, status, release, etc.)
        for skill_dir in skills_src.iterdir():
            if skill_dir.is_dir() and skill_dir.name not in skip:
                dst = skills_dst / skill_dir.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(skill_dir, dst)
                skills_installed += 1

        # Copy theme-specific skills
        theme_skills = skills_src / theme
        if theme_skills.exists():
            for skill_dir in theme_skills.iterdir():
                if skill_dir.is_dir():
                    dst = skills_dst / skill_dir.name
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(skill_dir, dst)
                    skills_installed += 1

    console.print(f"[green]Initialized yurtle-kanban with theme '{theme}'[/green]")
    console.print("  Config:  .kanban/config.yaml")
    if dirs_created:
        for d in dirs_created:
            console.print(f"  Created: {d} (with _TEMPLATE.md)")
    else:
        console.print(f"  Created: {path}")
    if templates_copied:
        console.print(f"  Copied:  {templates_copied} templates to .kanban/templates/")
    if skills_installed:
        console.print(
            f"  Skills:  {skills_installed} Claude Code skills installed to .claude/skills/"
        )
    console.print()
    console.print("Next steps:")
    example_type = list(item_types.keys())[0] if item_types else "feature"
    console.print(f"  1. Create work items: yurtle-kanban create {example_type} 'My item' --push")
    console.print("  2. View board: yurtle-kanban board")


@main.command("list")
@click.option("--status", "-s", help="Filter by status (backlog, ready, in_progress, review, done)")
@click.option("--type", "-t", "item_type", help="Filter by type (feature, bug, epic, task)")
@click.option("--assignee", "-a", help="Filter by assignee")
@click.option(
    "--priority", "-p",
    help="Filter by priority (critical, high, medium, low). Comma-separated.",
)
@click.option("--board", "-b", "board_name", help="Filter to a specific board (multi-board mode)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_items(
    status: str | None,
    item_type: str | None,
    assignee: str | None,
    priority: str | None,
    board_name: str | None,
    as_json: bool,
):
    """List work items."""
    service = get_service()

    # Parse filters
    status_filter = None
    if status:
        try:
            status_filter = WorkItemStatus.from_string(status)
        except ValueError:
            console.print(f"[red]Unknown status: {status}[/red]")
            sys.exit(1)

    type_filter = None
    if item_type:
        try:
            type_filter = WorkItemType.from_string(item_type)
        except ValueError:
            console.print(f"[red]Unknown type: {item_type}[/red]")
            sys.exit(1)

    priority_filter = None
    if priority:
        valid_priorities = {"critical", "high", "medium", "low"}
        priority_filter = [p.strip().lower() for p in priority.split(",")]
        invalid = [p for p in priority_filter if p not in valid_priorities]
        if invalid:
            console.print(
                f"[red]Unknown priority: {', '.join(invalid)}."
                " Valid: critical, high, medium, low[/red]"
            )
            sys.exit(1)

    items = service.get_items(
        status=status_filter,
        item_type=type_filter,
        assignee=assignee,
        board=board_name,
        priority=priority_filter,
    )

    if not items:
        console.print("[dim]No work items found.[/dim]")
        return

    if as_json:
        data = [item.to_dict() for item in items]
        click.echo(json.dumps(data, indent=2))
    else:
        render_list(items, console)


@main.command()
@click.argument("item_type")
@click.argument("title")
@click.option("--priority", "-p", default="medium", help="Priority: critical, high, medium, low")
@click.option("--assignee", "-a", help="Assignee")
@click.option("--description", "-d", help="Description")
@click.option("--tags", help="Comma-separated tags")
@click.option(
    "--push",
    is_flag=True,
    help="Atomic: allocate ID, create file, commit, and push (multi-agent safe)",
)
def create(
    item_type: str,
    title: str,
    priority: str,
    assignee: str | None,
    description: str | None,
    tags: str | None,
    push: bool,
):
    """Create a new work item.

    Use --push for multi-agent safety: fetches latest, allocates ID,
    creates the file, commits, and pushes in one atomic operation.
    If another agent pushed first, it retries with a new ID.

    Examples:
        yurtle-kanban create feature "Add dark mode"
        yurtle-kanban create expedition "Research vectors" --push
        yurtle-kanban create bug "Login crash" --push --assignee Mini
    """
    service = get_service()

    try:
        work_type = WorkItemType.from_string(item_type)
    except ValueError:
        console.print(f"[red]Unknown type: {item_type}[/red]")
        console.print(f"Valid types: {', '.join(t.value for t in WorkItemType)}")
        sys.exit(1)

    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    if push:
        result = service.create_item_and_push(
            item_type=work_type,
            title=title,
            priority=priority,
            assignee=assignee,
            description=description,
            tags=tag_list,
        )
        if result["success"]:
            item = result["item"]
            if result.get("pushed"):
                console.print(f"[green]Created and pushed {result['id']}: {title}[/green]")
                console.print(f"  File: {item.file_path}")
                console.print("[dim]  (committed and pushed to remote)[/dim]")
            else:
                console.print(f"[green]Created {result['id']}: {title}[/green]")
                console.print(f"  File: {item.file_path}")
                console.print("[dim]  (committed locally — no remote configured)[/dim]")
        else:
            console.print(f"[red]Failed: {result['message']}[/red]")
            sys.exit(1)
    else:
        item = service.create_item(
            item_type=work_type,
            title=title,
            priority=priority,
            assignee=assignee,
            description=description,
            tags=tag_list,
        )
        console.print(f"[green]Created {item.id}: {item.title}[/green]")
        console.print(f"  File: {item.file_path}")


@main.command()
@click.argument("item_id")
@click.argument("new_status")
@click.option("--no-commit", is_flag=True, help="Don't create git commit")
@click.option("--message", "-m", help="Custom commit message")
@click.option("--assign", "-a", help="Set assignee (e.g., 'Claude-M5', 'Claude-DGX')")
@click.option(
    "--export-board",
    "-e",
    help="Export board to file after move (e.g., 'kanban-work/KANBAN-BOARD.md')",
)
@click.option("--force", "-f", is_flag=True, help="Skip WIP limit and workflow validation")
@click.option("--closed-by", help="URI recording what triggered this move (e.g., PR URL)")
@click.option("--skip-gates", is_flag=True, help="Skip transition gate checks (Captain override)")
@click.option("--self-reviewed", is_flag=True, help="Confirm self-review was performed")
def move(
    item_id: str,
    new_status: str,
    no_commit: bool,
    message: str | None,
    assign: str | None,
    export_board: str | None,
    force: bool,
    closed_by: str | None,
    skip_gates: bool,
    self_reviewed: bool,
):
    """Move a work item to a new status.

    Examples:
        yurtle-kanban move EXP-123 in_progress
        yurtle-kanban move EXP-123 in_progress --assign "Claude-M5"
        yurtle-kanban move EXP-123 done --export-board kanban-work/KANBAN-BOARD.md
        yurtle-kanban move EXP-123 ready --force  # Skip WIP limit check
        yurtle-kanban move EXP-123 done --closed-by "https://github.com/repo/pull/42"
        yurtle-kanban move EXP-123 review --self-reviewed  # Pass self-review gate
        yurtle-kanban move EXP-123 review --skip-gates  # Skip all transition gates
    """
    service = get_service()

    try:
        status = WorkItemStatus.from_string(new_status)
    except ValueError:
        # Try resolving as a theme alias (e.g., "active" → "in_progress" for HDD)
        column_map = service._get_column_status_map()
        resolved = column_map.get(new_status.lower().replace("-", "_").replace(" ", "_"))
        if resolved:
            status = resolved
        else:
            console.print(f"[red]Unknown status: {new_status}[/red]")
            valid = sorted({s.value for s in WorkItemStatus} | set(column_map.keys()))
            console.print(f"Valid statuses: {', '.join(valid)}")
            sys.exit(1)

    # Build gate context from CLI flags
    gate_context: dict[str, object] = {}
    if self_reviewed:
        gate_context["self_reviewed"] = True

    try:
        item = service.move_item(
            item_id.upper(),
            status,
            commit=not no_commit,
            message=message,
            assignee=assign,
            skip_wip_check=force,
            validate_workflow=not force,
            closed_by=closed_by,
            skip_gates=skip_gates or force,
            gate_context=gate_context,
        )
        console.print(f"[green]Moved {item.id} to {status.value}[/green]")
        if assign:
            console.print(f"  Assigned to: {assign}")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

    # Export board if requested
    if export_board:
        board = service.get_board()
        content = export_expedition_index(board, min_id=600)
        Path(export_board).write_text(content)
        console.print(f"[green]Exported board to {export_board}[/green]")


@main.command()
@click.argument("item_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def show(item_id: str, as_json: bool):
    """Show details of a work item."""
    service = get_service()

    item = service.get_item(item_id.upper())
    if not item:
        if as_json:
            click.echo(json.dumps({"error": f"Item not found: {item_id}"}))
        else:
            console.print(f"[red]Item not found: {item_id}[/red]")
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(item.to_dict(), indent=2))
    else:
        render_item_detail(item, console)


@main.command()
@click.argument("board_name", required=False)
@click.option("--all", "show_all", is_flag=True, help="Show all boards (multi-board mode)")
@click.option("--epic", "epic_id", help="Filter to items linked to this epic/voyage")
def board(board_name: str | None, show_all: bool, epic_id: str | None):
    """Show the kanban board view.

    In multi-board mode, specify BOARD_NAME to view a specific board.
    Without arguments, shows the default board (or the board matching current directory).

    Examples:
        yurtle-kanban board                       # Show default/current board
        yurtle-kanban board research              # Show the 'research' board
        yurtle-kanban board development           # Show the 'development' board
        yurtle-kanban board --all                 # Show all boards
        yurtle-kanban board --epic VOY-108        # Show items linked to VOY-108
    """
    service = get_service()
    config = service.config

    if show_all and config.is_multi_board:
        # Show all boards
        for board_config in config.boards:
            console.print(f"\n[bold cyan]Board: {board_config.name}[/bold cyan]")
            console.print(f"  Preset: {board_config.preset}")
            console.print(f"  Path: {board_config.path}")
            board_data = service.get_board(board_name=board_config.name)
            if epic_id:
                board_data.items = [
                    i for i in board_data.items if epic_id in i.related
                ]
            render_board(board_data, console)
            console.print()
        return

    board_data = service.get_board(board_name=board_name)
    if epic_id:
        board_data.items = [i for i in board_data.items if epic_id in i.related]
    render_board(board_data, console)


@main.command("boards")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_boards(as_json: bool):
    """List all configured boards.

    Shows all boards in a multi-board configuration.
    For single-board configs, shows the default board.

    Examples:
        yurtle-kanban boards
        yurtle-kanban boards --json
    """
    service = get_service()
    config = service.config

    if not config.is_multi_board:
        # Single-board mode
        boards_info = [{
            "name": "default",
            "preset": config.theme,
            "path": config.paths.root or "work/",
            "default": True,
        }]
    else:
        boards_info = []
        for board_config in config.boards:
            boards_info.append({
                "name": board_config.name,
                "preset": board_config.preset,
                "path": board_config.path,
                "default": board_config.name == config.default_board,
                "wip_limits": board_config.wip_limits,
            })

    if as_json:
        data = {"boards": boards_info, "multi_board": config.is_multi_board}
        click.echo(json.dumps(data, indent=2))
        return

    if not config.is_multi_board:
        console.print(
            "[dim]Single-board mode. Use 'yurtle-kanban init"
            " --multi-board' to enable multi-board.[/dim]"
        )
        console.print()
        console.print("[bold]Board:[/bold] default")
        console.print(f"  Preset: {config.theme}")
        console.print(f"  Path: {config.paths.root or 'work/'}")
    else:
        console.print(f"[bold]Configured Boards ({len(config.boards)})[/bold]")
        console.print()
        for info in boards_info:
            default_marker = " [cyan](default)[/cyan]" if info.get("default") else ""
            console.print(f"  [bold]{info['name']}[/bold]{default_marker}")
            console.print(f"    Preset: {info['preset']}")
            console.print(f"    Path: {info['path']}")
            if info.get("wip_limits"):
                wip_str = ", ".join(f"{k}: {v}" for k, v in info["wip_limits"].items())
                console.print(f"    WIP Limits: {wip_str}")
            console.print()


@main.command("board-add")
@click.argument("name")
@click.option("--preset", "-p", default="software", help="Preset/theme to use (default: software)")
@click.option("--path", required=True, help="Path for board's work items")
@click.option(
    "--wip-limit", "-w", multiple=True,
    help="WIP limit as 'status:limit' (e.g., 'in_progress:3')",
)
@click.option("--default", "make_default", is_flag=True, help="Make this the default board")
def board_add(name: str, preset: str, path: str, wip_limit: tuple[str, ...], make_default: bool):
    """Add a new board to the configuration.

    If this is currently a single-board configuration, upgrades to multi-board mode.

    Examples:
        yurtle-kanban board-add research --preset hdd --path research/
        yurtle-kanban board-add development --preset nautical --path kanban-work/ --default
        yurtle-kanban board-add tasks --preset software --path work/ --wip-limit in_progress:3
    """
    from .config import BoardConfig, _load_builtin_theme

    service = get_service()
    config = service.config
    repo_root = service.repo_root

    # Validate preset exists
    if not _load_builtin_theme(preset, repo_root):
        available = ["software", "nautical", "spec", "hdd"]
        console.print(f"[red]Unknown preset: {preset}[/red]")
        console.print(f"[dim]Available presets: {', '.join(available)}[/dim]")
        sys.exit(1)

    # Parse WIP limits
    wip_limits = {}
    for wip in wip_limit:
        if ":" in wip:
            status, limit = wip.split(":", 1)
            try:
                wip_limits[status] = int(limit)
            except ValueError:
                console.print(f"[red]Invalid WIP limit: {wip}[/red]")
                sys.exit(1)

    # Check if board already exists
    if config.is_multi_board and config.get_board(name):
        console.print(f"[red]Board '{name}' already exists[/red]")
        sys.exit(1)

    # Create the new board config
    new_board = BoardConfig(
        name=name,
        preset=preset,
        path=path,
        wip_limits=wip_limits,
    )

    # Add to configuration
    config.add_board(new_board)

    # Set as default if requested
    if make_default:
        config.default_board = name

    # Save config
    config_path = repo_root / ".kanban" / "config.yaml"
    config.save(config_path)

    # Create the path directory if it doesn't exist
    board_path = repo_root / path
    if not board_path.exists():
        board_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]Created directory: {path}[/green]")

    console.print(f"[green]Added board '{name}'[/green]")
    console.print(f"  Preset: {preset}")
    console.print(f"  Path: {path}")
    if wip_limits:
        wip_str = ", ".join(f"{k}: {v}" for k, v in wip_limits.items())
        console.print(f"  WIP Limits: {wip_str}")
    if make_default:
        console.print("  [cyan]Set as default board[/cyan]")

    if not config.is_multi_board:
        console.print()
        console.print("[dim]Note: Configuration upgraded to multi-board mode.[/dim]")


@main.command()
def stats():
    """Show board statistics."""
    service = get_service()
    board = service.get_board()
    render_stats(board, console)


@main.command()
@click.option("--by-type", is_flag=True, help="Group by item type")
@click.option("--type", "-t", "item_type", help="Filter to a single item type")
@click.option("--ranked", is_flag=True, help="Sort by priority_rank (Captain's order)")
@click.option("--export", "-e", "export_fmt", type=click.Choice(["md"]), help="Export as markdown")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def roadmap(
    by_type: bool, item_type: str | None, ranked: bool,
    export_fmt: str | None, as_json: bool,
):
    """Show a prioritized roadmap of all work items.

    Displays all non-done items sorted by priority (critical first).
    Use --ranked to sort by Captain's priority_rank instead.
    Use --by-type to group by item type.

    Examples:
        yurtle-kanban roadmap
        yurtle-kanban roadmap --ranked
        yurtle-kanban roadmap --by-type
        yurtle-kanban roadmap --type expedition
        yurtle-kanban roadmap --export md
    """
    service = get_service()

    if ranked:
        # Use ranked ordering: priority_rank first, then priority_score
        items = service.get_ranked_items()
    else:
        # Get all non-done items (sorted by priority_score)
        items = service.get_items()
        items = [i for i in items if i.status != WorkItemStatus.DONE]

    # Optional type filter
    if item_type:
        try:
            type_filter = WorkItemType.from_string(item_type)
            items = [i for i in items if i.item_type == type_filter]
        except ValueError:
            console.print(f"[red]Unknown type: {item_type}[/red]")
            sys.exit(1)

    if as_json:
        data = [item.to_dict() for item in items]
        click.echo(json.dumps(data, indent=2))
    elif export_fmt == "md":
        lines = ["# Roadmap\n"]
        for i, item in enumerate(items, 1):
            priority = item.priority or "medium"
            assignee = item.assignee or "unassigned"
            if item.priority_rank is not None:
                rank_str = f" [rank:{item.priority_rank}]"
            else:
                rank_str = ""
            lines.append(
                f"{i}. **{item.id}**: {item.title} "
                f"[{priority}]{rank_str} "
                f"({item.status.value}) @{assignee}"
            )
        click.echo("\n".join(lines))
    else:
        render_roadmap(items, console, by_type=by_type, ranked=ranked)


@main.command()
@click.argument("item_id")
@click.argument("rank_number", type=int)
@click.option("--summary", "-s", help="Brief value statement for this item")
@click.option("--no-commit", is_flag=True, help="Don't create git commit")
def rank(item_id: str, rank_number: int, summary: str | None, no_commit: bool):
    """Set the priority rank for a work item.

    Lower rank = higher priority (1 = top of the queue).
    The Captain uses this to set the work order.

    Examples:
        yurtle-kanban rank EXP-1019 1
        yurtle-kanban rank EXP-1022 2 --summary "Unblocks Paper 127"
        yurtle-kanban rank CHORE-078 3
    """
    service = get_service()
    try:
        item = service.rank_item(
            item_id.upper(),
            rank_number,
            value_summary=summary,
            commit=not no_commit,
        )
        console.print(f"[green]Ranked {item.id} as #{rank_number}[/green]")
        if summary:
            console.print(f"  Value: {summary}")
        if item.priority:
            console.print(f"  Priority: {item.priority}")
        console.print(f"  Status: {item.status.value}")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)


@main.command()
@click.option("--since", help="Show items completed since date (YYYY-MM-DD)")
@click.option("--week", is_flag=True, help="Show items completed in the last 7 days")
@click.option("--month", is_flag=True, help="Show items completed in the last 30 days")
@click.option("--by-assignee", is_flag=True, help="Group by assignee")
@click.option("--by-type", is_flag=True, help="Group by item type")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def history(
    since: str | None,
    week: bool,
    month: bool,
    by_assignee: bool,
    by_type: bool,
    as_json: bool,
):
    """Show completed work history.

    Displays done items in reverse chronological order.
    Use time filters to narrow the range.

    Examples:
        yurtle-kanban history
        yurtle-kanban history --week
        yurtle-kanban history --since 2026-01-01
        yurtle-kanban history --by-assignee
    """
    from datetime import datetime, timedelta

    service = get_service()

    # Get done items
    items = service.get_items(status=WorkItemStatus.DONE)

    # Apply time filters
    cutoff = None
    if week:
        cutoff = datetime.now() - timedelta(days=7)
    elif month:
        cutoff = datetime.now() - timedelta(days=30)
    elif since:
        try:
            cutoff = datetime.fromisoformat(since)
        except ValueError:
            console.print(f"[red]Invalid date format: {since} (use YYYY-MM-DD)[/red]")
            sys.exit(1)

    if cutoff:
        filtered = []
        for item in items:
            if item.updated and item.updated >= cutoff:
                filtered.append(item)
            elif item.created and datetime.combine(item.created, datetime.min.time()) >= cutoff:
                filtered.append(item)
        items = filtered

    # Sort by updated date (most recent first)
    items.sort(key=lambda i: i.updated or datetime.min, reverse=True)

    if as_json:
        data = [item.to_dict() for item in items]
        click.echo(json.dumps(data, indent=2))
    else:
        render_history(items, console, by_assignee=by_assignee, by_type=by_type)


@main.command("next")
@click.option("--assignee", "-a", help="Filter by assignee")
def next_item(assignee: str | None):
    """Suggest the next item to work on."""
    service = get_service()

    item = service.suggest_next_item(assignee=assignee)

    if not item:
        console.print("[dim]No ready items to work on.[/dim]")
        return

    console.print("[bold]Suggested next item:[/bold]")
    render_item_detail(item, console)


@main.command()
@click.argument("item_id")
@click.argument("comment")
@click.option("--author", "-a", default="cli", help="Comment author")
def comment(item_id: str, comment: str, author: str):
    """Add a comment to a work item."""
    service = get_service()

    try:
        item = service.add_comment(item_id.upper(), comment, author)
        console.print(f"[green]Added comment to {item.id}[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@main.command()
def blocked():
    """List blocked items."""
    service = get_service()
    items = service.get_blocked_items()

    if not items:
        console.print("[green]No blocked items.[/green]")
        return

    console.print(f"[bold red]Blocked Items ({len(items)})[/bold red]")
    render_list(items, console)


@main.command()
@click.argument("item_id", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def metrics(item_id: str | None, as_json: bool):
    """Show flow metrics for an item or the board.

    Flow metrics track time spent in each status to help identify bottlenecks.
    Status history is automatically recorded when items move between statuses.

    Examples:
        yurtle-kanban metrics           # Board-wide metrics
        yurtle-kanban metrics EXP-123   # Metrics for a specific item
    """
    service = get_service()

    if item_id:
        # Single item metrics
        metrics_data = service.get_flow_metrics(item_id.upper())

        if "error" in metrics_data:
            console.print(f"[yellow]{metrics_data['error']}[/yellow]")
            console.print("[dim]Status history is recorded when items move between statuses.[/dim]")
            return

        if as_json:
            click.echo(json.dumps(metrics_data, indent=2, default=str))
        else:
            console.print(f"[bold]Flow Metrics: {item_id.upper()}[/bold]")
            console.print()

            if metrics_data.get("cycle_time_hours"):
                hours = metrics_data["cycle_time_hours"]
                if hours < 24:
                    console.print(f"  Cycle Time: {hours:.1f} hours")
                else:
                    console.print(f"  Cycle Time: {hours / 24:.1f} days")

            if metrics_data.get("lead_time_hours"):
                hours = metrics_data["lead_time_hours"]
                if hours < 24:
                    console.print(f"  Lead Time: {hours:.1f} hours")
                else:
                    console.print(f"  Lead Time: {hours / 24:.1f} days")

            console.print(f"  Transitions: {metrics_data['transitions']}")
            console.print()

            if metrics_data.get("time_in_status"):
                console.print("  [bold]Time in Status:[/bold]")
                for status, hours in sorted(metrics_data["time_in_status"].items()):
                    if hours < 24:
                        console.print(f"    {status}: {hours:.1f} hours")
                    else:
                        console.print(f"    {status}: {hours / 24:.1f} days")
    else:
        # Board-wide metrics
        metrics_data = service.get_board_metrics()

        if as_json:
            click.echo(json.dumps(metrics_data, indent=2, default=str))
        else:
            console.print("[bold]Board Flow Metrics[/bold]")
            console.print()
            console.print(f"  Total Items: {metrics_data['total_items']}")
            console.print(f"  Items with History: {metrics_data['items_with_history']}")

            if metrics_data.get("avg_cycle_time_hours"):
                hours = metrics_data["avg_cycle_time_hours"]
                if hours < 24:
                    console.print(f"  Avg Cycle Time: {hours:.1f} hours")
                else:
                    console.print(f"  Avg Cycle Time: {hours / 24:.1f} days")

            if metrics_data.get("avg_lead_time_hours"):
                hours = metrics_data["avg_lead_time_hours"]
                if hours < 24:
                    console.print(f"  Avg Lead Time: {hours:.1f} hours")
                else:
                    console.print(f"  Avg Lead Time: {hours / 24:.1f} days")

            if not metrics_data.get("items_with_history"):
                console.print()
                console.print(
                    "[dim]No status history yet. History is recorded when items move.[/dim]"
                )


@main.command("export")
@click.option(
    "--format",
    "-f",
    "fmt",
    required=True,
    type=click.Choice(["html", "markdown", "json", "expedition-index", "research-index"]),
    help="Export format",
)
@click.option("--output", "-o", help="Output file (default: stdout)")
@click.option("--min-id", default=600, help="Minimum ID for Work Trail (expedition-index only)")
@click.option("--board", "-b", "board_name", help="Board to export (multi-board mode)")
def export_cmd(fmt: str, output: str | None, min_id: int, board_name: str | None):
    """Export the board to various formats.

    Formats:
    - html: Standalone HTML board view
    - markdown: Simple markdown table
    - json: JSON for integrations
    - expedition-index: Enhanced index with Work Trail and Dependency Tree
    - research-index: Research board index grouped by type (papers, hypotheses, etc.)
    """
    service = get_service()
    board = service.get_board(board_name)

    if fmt == "html":
        content = export_html(board)
    elif fmt == "markdown":
        content = export_markdown(board)
    elif fmt == "json":
        content = export_json(board)
    elif fmt == "expedition-index":
        content = export_expedition_index(board, min_id=min_id)
    elif fmt == "research-index":
        content = export_research_index(board)
    else:
        console.print(f"[red]Unknown format: {fmt}[/red]")
        sys.exit(1)

    if output:
        Path(output).write_text(content)
        console.print(f"[green]Exported to {output}[/green]")
    else:
        click.echo(content)


@main.command("next-id")
@click.argument("prefix")
@click.option("--no-sync", is_flag=True, help="Skip git fetch/push (local only)")
@click.option("--no-commit", is_flag=True, help="Don't commit the allocation")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def next_id(prefix: str, no_sync: bool, no_commit: bool, as_json: bool):
    """Allocate the next available ID for a prefix.

    This command prevents duplicate IDs when multiple agents create work items
    concurrently by:
    1. Fetching latest changes from remote
    2. Scanning all files to find the highest ID
    3. Committing and pushing an allocation lock file

    Examples:
        yurtle-kanban next-id EXP       # Allocate next expedition ID
        yurtle-kanban next-id FEAT      # Allocate next feature ID
        yurtle-kanban next-id EXP --no-sync  # Local only (no git operations)
    """
    service = get_service()

    result = service.allocate_next_id(
        prefix=prefix,
        sync_remote=not no_sync,
        commit_allocation=not no_commit,
    )

    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        if result["success"]:
            console.print(f"[green]Allocated: {result['id']}[/green]")
            console.print(f"  Prefix: {result['prefix']}")
            console.print(f"  Number: {result['number']}")
            if not no_sync:
                console.print("[dim]  (committed and pushed to remote)[/dim]")
        else:
            console.print(f"[red]Failed to allocate ID: {result['message']}[/red]")
            sys.exit(1)


@main.command()
@click.option("--fix", is_flag=True, help="Attempt to fix issues (rename files)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate(fix: bool, as_json: bool):
    """Validate work items for consistency issues.

    Checks:
    - File name matches ID in frontmatter
    - No duplicate IDs
    - Required fields present (id, title, status, type)
    """
    service = get_service()
    items = service.get_items()

    issues = []
    seen_ids = {}

    for item in items:
        # Check for duplicate IDs
        if item.id in seen_ids:
            issues.append(
                {
                    "type": "duplicate_id",
                    "id": item.id,
                    "file": str(item.file_path),
                    "other_file": str(seen_ids[item.id]),
                    "message": (
                        f"Duplicate ID: {item.id} in "
                        f"{item.file_path} and {seen_ids[item.id]}"
                    ),
                }
            )
        else:
            seen_ids[item.id] = item.file_path

        # Check file name matches ID
        file_stem = item.file_path.stem  # e.g., "EXP-300-Some-Title"
        expected_prefix = item.id  # e.g., "EXP-300"

        if not file_stem.startswith(expected_prefix):
            issues.append(
                {
                    "type": "filename_mismatch",
                    "id": item.id,
                    "file": str(item.file_path),
                    "expected_prefix": expected_prefix,
                    "message": f"File name '{file_stem}' doesn't start with ID '{expected_prefix}'",
                }
            )

    if as_json:
        result = {
            "valid": len(issues) == 0,
            "items_checked": len(items),
            "issues": issues,
        }
        click.echo(json.dumps(result, indent=2))
        if issues:
            sys.exit(1)
        return

    if not issues:
        console.print("[green]All work items valid.[/green]")
        console.print(f"  Checked {len(items)} items")
        return

    # Report issues
    console.print(f"[bold red]Found {len(issues)} issue(s):[/bold red]")
    console.print()

    for issue in issues:
        if issue["type"] == "duplicate_id":
            console.print(f"[red]DUPLICATE ID:[/red] {issue['id']}")
            console.print(f"  File 1: {issue['file']}")
            console.print(f"  File 2: {issue['other_file']}")
        elif issue["type"] == "filename_mismatch":
            console.print(f"[yellow]FILENAME MISMATCH:[/yellow] {issue['id']}")
            console.print(f"  File: {issue['file']}")
            console.print(f"  Expected prefix: {issue['expected_prefix']}")

        console.print()

    if fix:
        fixed = 0
        for issue in issues:
            if issue["type"] == "filename_mismatch":
                old_path = Path(issue["file"])
                # Build new filename: ID + rest of old name after any existing ID
                old_stem = old_path.stem
                new_stem = issue["expected_prefix"]

                # Try to preserve the descriptive part after the ID
                # e.g., "EXP-303-Automated-Domain-Research" -> keep "-Automated-Domain-Research"
                import re

                match = re.match(r"^[A-Z]+-\d+(-.*)?$", old_stem)
                if match and match.group(1):
                    new_stem = issue["expected_prefix"] + match.group(1)

                new_path = old_path.parent / f"{new_stem}{old_path.suffix}"

                if new_path != old_path and not new_path.exists():
                    old_path.rename(new_path)
                    console.print(f"[green]Fixed:[/green] {old_path.name} -> {new_path.name}")
                    fixed += 1

        if fixed:
            console.print(f"\n[green]Fixed {fixed} issue(s)[/green]")
    else:
        console.print("[dim]Run with --fix to attempt automatic fixes[/dim]")

    sys.exit(1)


# HDD (Hypothesis-Driven Development) subgroups
main.add_command(hdd)
main.add_command(idea)
main.add_command(literature)
main.add_command(paper)
main.add_command(hypothesis)
main.add_command(experiment)
main.add_command(measure)


@main.command()
@click.argument("query_text", required=False)
@click.option("--sparql", "sparql_query", help="Raw SPARQL SELECT query against the unified graph")
@click.option(
    "--semantic", "semantic_query",
    help="Pure semantic search (requires sentence-transformers)",
)
@click.option("--top", "-n", "top_k", default=20, help="Max results to return (default: 20)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--no-semantic", is_flag=True, help="Disable semantic search (graph-only mode)")
@click.option("--verbose", "-v", is_flag=True, help="Show parsed query decomposition")
def query(
    query_text: str | None,
    sparql_query: str | None,
    semantic_query: str | None,
    top_k: int,
    as_json: bool,
    no_semantic: bool,
    verbose: bool,
):
    """Query work items using SPARQL, semantic search, or natural language.

    \b
    Examples:
      yurtle-kanban query "not-done expeditions above 700 that improve brain"
      yurtle-kanban query --sparql "SELECT ?id WHERE { ?item kb:id ?id . }"
      yurtle-kanban query --semantic "knowledge graph reasoning"
      yurtle-kanban query "blocked items" --no-semantic
    """
    from .query import EmbeddingIndex, NLDecomposer, QueryEngine, UnifiedGraph

    service = get_service()

    if sparql_query:
        # Raw SPARQL mode
        ug = UnifiedGraph.from_service(service)
        try:
            results = ug.sparql(sparql_query)
        except Exception as e:
            console.print(f"[red]SPARQL error:[/red] {e}")
            sys.exit(1)

        if as_json:
            click.echo(json.dumps(results, indent=2))
        else:
            if not results:
                console.print("[dim]No results.[/dim]")
                return
            # Print as table
            from rich.table import Table
            headers = list(results[0].keys())
            table = Table(title="SPARQL Results")
            for h in headers:
                table.add_column(h)
            for row in results[:top_k]:
                table.add_row(*[row.get(h, "") for h in headers])
            console.print(table)
        return

    if semantic_query:
        # Pure semantic mode
        try:
            emb = EmbeddingIndex.from_service(service)
        except ImportError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)

        hits = emb.search(semantic_query, top_k=top_k)
        if as_json:
            click.echo(json.dumps(
                [
                    {
                        "id": h.item_id,
                        "score": round(h.score, 4),
                        "title": h.item.title if h.item else "",
                    }
                    for h in hits
                ],
                indent=2,
            ))
        else:
            from rich.table import Table
            table = Table(title=f"Semantic Search: \"{semantic_query}\"")
            table.add_column("ID", style="cyan")
            table.add_column("Score", justify="right")
            table.add_column("Status")
            table.add_column("Title")
            for hit in hits:
                title = hit.item.title if hit.item else ""
                status = hit.item.status.value if hit.item else ""
                table.add_row(hit.item_id, f"{hit.score:.4f}", status, title)
            console.print(table)
        return

    if not query_text:
        console.print("[red]Provide a query string, --sparql, or --semantic.[/red]")
        console.print(
            '[dim]Example: yurtle-kanban query'
            ' "not-done expeditions that improve brain"[/dim]'
        )
        sys.exit(1)

    # Hybrid NL query
    enable_semantic = not no_semantic
    try:
        engine = QueryEngine.from_service(service, enable_semantic=enable_semantic)
    except ImportError:
        # Fall back to graph-only if sentence-transformers not installed
        engine = QueryEngine.from_service(service, enable_semantic=False)
        if enable_semantic:
            console.print("[dim]sentence-transformers not installed; using graph-only mode[/dim]")

    if verbose:
        decomposer = NLDecomposer()
        parsed = decomposer.parse(query_text)
        console.print("[bold]Parsed query:[/bold]")
        if parsed.status_filter:
            console.print(f"  Status exclude: {parsed.status_filter}")
        if parsed.status_include:
            console.print(f"  Status include: {parsed.status_include}")
        if parsed.type_filter:
            console.print(f"  Type: {parsed.type_filter}")
        if parsed.id_min is not None:
            console.print(f"  ID min: {parsed.id_min}")
        if parsed.id_max is not None:
            console.print(f"  ID max: {parsed.id_max}")
        if parsed.assignee:
            console.print(f"  Assignee: {parsed.assignee}")
        if parsed.tag:
            console.print(f"  Tag: {parsed.tag}")
        if parsed.semantic_query:
            console.print(f"  Semantic: \"{parsed.semantic_query}\"")
        console.print()

    results = engine.query(query_text, top_k=top_k)

    if not results:
        console.print("[dim]No results.[/dim]")
        return

    if as_json:
        click.echo(json.dumps(
            [{
                "id": r.item.id,
                "title": r.item.title,
                "status": r.item.status.value,
                "priority": r.item.priority,
                "semantic_score": round(r.semantic_score, 4) if r.semantic_score else None,
                "combined_score": round(r.combined_score, 4),
            } for r in results],
            indent=2,
        ))
    else:
        from rich.table import Table
        has_semantic = any(r.semantic_score > 0 for r in results)
        table = Table(title=f"Query: \"{query_text}\"")
        table.add_column("ID", style="cyan")
        table.add_column("Status")
        table.add_column("Priority")
        if has_semantic:
            table.add_column("Score", justify="right")
        table.add_column("Title")

        for r in results:
            row = [r.item.id, r.item.status.value, r.item.priority or ""]
            if has_semantic:
                row.append(f"{r.combined_score:.3f}")
            row.append(r.item.title)
            table.add_row(*row)
        console.print(table)

# Epic subgroups (epic is primary, voyage is nautical alias)
main.add_command(epic)
main.add_command(voyage)


if __name__ == "__main__":
    main()
