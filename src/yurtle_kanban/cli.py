"""
yurtle-kanban CLI

File-based kanban using Yurtle (Turtle RDF in Markdown). Git is your database.

Usage:
    yurtle-kanban init [--theme THEME] [--path PATH]
    yurtle-kanban list [--status STATUS] [--type TYPE] [--assignee ASSIGNEE]
    yurtle-kanban create TYPE TITLE [--priority PRIORITY] [--assign NAME]
                         [--body TEXT | --body-file PATH|-] [--push]
    yurtle-kanban move ID STATUS [--assign NAME] [--agent ACTOR]
    yurtle-kanban comment ID (--body TEXT | --body-file PATH|-) [--agent ACTOR]
    yurtle-kanban show ID
    yurtle-kanban board
    yurtle-kanban stats
    yurtle-kanban rank ID RANK [--summary TEXT]
    yurtle-kanban roadmap [--by-type] [--type TYPE] [--ranked] [--export md]
    yurtle-kanban history [--week] [--month] [--since DATE] [--by-assignee]
    yurtle-kanban next [--agent ACTOR]
    yurtle-kanban export --format FORMAT [--output FILE]
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import NoReturn

import click
from rich.console import Console
from rich.markup import escape

from ._click import Group, pull_note, safe
from .board import (
    render_board,
    render_history,
    render_item_detail,
    render_list,
    render_roadmap,
    render_stats,
)
from .config import BoardConfig, KanbanConfig, _under
from .epic_commands import epic, voyage
from .export import (
    export_expedition_index,
    export_html,
    export_json,
    export_markdown,
    export_research_index,
)
from .hdd_commands import experiment, hdd, hypothesis, idea, literature, measure, paper
from .inputs import check_identity, read_text_option, resolve_actor
from .models import (
    PRIORITIES,
    InputRefused,
    WorkItemStatus,
    WorkItemType,
    check_encodable,
    unknown_priority_message,
)
from .service import KanbanService, git_toplevel


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
# messages that must not mix into piped/--json stdout: hints, errors (#360, #371)
err_console = Console(stderr=True)


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
        try:
            config = KanbanConfig.load(config_path)
        except ValueError as e:  # a config value of the wrong kind (#220)
            # one line: a wrapped path breaks copy-paste and grep (#272)
            console.print(
                f"[red]Invalid {safe(config_path)}: {safe(e)}[/red]", soft_wrap=True
            )
            sys.exit(1)
    else:
        config = KanbanConfig()  # Use defaults

    return KanbanService(config, repo_root)


def _refuse(e: Exception) -> NoReturn:
    """Print a refused input as one red line and exit 1 (#580)."""
    console.print(f"[red]Error: {safe(e)}[/red]", soft_wrap=True)
    sys.exit(1)


class _Main(Group):
    """The root group: refuses undecodable argv before any command runs (#193).

    Python decodes invalid UTF-8 in argv to lone surrogates, which can't be
    written: without this, a title, `--summary`, `--authors`, a `next-id` prefix
    or an `experiment run --being` value crashed after a partial write, or was
    saved escaped. One check here covers every command and option.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # shell completion parses resiliently and reads stdout as candidates:
        # stay quiet there, the real run refuses (#193)
        for n, arg in enumerate([] if ctx.resilient_parsing else args, 1):
            try:
                check_encodable(f"argument {n}", arg)
            except ValueError as e:
                console.print(f"[red]{safe(e)}[/red]", soft_wrap=True)
                ctx.exit(1)
        return super().parse_args(ctx, args)


@click.group(cls=_Main)
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



def _yaml_scalar(text: str) -> str:
    """`text` as a YAML value that loads back as exactly `text`: bare when it
    already does, else a JSON string (valid YAML), so `~` or `a: b` stay the path
    they are, never null or a mapping (#509)."""
    import yaml

    try:
        if yaml.safe_load(f"k: {text}") == {"k": text}:
            return text
    except yaml.YAMLError:
        pass
    return json.dumps(text, ensure_ascii=False)


def _within(path: Path, root: Path) -> bool:
    """True when `path` is `root` or lies under it (both repo-relative)."""
    return path == root or root in path.parents

@main.command()
@click.option("--theme", default="software", help="Theme: software, nautical, or custom")
@click.option(
    "--path", default=None,
    help="Root for work items (default: the theme's own root, e.g. kanban-work/)",
)
def init(theme: str, path: str | None):
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
        if type_path and path and not _within(Path(type_path), Path(path)):
            # An explicit --path is the board root: scaffold each type folder
            # where `create` will write (#134). Like #102/#113's placement, a
            # theme path already under the root (e.g. `--path .`) is kept; one
            # outside it moves to <root>/<type folder>/.
            parts = Path(type_path).parts
            folder = Path(*parts[1:]) if len(parts) > 1 else Path(type_path)
            type_path = f"{(Path(path) / folder).as_posix()}/"
        if type_path:
            type_dir = _under(repo_root, type_path)
            type_dir.mkdir(parents=True, exist_ok=True)
            scan_paths.append(type_path)
            dirs_created.append(type_path)

            # Create _TEMPLATE.md in each directory
            prefix = type_def.get("id_prefix", type_id[:4].upper())
            sections = _TEMPLATE_SECTIONS.get(type_id, ["Description"])
            template_path = type_dir / "_TEMPLATE.md"
            if not template_path.exists():
                template_path.write_text(_generate_template(prefix, type_id, sections))

    # The root is the theme's own root, the common parent of its per-type
    # folders (kanban-work/, research/), unless --path says otherwise; the
    # board scans that one root, so new types are covered too (#112)
    scanned = [path] if path else []
    if path is None:
        common = os.path.commonpath([p.rstrip("/") for p in scan_paths]) if scan_paths else ""
        if common:
            path, scanned = f"{common}/", [f"{common}/"]
        elif scan_paths:
            # Type folders with no common parent (e.g. a custom theme's
            # `features/` and `bugs/` at the repo root): scan them as they are,
            # never `./` (that would pull in `.claude/**/*.md`)
            path, scanned = "work/", scan_paths
        else:
            path, scanned = "work/", ["work/"]
    # JSON strings are YAML strings: the same `"p"` as before for a plain path,
    # and a `"` or `\\` in one is escaped instead of breaking the file (#509)
    scan_paths_yaml = "\n".join(f"    - {json.dumps(p, ensure_ascii=False)}" for p in scanned)
    config_content = f"""# yurtle-kanban configuration
kanban:
  theme: {theme}

  paths:
    root: {_yaml_scalar(path)}
    scan_paths:
{scan_paths_yaml}

    ignore:
      - "**/archive/**"
      - "**/templates/**"
      - "**/_TEMPLATE*"
"""
    config_path = kanban_dir / "config.yaml"
    config_path.write_text(config_content)

    board_root = _under(repo_root, path).resolve()
    git_root = (git_toplevel(repo_root) or repo_root).resolve()
    if not _within(board_root, git_root):
        # git commits nothing outside the repo; say so now, not at the first move (#174)
        console.print(
            f"[yellow]Warning: {safe(board_root)} is outside this repository, so "
            "its items will not be git-tracked (no commits on create, move or comment)."
            "[/yellow]",
            soft_wrap=True,
        )

    # Copy any additional theme templates
    templates_src = _get_templates_dir()
    theme_templates = templates_src / theme
    templates_copied = 0
    if theme_templates.exists():
        templates_dst = kanban_dir / "templates"
        for template_file in theme_templates.glob("*.md"):
            shutil.copy(template_file, templates_dst / template_file.name)
            templates_copied += 1

    # Create the root directory only when the board scans it
    if path in scanned:
        _under(repo_root, path).mkdir(parents=True, exist_ok=True)

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

    console.print(f"[green]Initialized yurtle-kanban with theme '{escape(theme)}'[/green]")
    console.print("  Config:  .kanban/config.yaml")
    if dirs_created:
        for d in dirs_created:
            console.print(f"  Created: {escape(str(d))} (with _TEMPLATE.md)")
    else:
        console.print(f"  Created: {escape(str(path))}")
    if templates_copied:
        console.print(f"  Copied:  {templates_copied} templates to .kanban/templates/")
    if skills_installed:
        console.print(
            f"  Skills:  {skills_installed} Claude Code skills installed to .claude/skills/"
        )
    console.print()
    console.print("Next steps:")
    example_type = list(item_types.keys())[0] if item_types else "feature"
    console.print(
        "  1. Create work items: yurtle-kanban create "
        f"{escape(str(example_type))} 'My item' --push"
    )
    console.print("  2. View board: yurtle-kanban board")


def _warn_unparseable(service: KanbanService) -> None:
    """Print one stderr line per file that looks like an item but didn't parse (#139)."""
    for path, reason in dict.fromkeys(service.parse_warnings):
        try:
            shown = path.relative_to(service.repo_root)
        except ValueError:
            shown = path
        click.echo(f"warning: skipped {shown}: {reason}", err=True)


@main.command("list")
@click.option("--status", "-s", help="Filter by status (backlog, ready, in_progress, review, done)")
@click.option("--type", "-t", "item_type", help="Filter by type (feature, bug, epic, task)")
@click.option("--assignee", help="Filter by assignee (who holds the item)")
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
    if assignee is not None:
        try:
            assignee = check_identity(assignee, "--assignee")
        except ValueError as e:
            _refuse(e)
    service = get_service()

    # Parse filters
    status_filter = None
    if status:
        try:
            status_filter = WorkItemStatus.from_string(status)
        except ValueError:
            console.print(f"[red]Unknown status: {safe(status)}[/red]")
            sys.exit(1)

    type_filter = None
    if item_type:
        try:
            type_filter = WorkItemType.from_string(item_type)
        except ValueError:
            console.print(f"[red]Unknown type: {safe(item_type)}[/red]")
            sys.exit(1)

    priority_filter = None
    if priority:
        # empty segments (`high,,low`, `high,`) are dropped (#238)
        priority_filter = [p for p in (s.strip().lower() for s in priority.split(",")) if p]
        if not priority_filter:
            console.print(f"[red]No priority given; valid: {', '.join(PRIORITIES)}[/red]")
            sys.exit(1)
        invalid = [p for p in priority_filter if p not in PRIORITIES]
        # one message per value, each rendered like everywhere else (#190, #238)
        for value in invalid:
            console.print(
                f"[red]{escape(unknown_priority_message(value))}[/red]", soft_wrap=True
            )
        if invalid:
            sys.exit(1)

    items = service.get_items(
        status=status_filter,
        item_type=type_filter,
        assignee=assignee,
        board=board_name,
        priority=priority_filter,
    )

    _warn_unparseable(service)

    if not items:
        console.print("[dim]No work items found.[/dim]")
        return

    if as_json:
        data = [item.to_dict() for item in items]
        click.echo(json.dumps(data, indent=2))
    else:
        render_list(items, console, status_label=service.status_label)


@main.command()
@click.argument("item_type")
@click.argument("title")
@click.option(
    "--priority", "-p", default="medium",
    type=click.Choice(PRIORITIES, case_sensitive=False),
    help="Priority",
)
@click.option("--assign", "assign", help="Set the assignee (who holds the item); never defaulted")
@click.option("--body", help="The item's body text (prefer --body-file for anything multi-line)")
@click.option(
    "--body-file",
    help="Read the body from PATH, or from stdin with '-' (pipe it: a quoted heredoc <<'EOF')",
)
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
    assign: str | None,
    body: str | None,
    body_file: str | None,
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
        yurtle-kanban create bug "Login crash" --push --assign Mini
        yurtle-kanban create feature "Dark mode" --body-file - <<'EOF'
        ...body text, never expanded by the shell...
        EOF
    """
    # the whole body is read before any subprocess can touch stdin (#580)
    try:
        description = read_text_option(body, body_file, "body")
        assignee = check_identity(assign, "--assign") if assign is not None else None
    except ValueError as e:
        _refuse(e)
    service = get_service()

    try:
        work_type = WorkItemType.from_string(item_type)
    except ValueError:
        console.print(f"[red]Unknown type: {safe(item_type)}[/red]")
        console.print(f"Valid types: {', '.join(t.value for t in WorkItemType)}")
        sys.exit(1)

    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    try:
        check_encodable("title", title)
        check_encodable("description", description)
        check_encodable("assignee", assignee)
        check_encodable("tags", tag_list)
    except InputRefused as e:
        console.print(f"[red]{safe(e)}[/red]", soft_wrap=True)
        sys.exit(1)

    if push:
        try:
            result = service.create_item_and_push(
                item_type=work_type,
                title=title,
                priority=priority,
                assignee=assignee,
                description=description,
                tags=tag_list,
            )
        except InputRefused as e:  # a refusal only; a bug keeps its traceback (#644, #666)
            _refuse(e)
        if result["success"]:
            item = result["item"]
            if result.get("pushed"):
                console.print(
                    f"[green]Created and pushed {escape(str(result['id']))}: "
                    f"{escape(title)}[/green]"
                )
                if result.get("local", True):
                    console.print(f"  File: {escape(str(item.file_path))}")
                    console.print("[dim]  (committed and pushed to remote)[/dim]")
                else:
                    console.print(pull_note(result))
            else:
                console.print(
                    f"[green]Created {escape(str(result['id']))}: "
                    f"{escape(title)}[/green]"
                )
                console.print(f"  File: {escape(str(item.file_path))}")
                if result.get("committed", True):
                    console.print("[dim]  (committed locally — no remote configured)[/dim]")
                else:
                    console.print(
                        "[yellow]  (not committed: the board is outside the git repository)"
                        "[/yellow]"
                    )
        else:
            console.print(f"[red]Failed: {safe(result['message'])}[/red]")
            sys.exit(1)
    else:
        try:
            item = service.create_item(
                item_type=work_type,
                title=title,
                priority=priority,
                assignee=assignee,
                description=description,
                tags=tag_list,
            )
        except InputRefused as e:  # a refusal only; a bug keeps its traceback (#644, #666)
            _refuse(e)
        console.print(f"[green]Created {escape(item.id)}: {escape(item.title)}[/green]")
        console.print(f"  File: {escape(str(item.file_path))}")


@main.command()
@click.argument("item_id")
@click.argument("new_status")
@click.option("--no-commit", is_flag=True, help="Don't create git commit")
@click.option("--message", "-m", help="Custom commit message")
@click.option("--assign", help="Set the assignee (who holds the item, e.g. 'Claude-M5')")
@click.option(
    "--agent",
    help="Who is moving it (kb:by); default $YURTLE_AGENT, then git user.name",
)
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
    agent: str | None,
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
        if assign is not None:
            assign = check_identity(assign, "--assign")
        actor = resolve_actor(agent, cwd=service.repo_root)
    except ValueError as e:
        _refuse(e)

    target = service.get_item(item_id.upper())
    if target is None:
        console.print(f"[red]Error: Item not found: {safe(item_id.upper())}[/red]")
        sys.exit(1)
    try:  # before its status is read off one of the copies (#742)
        service.refuse_duplicate(target, "a move")
    except ValueError as e:
        _refuse(e)
    # a name resolves through the item's own theme only (hdd `active`), never
    # another theme's; --force doesn't change that (#587)
    status = service.resolve_status_name(target, new_status)
    if status is None:
        console.print(f"[red]Unknown status: {safe(new_status)}[/red]")
        valid = service.listed_status_names(target)
        console.print(f"Valid statuses: {escape(', '.join(valid))}")
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
            actor=actor,
            skip_wip_check=force,
            validate_workflow=not force,
            closed_by=closed_by,
            skip_gates=skip_gates or force,
            gate_context=gate_context,
        )
        # named the way the item's theme names it (hdd `active`) (#448)
        moved_to = safe(service.status_label(item))
        console.print(f"[green]Moved {escape(item.id)} to {moved_to}[/green]")
        if assign:
            console.print(f"  Assigned to: {escape(assign)}")
    except ValueError as e:
        console.print(f"[red]Error: {safe(e)}[/red]", soft_wrap=True)
        sys.exit(1)

    # Export board if requested
    if export_board:
        board = service.get_board()
        content = export_expedition_index(board, min_id=600)
        Path(export_board).write_text(content)
        console.print(f"[green]Exported board to {escape(export_board)}[/green]")


@main.command()
@click.argument("item_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def show(item_id: str, as_json: bool):
    """Show details of a work item."""
    service = get_service()

    item = service.get_item(item_id.upper())
    if not item:
        # The ID may belong to a file that exists but doesn't parse (#158)
        wanted = item_id.upper()
        broken = [
            (path, reason) for path, reason in service.parse_warnings
            if path.stem.upper() == wanted or path.stem.upper().startswith(wanted + "-")
        ]
        if as_json:
            payload: dict[str, object] = {"error": f"Item not found: {item_id}"}
            if broken:
                payload["unparseable"] = [
                    {"file": str(path), "reason": reason} for path, reason in broken
                ]
            click.echo(json.dumps(payload))
        else:
            console.print(f"[red]Item not found: {safe(item_id)}[/red]")
            for path, reason in broken:
                try:
                    shown = path.relative_to(service.repo_root)
                except ValueError:
                    shown = path
                # escape: a YAML error quotes the bad line, and `[...]` in it would
                # otherwise be read as Rich markup (crash or swallowed text)
                console.print(
                    f"  found {safe(shown)}, but it doesn't parse: {safe(reason)}",
                    soft_wrap=True,
                )
        sys.exit(1)

    next_statuses = service.next_statuses(item)
    if as_json:
        data = item.to_dict()
        # JSON stays canonical; the native names ride alongside, same order (#448, #573)
        data["next_statuses"] = [canonical for canonical, _ in next_statuses]
        data["next_status_labels"] = [native for _, native in next_statuses]
        click.echo(json.dumps(data, indent=2))
    else:
        render_item_detail(
            item, console, status_label=service.status_label,
            next_statuses=[_status_display(*pair) for pair in next_statuses],
        )


def _status_display(canonical: str, native: str) -> str:
    """`native (canonical)` where the theme renames a status, else the name (#573)."""
    return native if native == canonical else f"{native} ({canonical})"


@main.command()
@click.option("--board", "-b", "board_name", help="Only this board (default: every board)")
@click.option(
    "--type", "-t", "item_type",
    help="Item type: selects its workflow, where .kanban/workflows/ has one for it",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def states(board_name: str | None, item_type: str | None, as_json: bool):
    """Show each board's lifecycle: every status and the statuses it may move to.

    This is lifecycle legality only: gates, WIP limits and workflow rules can still
    refuse a legal move (--json lists each transition's gate ids). --type only
    matters where a workflow exists for that type; a theme's own transitions
    apply to every type. Without --board, every board in turn.

    Examples:
        yurtle-kanban states
        yurtle-kanban states --board research --json
        yurtle-kanban states --type feature
    """
    service = get_service()
    config = service.config

    boards: list[tuple[str, BoardConfig | None, str]]
    if config.is_multi_board:
        boards = [(b.name, b, b.preset) for b in config.boards]
    else:
        boards = [("default", None, config.theme)]
    if board_name is not None:
        boards = [b for b in boards if b[0] == board_name]
        if not boards:
            error = f"Unknown board: {board_name}"
            if as_json:
                click.echo(json.dumps({"error": error}))
            else:
                console.print(f"[red]{safe(error)}[/red]")
            sys.exit(1)

    entries = [
        {
            "board": name,
            "theme": theme,
            "type": item_type,
            "states": service.lifecycle(board_config, item_type),
        }
        for name, board_config, theme in boards
    ]
    if as_json:
        click.echo(json.dumps(entries, indent=2))
        return

    for entry in entries:
        heading = f"Board {entry['board']} ({entry['theme']})"
        if item_type:
            heading += f", type {item_type}"
        console.print(f"[bold]{escape(heading)}[/bold]", soft_wrap=True)
        for state in entry["states"]:
            nexts = ", ".join(
                _status_display(n["canonical"], n["name"]) for n in state["next"]
            ) or "(terminal)"
            name = _status_display(state["canonical"], state["name"])
            console.print(f"  {escape(name)} → {escape(nexts)}", highlight=False, soft_wrap=True)
        console.print()


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
            console.print(f"\n[bold cyan]Board: {escape(str(board_config.name))}[/bold cyan]")
            console.print(f"  Preset: {escape(str(board_config.preset))}")
            console.print(f"  Path: {escape(str(board_config.path))}")
            board_data = service.get_board(board_name=board_config.name)
            if epic_id:
                board_data.items = [
                    i for i in board_data.items if epic_id in i.related
                ]
            render_board(board_data, console)
            console.print()
        _warn_unparseable(service)
        return

    board_data = service.get_board(board_name=board_name)
    if epic_id:
        board_data.items = [i for i in board_data.items if epic_id in i.related]
    render_board(board_data, console)
    _warn_unparseable(service)


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
        console.print(f"  Preset: {escape(str(config.theme))}")
        console.print(f"  Path: {escape(str(config.paths.root or 'work/'))}")
    else:
        console.print(f"[bold]Configured Boards ({len(config.boards)})[/bold]")
        console.print()
        for info in boards_info:
            default_marker = " [cyan](default)[/cyan]" if info.get("default") else ""
            console.print(f"  [bold]{escape(str(info['name']))}[/bold]{default_marker}")
            console.print(f"    Preset: {escape(str(info['preset']))}")
            console.print(f"    Path: {escape(str(info['path']))}")
            if info.get("wip_limits"):
                wip_str = ", ".join(f"{k}: {v}" for k, v in info["wip_limits"].items())
                console.print(f"    WIP Limits: {escape(wip_str)}")
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
        console.print(f"[red]Unknown preset: {safe(preset)}[/red]")
        console.print(f"[dim]Available presets: {', '.join(available)}[/dim]")
        sys.exit(1)

    # Parse WIP limits
    wip_limits = {}
    for wip in wip_limit:
        if ":" in wip:
            status, limit = wip.split(":", 1)
            try:
                wip_limits[status] = int(limit)
                if wip_limits[status] < 0:  # the same rule as a config limit (#420)
                    raise ValueError
            except ValueError:
                console.print(f"[red]Invalid WIP limit: {safe(wip)}[/red]")
                sys.exit(1)

    # Upgrading to multi-board turns the single-board config into ONE board that
    # scans one path; if no path covers every scan path, items would silently
    # vanish from the board, so refuse and change nothing (#122)
    # Every path the single board scans: scan_paths plus the legacy per-type
    # paths (paths.features/bugs/epics/tasks), which get_work_paths() also scans
    # and the upgrade doesn't carry over (#147)
    paths = config.paths
    legacy = [p for p in (paths.features, paths.bugs, paths.epics, paths.tasks) if p]
    scanned = list(config.paths.scan_paths) + legacy
    if not config.is_multi_board and scanned:
        # `~` and absolute spellings of one place are the same place (#494)
        board_path = Path(config._single_board_path()).expanduser()
        uncovered = [
            p for p in scanned
            if not _within(Path(p).expanduser(), board_path)
        ]
        if uncovered:
            # soft_wrap: never hard-wrap inside a path, or it can't be copied (#147)
            console.print(
                "[red]Can't upgrade to multi-board: a board scans one path, and no "
                f"single path covers these scan paths: {safe(', '.join(uncovered))}[/red]",
                soft_wrap=True,
            )
            console.print(
                "[dim]Move them under a common folder (and set paths.root to it), "
                "then run board-add again. .kanban/config.yaml was not changed.[/dim]",
                soft_wrap=True,
            )
            sys.exit(1)

    # Check if board already exists
    if config.is_multi_board and config.get_board(name):
        console.print(f"[red]Board '{safe(name)}' already exists[/red]")
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
    board_path = _under(repo_root, path)
    if not board_path.exists():
        board_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]Created directory: {escape(path)}[/green]")

    console.print(f"[green]Added board '{escape(name)}'[/green]")
    console.print(f"  Preset: {escape(preset)}")
    console.print(f"  Path: {escape(path)}")
    if wip_limits:
        wip_str = ", ".join(f"{k}: {v}" for k, v in wip_limits.items())
        console.print(f"  WIP Limits: {escape(wip_str)}")
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
            console.print(f"[red]Unknown type: {safe(item_type)}[/red]")
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
                f"({service.status_label(item)}) @{assignee}"
            )
        click.echo("\n".join(lines))
    else:
        render_roadmap(
            items, console, by_type=by_type, ranked=ranked, status_label=service.status_label
        )


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
        console.print(f"[green]Ranked {escape(item.id)} as #{rank_number}[/green]")
        if summary:
            console.print(f"  Value: {escape(summary)}")
        if item.priority:
            console.print(f"  Priority: {escape(str(item.priority))}")
        console.print(f"  Status: {safe(service.status_label(item))}")
    except ValueError as e:
        console.print(f"[red]{safe(e)}[/red]", soft_wrap=True)
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
            console.print(f"[red]Invalid date format: {safe(since)} (use YYYY-MM-DD)[/red]")
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
@click.option("--agent", help="Who is asking (items it holds are suggested first)")
def next_item(agent: str | None):
    """Suggest the next item to work on."""
    if agent is not None:
        try:
            agent = check_identity(agent, "--agent")
        except ValueError as e:
            _refuse(e)
    service = get_service()

    item = service.suggest_next_item(assignee=agent)

    if not item:
        console.print("[dim]No ready items to work on.[/dim]")
        return

    console.print("[bold]Suggested next item:[/bold]")
    render_item_detail(item, console, status_label=service.status_label)


def _id_csv(value: str | None) -> list[str] | None:
    """A comma-separated ID list option: None when not given, [] for `""`."""
    return None if value is None else value.split(",")


@main.command()
@click.argument("item_id")
@click.option("--title", help="New title, one line: the `title:` key and the `# Title` heading")
@click.option("--priority", help="New priority: critical, high, medium or low")
@click.option("--tag", "add_tags", multiple=True, help="Add a tag (repeatable)")
@click.option("--untag", "remove_tags", multiple=True, help="Remove a tag (repeatable)")
@click.option(
    "--body",
    help="New body, replacing the text under the heading (prefer --body-file: no shell expansion)",
)
@click.option(
    "--body-file",
    help="Read the new body from PATH, or from stdin with '-' (pipe it: a quoted heredoc <<'EOF')",
)
@click.option(
    "--depends-on", help='Replace the dependencies: comma-separated IDs ("" clears them)'
)
@click.option("--add-dep", multiple=True, help="Add a dependency (repeatable)")
@click.option("--rm-dep", multiple=True, help="Remove a dependency (repeatable)")
@click.option("--related", help='Replace the related items: comma-separated IDs ("" clears them)')
@click.option(
    "--allow-unknown", is_flag=True, help="Accept dependency IDs that are on no board"
)
@click.option("--no-commit", is_flag=True, help="Write the file, but don't commit it")
def update(
    item_id: str,
    title: str | None,
    priority: str | None,
    add_tags: tuple[str, ...],
    remove_tags: tuple[str, ...],
    body: str | None,
    body_file: str | None,
    depends_on: str | None,
    add_dep: tuple[str, ...],
    rm_dep: tuple[str, ...],
    related: str | None,
    allow_unknown: bool,
    no_commit: bool,
):
    """Edit a work item's fields and dependencies (not its status: use move).

    Only the lines that change are rewritten; the status history, comments and
    any other frontmatter keys stay as they are. The commit holds the item file
    alone and names each change. A dependency that points at the item itself,
    at an ID on no board (without --allow-unknown) or on two boards, or that
    closes a cycle is refused, and nothing is written.

    Examples:

        yurtle-kanban update EXP-5 --add-dep EXP-3 --rm-dep EXP-2 --priority high

        yurtle-kanban update EXP-5 --depends-on ""          # clear the dependencies

        yurtle-kanban update EXP-5 --body-file - <<'EOF'
        ...new body...
        EOF
    """
    # the whole text is read before any subprocess can touch stdin (#580)
    try:
        description = read_text_option(body, body_file, "body")
    except ValueError as e:
        _refuse(e)
    service = get_service()
    try:
        item, changes = service.update_item_changes(
            item_id.upper(),
            title=title,
            priority=priority,
            description=description,
            add_tags=list(add_tags),
            remove_tags=list(remove_tags),
            depends_on=_id_csv(depends_on),
            add_depends_on=list(add_dep),
            remove_depends_on=list(rm_dep),
            related=_id_csv(related),
            allow_unknown=allow_unknown,
            commit=not no_commit,
        )
    except ValueError as e:
        _refuse(e)
    if not changes:
        console.print("no changes")
        return
    console.print(
        f"[green]Updated {escape(item.id)}:[/green] {safe(', '.join(changes))}", soft_wrap=True
    )


@main.command()
@click.argument("item_id")
@click.option("--body", help="The comment text (prefer --body-file: no shell expansion)")
@click.option(
    "--body-file",
    help="Read the comment from PATH, or from stdin with '-' (pipe it: a quoted heredoc <<'EOF')",
)
@click.option(
    "--agent",
    help="Who is commenting; default $YURTLE_AGENT, then git user.name",
)
def comment(item_id: str, body: str | None, body_file: str | None, agent: str | None):
    """Add a comment to a work item.

    Example (the quoted 'EOF' keeps the shell from expanding $(...) and backticks):

        yurtle-kanban comment EXP-123 --body-file - <<'EOF'
        ...comment text...
        EOF
    """
    # the whole text is read before any subprocess can touch stdin (#580)
    try:
        text = read_text_option(body, body_file, "body", required=True)
        assert text is not None  # required=True: None is a usage error
    except ValueError as e:
        _refuse(e)
    service = get_service()

    try:
        author = resolve_actor(agent, cwd=service.repo_root)
        item = service.add_comment(item_id.upper(), text, author)
        console.print(f"[green]Added comment to {escape(item.id)}[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {safe(e)}[/red]", soft_wrap=True)
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
    render_list(items, console, status_label=service.status_label)


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
            console.print(f"[yellow]{safe(metrics_data['error'])}[/yellow]")
            console.print("[dim]Status history is recorded when items move between statuses.[/dim]")
            return

        if as_json:
            click.echo(json.dumps(metrics_data, indent=2, default=str))
        else:
            console.print(f"[bold]Flow Metrics: {escape(item_id.upper())}[/bold]")
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
                        console.print(f"    {escape(str(status))}: {hours:.1f} hours")
                    else:
                        console.print(f"    {escape(str(status))}: {hours / 24:.1f} days")
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
        console.print(f"[red]Unknown format: {safe(fmt)}[/red]")
        sys.exit(1)

    if output:
        Path(output).write_text(content)
        console.print(f"[green]Exported to {escape(output)}[/green]")
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
    concurrently: it fetches the remote's default branch, takes the next id past
    what that branch and this checkout hold, and commits the allocation record
    onto the default branch with a compare-and-swap push (a lost race retries
    with a new id). Your branch, index and working tree are not touched.

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
        if not result["success"]:
            sys.exit(1)  # a failed allocation is a failure in JSON too (#590)
    else:
        if result["success"]:
            console.print(f"[green]Allocated: {escape(str(result['id']))}[/green]")
            console.print(f"  Prefix: {escape(str(result['prefix']))}")
            console.print(f"  Number: {result['number']}")
            if not no_sync:
                console.print("[dim]  (committed and pushed to remote)[/dim]")
        else:
            console.print(
                f"[red]Failed to allocate ID: {safe(result['message'])}[/red]", soft_wrap=True
            )
            sys.exit(1)


@main.command()
@click.option("--fix", is_flag=True, help="Attempt to fix issues (rename files)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate(fix: bool, as_json: bool):
    """Validate work items for consistency issues.

    Checks:
    - File name matches ID in frontmatter
    - No duplicate IDs, across every board
    - No dependency cycles, across every board
    - Every depends_on target is on a board
    - Required fields present (id, title, status, type)
    """
    service = get_service()
    items = service.get_items()

    issues = []
    # every file per ID: the items listed, then the ones the scan's merge dropped
    # (a duplicate across boards keeps one item, #576)
    files_by_id: dict[str, list[Path]] = {}
    for item in items:
        files_by_id.setdefault(item.id, []).append(item.file_path)
    for dup_id, files in service.duplicate_ids.items():
        known = files_by_id.setdefault(dup_id, [])
        known += [f for f in files if f not in known]
    for dup_id, files in files_by_id.items():
        for other in files[1:]:
            issues.append(
                {
                    "type": "duplicate_id",
                    "id": dup_id,
                    "file": str(other),
                    "other_file": str(files[0]),
                    "message": f"Duplicate ID: {dup_id} in {other} and {files[0]}",
                }
            )

    # the dependency graph over every board (#576)
    for cycle in service.dependency_cycles():
        path = " → ".join(cycle)
        issues.append(
            {
                "type": "dependency_cycle",
                "ids": cycle,
                "message": f"Dependency cycle: {path}",
            }
        )
    for item_id, target in service.dangling_dependencies():
        issues.append(
            {
                "type": "dangling_dependency",
                "id": item_id,
                "target": target,
                "message": f"{item_id} depends on {target}, which is on no board",
            }
        )

    for item in items:
        # a body fence that swallows the history or comments (#727, #769)
        try:
            text = item.file_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            text = None
        if text is not None and (line := service.swallowed_fence_line(text)) is not None:
            issues.append(
                {
                    "type": "swallowing_fence",
                    "id": item.id,
                    "line": line,
                    "swallows": (what := service.swallowed_what(text, line)),
                    # "runs over": refusal 2's fence is closed, but quotes the opener (#758)
                    "message": f"{item.id}: the body's code fence on line {line} runs over "
                    f"{what}: close it, or reword a quoted heading inside it",
                }
            )

    for item in items:
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
            console.print(f"[red]DUPLICATE ID:[/red] {safe(issue['id'])}")
            console.print(f"  File 1: {safe(issue['file'])}")
            console.print(f"  File 2: {safe(issue['other_file'])}")
        elif issue["type"] == "filename_mismatch":
            console.print(f"[yellow]FILENAME MISMATCH:[/yellow] {safe(issue['id'])}")
            console.print(f"  File: {safe(issue['file'])}")
            console.print(f"  Expected prefix: {safe(issue['expected_prefix'])}")
        elif issue["type"] == "dependency_cycle":
            console.print(
                f"[red]DEPENDENCY CYCLE:[/red] {safe(' → '.join(issue['ids']))}", soft_wrap=True
            )
        elif issue["type"] == "swallowing_fence":
            console.print(
                f"[yellow]SWALLOWING FENCE:[/yellow] {safe(issue['id'])}: the body's code "
                f"fence on line {safe(str(issue['line']))} runs over "
                f"{safe(issue['swallows'])}: close it, or reword a quoted heading inside it",
                soft_wrap=True,
            )
        elif issue["type"] == "dangling_dependency":
            console.print(
                f"[yellow]DANGLING DEPENDENCY:[/yellow] {safe(issue['id'])} depends on "
                f"{safe(issue['target'])}, which is on no board",
                soft_wrap=True,
            )

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
                    console.print(
                        f"[green]Fixed:[/green] {escape(old_path.name)} -> "
                        f"{escape(new_path.name)}"
                    )
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


def _cap_to_top(rows: list, top: int) -> list:
    """The first `top` rows; when there were more (the caller asks for one extra),
    say so on stderr, so a truncated `--json` is never silent (#397)."""
    if len(rows) > top:
        err_console.print(
            f"[dim]showing the first {top} results; use --top for more[/dim]",
            soft_wrap=True,
        )
    return rows[:top]


@main.command()
@click.argument("query_text", required=False)
@click.option("--sparql", "sparql_query", help="Raw SPARQL SELECT query against the unified graph")
@click.option(
    "--semantic", "semantic_query",
    help="Pure semantic search (requires sentence-transformers)",
)
@click.option(
    "--top", "-n", "top_k", default=20, type=click.IntRange(min=1),  # 0/-N made no sense (#377)
    help="Max results to return (default: 20)",
)
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
      yurtle-kanban query --sparql "SELECT ?id WHERE { ?item kb:id ?id . FILTER(isIRI(?item)) }"
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
            err_console.print(f"[red]SPARQL error:[/red] {safe(e)}", soft_wrap=True)
            sys.exit(1)
        results = _cap_to_top(results, top_k)  # JSON too, like the table (#387, #397)

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
                table.add_column(escape(str(h)))
            for row in results:
                table.add_row(*[escape(str(row.get(h, ""))) for h in headers])
            console.print(table)
        return

    if semantic_query:
        # Pure semantic mode
        try:
            emb = EmbeddingIndex.from_service(service)
            hits = emb.search(semantic_query, top_k=top_k + 1)
        except ImportError as e:
            # missing, or installed but broken (#346, #358): one line, never wrapped,
            # on stderr so `--json` stdout stays empty (#371)
            err_console.print(f"[red]{safe(e)}[/red]", soft_wrap=True)
            sys.exit(1)
        hits = _cap_to_top(hits, top_k)  # the --top note (#397), outside the try (#405)
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
            table = Table(title=f"Semantic Search: \"{escape(semantic_query)}\"")
            table.add_column("ID", style="cyan")
            table.add_column("Score", justify="right")
            table.add_column("Status")
            table.add_column("Title")
            for hit in hits:
                title = hit.item.title if hit.item else ""
                status = escape(service.status_label(hit.item)) if hit.item else ""
                table.add_row(escape(hit.item_id), f"{hit.score:.4f}", status, escape(title))
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
    engine = QueryEngine.from_service(service, enable_semantic=enable_semantic)

    if verbose:
        # with --json the parse goes to stderr, so stdout stays pure JSON (#371)
        out = err_console if as_json else console
        decomposer = NLDecomposer()
        parsed = decomposer.parse(query_text)
        out.print("[bold]Parsed query:[/bold]")
        if parsed.status_filter:
            out.print(f"  Status exclude: {escape(str(parsed.status_filter))}")
        if parsed.status_include:
            out.print(f"  Status include: {escape(str(parsed.status_include))}")
        if parsed.type_filter:
            out.print(f"  Type: {escape(str(parsed.type_filter))}")
        if parsed.id_min is not None:
            out.print(f"  ID min: {parsed.id_min}")
        if parsed.id_max is not None:
            out.print(f"  ID max: {parsed.id_max}")
        if parsed.assignee:
            out.print(f"  Assignee: {escape(str(parsed.assignee))}")
        if parsed.tag:
            out.print(f"  Tag: {escape(str(parsed.tag))}")
        if parsed.semantic_query:
            out.print(f"  Semantic: \"{escape(str(parsed.semantic_query))}\"")
        out.print()

    results = _cap_to_top(engine.query(query_text, top_k=top_k + 1), top_k)
    if enable_semantic and not engine.semantic_enabled:
        # the search extra is missing or broken: graph-only results, said once (after
        # the query, which may find it broken, #358), on stderr so
        # `--json` output stays pure JSON (#346)
        err_console.print(
            "[dim]semantic search is off (sentence-transformers unavailable; "
            "pip install yurtle-kanban\\[search]); using graph-only mode[/dim]",
            soft_wrap=True,
        )

    if not results:
        if as_json:
            click.echo("[]")  # --json output is always JSON (#358)
        else:
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
        table = Table(title=f"Query: \"{escape(query_text)}\"")
        table.add_column("ID", style="cyan")
        table.add_column("Status")
        table.add_column("Priority")
        if has_semantic:
            table.add_column("Score", justify="right")
        table.add_column("Title")

        for r in results:
            row = [
                escape(r.item.id),
                escape(service.status_label(r.item)),  # theme's name (#448)
                escape(r.item.priority or ""),
            ]
            if has_semantic:
                row.append(f"{r.combined_score:.3f}")
            row.append(escape(r.item.title))
            table.add_row(*row)
        console.print(table)

# Epic subgroups (epic is primary, voyage is nautical alias)
main.add_command(epic)
main.add_command(voyage)


if __name__ == "__main__":
    main()
