"""
Kanban Service - Core operations for managing work items.

This service provides the business logic for:
- Loading/saving work items from Yurtle files
- State transitions with validation
- WIP limit enforcement
- Item creation and updates
"""

from __future__ import annotations

import fnmatch
import logging
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef

from .config import KanbanConfig
from .hooks import HookContext, HookEngine, HookEvent

if TYPE_CHECKING:
    from .config import BoardConfig
    from .gates import GateResult
from .models import Board, Column, Comment, WorkItem, WorkItemStatus, WorkItemType
from .turtle_builder import PREFIXES
from .workflow import WorkflowConfig, WorkflowParser

logger = logging.getLogger("yurtle-kanban")

# HDD namespace objects (derived from turtle_builder.PREFIXES, single source of truth)
_HYP = Namespace(PREFIXES["hyp"])
_PAPER_NS = Namespace(PREFIXES["paper"])
_EXPR = Namespace(PREFIXES["expr"])
_MEASURE = Namespace(PREFIXES["measure"])
_IDEA = Namespace(PREFIXES["idea"])
_LIT = Namespace(PREFIXES["lit"])

# HDD type aliases for backfill (normalize variant names to canonical types)
_TYPE_ALIASES: dict[str, str] = {"secondary-hypothesis": "hypothesis"}

# HDD types eligible for turtle block backfill
_BACKFILL_TYPES = frozenset({"idea", "literature", "paper", "hypothesis", "experiment", "measure"})

# RDF class URIs for each HDD type
_HDD_TYPE_CLASSES: dict[str, URIRef] = {
    "idea": _IDEA.Idea,
    "literature": _LIT.Literature,
    "paper": _PAPER_NS.Paper,
    "hypothesis": _HYP.Hypothesis,
    "experiment": _EXPR.Experiment,
    "measure": _MEASURE.Measure,
}

_PAPER_PREFIX_RE = re.compile(r"^[Pp]aper", re.IGNORECASE)


def _normalize_paper_num(raw: str | int) -> str:
    """Extract the numeric part from a paper field value.

    Handles: 130, "130", "Paper130", "paper130", "PAPER130".
    """
    return _PAPER_PREFIX_RE.sub("", str(raw))


class KanbanService:
    """Service for managing kanban work items."""

    def __init__(self, config: KanbanConfig, repo_root: Path, hooks_config: Path | None = None):
        self.config = config
        self.repo_root = repo_root
        self._items: dict[str, WorkItem] = {}
        self._board: Board | None = None
        self._workflow_parser = WorkflowParser(repo_root / ".kanban")
        self._workflows: dict[str, WorkflowConfig] = {}
        self._hook_engine = HookEngine(
            hooks_config or (repo_root / ".kanban" / "hooks" / "kanban-hooks.yurtle.md")
        )
        self._hook_engine.set_callback("create_item", self._hook_create_item)

    def _get_board_for_item(self, item: WorkItem) -> BoardConfig | None:
        """Get the board configuration for a work item."""
        if not self.config.is_multi_board or not item.file_path:
            return None
        return self.config.get_board_for_path(item.file_path, self.repo_root)

    def _load_board_theme(
        self, board_config: BoardConfig | None,
    ) -> dict | None:
        """Load the theme for a board configuration.

        Returns the full theme dict, or None if no board or no theme.
        Centralises theme loading so callers don't duplicate the work.
        """
        if not board_config:
            return None

        from .config import _load_builtin_theme

        return _load_builtin_theme(board_config.preset, self.repo_root)

    def _get_reverse_status_mapping(
        self, board_config: BoardConfig | None,
        theme: dict | None = None,
    ) -> dict[str, str]:
        """Get reverse mapping from canonical status to board-native name.

        For HDD: ``{backlog: draft, in_progress: active, ...}``
        For nautical or no board: returns empty dict.
        """
        if theme is None:
            theme = self._load_board_theme(board_config)
        if not theme or "status_mappings" not in theme:
            return {}

        # Invert: {native: canonical} -> {canonical: native}
        return {
            canonical: native
            for native, canonical in theme["status_mappings"].items()
        }

    def _get_board_transitions(
        self, board_config: BoardConfig | None,
        theme: dict | None = None,
    ) -> dict[str, list[str]] | None:
        """Get board-specific state transitions.

        Returns the transitions dict from the board's theme, or None
        if not defined.
        """
        if theme is None:
            theme = self._load_board_theme(board_config)
        if not theme or "transitions" not in theme:
            return None

        return theme["transitions"]

    def scan(self) -> list[WorkItem]:
        """Scan configured paths for work items."""
        self._items.clear()

        for scan_path in self.config.get_work_paths():
            full_path = self.repo_root / scan_path
            if full_path.exists():
                for item in self._scan_directory(full_path):
                    self._items[item.id] = item

        return list(self._items.values())

    def _scan_directory(self, directory: Path) -> list[WorkItem]:
        """Scan a directory for Yurtle work items."""
        items = []

        for md_file in directory.rglob("*.md"):
            # Check ignore patterns
            if self._should_ignore(md_file):
                continue

            item = self._parse_file(md_file)
            if item:
                items.append(item)

        return items

    def _should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored."""
        path_str = str(path.relative_to(self.repo_root))
        for pattern in self.config.paths.ignore:
            if fnmatch.fnmatch(path_str, pattern):
                return True
        return False

    def _parse_file(self, file_path: Path) -> WorkItem | None:
        """Parse a markdown file for work item data."""
        try:
            content = file_path.read_text()

            # Parse frontmatter
            frontmatter = self._parse_frontmatter(content)
            if not frontmatter:
                return None

            # Get required fields
            item_id = frontmatter.get("id")
            if not item_id:
                # Generate from filename
                item_id = file_path.stem.upper().replace("-", "_")

            item_type_str = frontmatter.get("type", "task")
            try:
                item_type = WorkItemType.from_string(item_type_str)
            except ValueError:
                # Try theme mapping
                item_type = self._map_theme_type(item_type_str)
                if not item_type:
                    return None

            status_str = frontmatter.get("status", "backlog")
            try:
                status = WorkItemStatus.from_string(status_str)
            except ValueError:
                # Try theme mapping
                status = self._map_theme_status(status_str)
                if not status:
                    status = WorkItemStatus.BACKLOG

            # Get title
            title = frontmatter.get("title", file_path.stem.replace("-", " ").title())

            # Parse optional fields
            priority = frontmatter.get("priority")
            assignee = frontmatter.get("assignee")
            tags = frontmatter.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]

            depends_on = frontmatter.get("depends_on", [])
            if isinstance(depends_on, str):
                depends_on = [d.strip() for d in depends_on.split(",")]

            related = frontmatter.get("related", [])
            if isinstance(related, str):
                related = [r.strip() for r in related.split(",")]

            created = None
            if "created" in frontmatter:
                created_val = frontmatter["created"]
                if isinstance(created_val, date):
                    created = created_val
                elif isinstance(created_val, str):
                    try:
                        created = date.fromisoformat(created_val)
                    except ValueError:
                        pass

            # Extract description (content after frontmatter, before yurtle block)
            description = self._extract_description(content)

            # Parse priority rank and value summary
            priority_rank = frontmatter.get("priority_rank")
            if priority_rank is not None:
                try:
                    priority_rank = int(priority_rank)
                except (ValueError, TypeError):
                    priority_rank = None
            value_summary = frontmatter.get("value_summary")

            # Parse resolution fields
            resolution = frontmatter.get("resolution")
            if resolution is not None:
                valid_resolutions = {
                    "completed", "superseded", "wont_do",
                    "duplicate", "obsolete", "merged",
                }
                if resolution not in valid_resolutions:
                    logger.warning(
                        f"Unknown resolution '{resolution}' in {file_path}. "
                        f"Valid values: {', '.join(sorted(valid_resolutions))}"
                    )
            superseded_by = frontmatter.get("superseded_by", [])
            if isinstance(superseded_by, str):
                superseded_by = [s.strip() for s in superseded_by.split(",")]

            compute_requirement = frontmatter.get("compute_requirement")

            # Collect remaining frontmatter keys into metadata
            _parsed_keys = {
                "id", "title", "type", "status", "priority", "assignee",
                "created", "tags", "depends_on", "related", "resolution",
                "superseded_by", "priority_rank", "value_summary",
                "compute_requirement",
            }
            metadata = {
                k: v for k, v in frontmatter.items()
                if k not in _parsed_keys and v is not None
            }
            # Preserve original status string for theme-aware rendering
            metadata["_original_status"] = status_str

            # Parse RDF graph from frontmatter + fenced blocks
            graph = self._parse_graph(content)

            return WorkItem(
                id=item_id,
                title=title,
                item_type=item_type,
                status=status,
                file_path=file_path,
                priority=priority,
                assignee=assignee,
                created=created,
                tags=tags,
                depends_on=depends_on,
                related=related,
                description=description,
                metadata=metadata,
                resolution=resolution,
                superseded_by=superseded_by,
                graph=graph,
                priority_rank=priority_rank,
                value_summary=value_summary,
                compute_requirement=compute_requirement,
            )

        except Exception as e:
            logger.debug(f"Failed to parse {file_path}: {e}")
            return None

    def _parse_frontmatter(self, content: str) -> dict[str, Any] | None:
        """Parse YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return None

        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        try:
            return yaml.safe_load(parts[1])
        except yaml.YAMLError:
            return None

    def _parse_graph(self, content: str) -> Graph | None:
        """Parse RDF graph from file content using yurtle-rdflib.

        Returns an rdflib.Graph with triples from both YAML/Turtle frontmatter
        and fenced ```turtle/```yurtle blocks in the markdown body.
        Returns None if parsing fails.
        """
        try:
            import yurtle_rdflib
            # Suppress rdflib URI warnings for placeholder URIs like
            # y3:tool_experience_{hash} that appear in prose/examples (#59).
            # rdflib uses logging.warning(), not warnings.warn().
            rdflib_logger = logging.getLogger("rdflib.term")
            old_level = rdflib_logger.level
            rdflib_logger.setLevel(logging.ERROR)
            try:
                doc = yurtle_rdflib.parse_yurtle(content)
            finally:
                rdflib_logger.setLevel(old_level)
            return doc.graph
        except Exception as e:
            logger.debug(f"Failed to parse graph: {e}")
            return None

    def _extract_description(self, content: str) -> str | None:
        """Extract description from markdown content."""
        # Remove frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2]

        # Remove yurtle and turtle knowledge blocks
        content = re.sub(r"```(?:yurtle|turtle).*?```", "", content, flags=re.DOTALL)

        # Remove heading (title)
        lines = content.strip().split("\n")
        description_lines = []
        skip_heading = True
        for line in lines:
            if skip_heading and line.startswith("#"):
                skip_heading = False
                continue
            description_lines.append(line)

        description = "\n".join(description_lines).strip()
        return description if description else None

    def _map_theme_type(self, type_str: str) -> WorkItemType | None:
        """Map theme-specific type to standard type."""
        # First try direct enum match (handles HDD types that are in the enum)
        try:
            return WorkItemType.from_string(type_str)
        except ValueError:
            pass

        theme = self.config.get_theme()
        if theme and "item_types" in theme:
            for type_id, type_def in theme["item_types"].items():
                if type_id == type_str.lower():
                    # Map common nautical types
                    mapping = {
                        "expedition": WorkItemType.FEATURE,
                        "voyage": WorkItemType.EPIC,
                        "directive": WorkItemType.TASK,
                        "hazard": WorkItemType.BUG,
                        "signal": WorkItemType.IDEA,
                    }
                    return mapping.get(type_id, WorkItemType.TASK)
        return None

    def _map_theme_status(self, status_str: str) -> WorkItemStatus | None:
        """Map theme-specific status to standard status."""
        # Common status mappings across themes
        mapping = {
            # Nautical theme
            "harbor": WorkItemStatus.BACKLOG,
            "provisioning": WorkItemStatus.READY,
            "underway": WorkItemStatus.IN_PROGRESS,
            "approaching": WorkItemStatus.REVIEW,
            "arrived": WorkItemStatus.DONE,
            # Custom statuses
            "intake": WorkItemStatus.BACKLOG,
            "planning": WorkItemStatus.READY,
            "active": WorkItemStatus.IN_PROGRESS,
            "complete": WorkItemStatus.DONE,
            "completed": WorkItemStatus.DONE,
            # Spec theme
            "draft": WorkItemStatus.BACKLOG,
            "proposed": WorkItemStatus.READY,
            "implementing": WorkItemStatus.IN_PROGRESS,
            "accepted": WorkItemStatus.DONE,
        }
        return mapping.get(status_str.lower())

    def get_board(self, board_name: str | None = None) -> Board:
        """Get the kanban board with all items.

        In multi-board mode, specify board_name to get a specific board.
        Without arguments, returns the default board.

        Args:
            board_name: Name of the board (multi-board mode) or None for default

        Returns:
            Board object with items and column configuration
        """
        # Multi-board mode
        if self.config.is_multi_board:
            return self._get_board_multi(board_name)

        # Single-board mode (cached)
        if self._board is None:
            items = self.scan()
            columns = self._get_columns_from_theme()
            column_status_map = self._get_column_status_map()
            self._board = Board(
                id="main",
                name=self.config.theme.title() + " Board",
                columns=columns,
                items=items,
                column_status_map=column_status_map,
            )
        return self._board

    def _get_board_multi(self, board_name: str | None = None) -> Board:
        """Get a specific board in multi-board mode.

        Args:
            board_name: Name of the board, or None for default/cwd-matched board

        Returns:
            Board object for the specified board
        """
        board_config = None

        if board_name:
            board_config = self.config.get_board(board_name)
            if not board_config:
                # Warn user and fall back to default
                logger.warning(f"Board '{board_name}' not found, falling back to default")
                board_config = self.config.get_default_board()
        else:
            # Try to detect from current working directory
            cwd = Path.cwd()
            board_config = self.config.get_board_for_path(cwd, self.repo_root)
            if not board_config:
                board_config = self.config.get_default_board()

        if not board_config:
            # No board found, return empty board
            return Board(
                id="empty",
                name="No Board Configured",
                columns=[],
                items=[],
            )

        # Scan items for this board only
        items = self._scan_board(board_config)

        # Get columns from board's preset, then apply WIP overrides
        columns = self._get_columns_from_preset(board_config.preset)
        columns = self._apply_wip_overrides(columns, board_config)


        column_status_map = self._get_column_status_map()

        return Board(
            id=board_config.name,
            name=f"{board_config.name.title()} Board",
            columns=columns,
            items=items,
            column_status_map=column_status_map,
        )

    def _scan_board(self, board_config: BoardConfig) -> list[WorkItem]:
        """Scan a specific board for work items.

        Args:
            board_config: Configuration for the board to scan

        Returns:
            List of work items found in the board's path
        """
        items = []
        board_path = self.repo_root / board_config.path

        if board_path.exists():
            for md_file in board_path.rglob("*.md"):
                # Check board-specific ignore patterns
                if self._should_ignore_for_board(md_file, board_config):
                    continue

                item = self._parse_file(md_file)
                if item:
                    items.append(item)

        return items

    def _should_ignore_for_board(self, path: Path, board_config: BoardConfig) -> bool:
        """Check if a path should be ignored for a specific board.

        Args:
            path: File path to check
            board_config: Board configuration with ignore patterns

        Returns:
            True if the path should be ignored
        """
        try:
            path_str = str(path.relative_to(self.repo_root))
        except ValueError:
            path_str = str(path)

        for pattern in board_config.ignore:
            if fnmatch.fnmatch(path_str, pattern):
                return True
        return False

    def _get_columns_from_preset(
        self,
        preset: str,
        wip_limit_overrides: dict[str, int] | None = None,
    ) -> list[Column]:
        """Get column definitions from a preset/theme.

        Args:
            preset: Name of the preset (e.g., 'software', 'nautical', 'hdd')
            wip_limit_overrides: Optional per-column WIP limit overrides from
                board config. Keys are column IDs (e.g., 'in_progress',
                'underway'). Overrides the theme's default wip_limit values.

        Returns:
            List of Column objects
        """
        from .config import _load_builtin_theme

        theme = _load_builtin_theme(preset, self.repo_root)
        overrides = wip_limit_overrides or {}
        columns = []

        if theme and "columns" in theme:
            for col_id, col_def in theme["columns"].items():
                # Apply override: check col_id directly, then its display name
                wip_limit = col_def.get("wip_limit")
                if col_id in overrides:
                    wip_limit = overrides[col_id]
                else:
                    # Also check the mapped status value (e.g. "underway" maps
                    # to WorkItemStatus.IN_PROGRESS whose value is "in_progress")
                    for override_key, override_val in overrides.items():
                        if override_key == col_def.get("name", "").lower().replace(" ", "_"):
                            wip_limit = override_val
                            break
                columns.append(
                    Column(
                        id=col_id,
                        name=col_def.get("name", col_id.title()),
                        order=col_def.get("order", 0),
                        wip_limit=wip_limit,
                        description=col_def.get("description"),
                    )
                )
        else:
            # Default columns
            columns = [
                Column(id="backlog", name="Backlog", order=1),
                Column(id="ready", name="Ready", order=2),
                Column(
                    id="in_progress",
                    name="In Progress",
                    order=3,
                    wip_limit=overrides.get("in_progress", 3),
                ),
                Column(id="review", name="Review", order=4),
                Column(id="done", name="Done", order=5),
            ]

        return sorted(columns, key=lambda c: c.order)

    def _apply_wip_overrides(
        self, columns: list[Column], board_config: BoardConfig
    ) -> list[Column]:
        """Apply board-level WIP limit overrides to columns.

        Merges overrides from:
        1. BoardConfig.wip_limits (YAML config)
        2. Yurtle WIP policy file (.yurtle-kanban/wip-policy.md)

        The Yurtle policy takes precedence over YAML config, which takes
        precedence over theme defaults.
        """
        from .config import load_wip_policy

        # Collect overrides: YAML first, then Yurtle on top
        wip_overrides = board_config.wip_limits

        # Board-level unlimited: clear all WIP limits
        if wip_overrides is None:
            for col in columns:
                col.wip_limit = None
                col.type_wip_limits = None
            return columns

        # Load Yurtle policy if available
        config_dir = self.repo_root / ".yurtle-kanban"
        yurtle_policy = load_wip_policy(config_dir)
        if yurtle_policy and board_config.name in yurtle_policy:
            board_policy = yurtle_policy[board_config.name]
            if board_policy is None:
                # Yurtle policy says this board is unlimited
                for col in columns:
                    col.wip_limit = None
                    col.type_wip_limits = None
                return columns
            # Merge: Yurtle policy overrides YAML config
            merged = dict(wip_overrides)
            merged.update(board_policy)
            wip_overrides = merged

        if not wip_overrides:
            return columns

        for col in columns:
            if col.id not in wip_overrides:
                continue

            override = wip_overrides[col.id]

            if override is None:
                # Explicitly unlimited column
                col.wip_limit = None
                col.type_wip_limits = None
            elif isinstance(override, int):
                # Legacy aggregate limit
                col.wip_limit = override
            elif isinstance(override, dict):
                # Per-type limits
                col.type_wip_limits = {}
                for type_name, limit in override.items():
                    col.type_wip_limits[type_name] = limit
                # Clear legacy limit when per-type is set
                col.wip_limit = None

        return columns

    def _get_column_status_map(self) -> dict[str, WorkItemStatus]:
        """Get mapping from column IDs to WorkItemStatus for themed columns.

        Loads status_mappings from all configured board presets, then
        falls back to hardcoded defaults for known themes.
        """
        mappings: dict[str, WorkItemStatus] = {
            # Standard software theme (identity mapping)
            "backlog": WorkItemStatus.BACKLOG,
            "ready": WorkItemStatus.READY,
            "in_progress": WorkItemStatus.IN_PROGRESS,
            "review": WorkItemStatus.REVIEW,
            "done": WorkItemStatus.DONE,
            "blocked": WorkItemStatus.BLOCKED,
            # Nautical theme
            "harbor": WorkItemStatus.BACKLOG,
            "provisioning": WorkItemStatus.READY,
            "underway": WorkItemStatus.IN_PROGRESS,
            "approaching": WorkItemStatus.REVIEW,
            "arrived": WorkItemStatus.DONE,
            # Spec theme
            "draft": WorkItemStatus.BACKLOG,
            "proposed": WorkItemStatus.READY,
            "implementing": WorkItemStatus.IN_PROGRESS,
            "accepted": WorkItemStatus.DONE,
            # HDD theme (Hypothesis-Driven Development)
            "active": WorkItemStatus.IN_PROGRESS,
            "complete": WorkItemStatus.DONE,
            "abandoned": WorkItemStatus.BLOCKED,
        }

        # Load status_mappings from all configured board presets
        from .config import _load_builtin_theme

        presets_seen: set[str] = set()
        if self.config.is_multi_board:
            for board_config in self.config.boards:
                if board_config.preset in presets_seen:
                    continue
                presets_seen.add(board_config.preset)
                theme = _load_builtin_theme(board_config.preset, self.repo_root)
                if theme and "status_mappings" in theme:
                    for alias, canonical in theme["status_mappings"].items():
                        try:
                            mappings[alias] = WorkItemStatus.from_string(canonical)
                        except ValueError:
                            pass

        return mappings

    def _get_columns_from_theme(self) -> list[Column]:
        """Get column definitions from theme."""
        theme = self.config.get_theme()
        columns = []

        if theme and "columns" in theme:
            for col_id, col_def in theme["columns"].items():
                columns.append(
                    Column(
                        id=col_id,
                        name=col_def.get("name", col_id.title()),
                        order=col_def.get("order", 0),
                        wip_limit=col_def.get("wip_limit"),
                        description=col_def.get("description"),
                    )
                )
        else:
            # Default software columns
            columns = [
                Column("backlog", "Backlog", 1),
                Column("ready", "Ready", 2, wip_limit=5),
                Column("in_progress", "In Progress", 3, wip_limit=3),
                Column("review", "Review", 4, wip_limit=2),
                Column("done", "Done", 5),
            ]

        return sorted(columns, key=lambda c: c.order)

    def get_item(self, item_id: str) -> WorkItem | None:
        """Get a work item by ID."""
        if not self._items:
            self.scan()
        return self._items.get(item_id)

    def get_items(
        self,
        status: WorkItemStatus | None = None,
        item_type: WorkItemType | None = None,
        assignee: str | None = None,
        board: str | None = None,
        priority: list[str] | None = None,
    ) -> list[WorkItem]:
        """Get items with optional filters.

        Args:
            status: Filter by status
            item_type: Filter by type
            assignee: Filter by assignee
            board: Filter to a specific board (multi-board mode only).
                   If None, returns items from all boards (default behavior).
            priority: Filter by priority level(s) (e.g. ["critical", "high"]).
        """
        if board and self.config.is_multi_board:
            board_config = self.config.get_board(board)
            if board_config:
                items = self._scan_board(board_config)
            else:
                items = []
        else:
            if not self._items:
                self.scan()
            items = list(self._items.values())

        if status:
            items = [i for i in items if i.status == status]
        if item_type:
            items = [i for i in items if i.item_type == item_type]
        if assignee:
            items = [i for i in items if i.assignee == assignee]
        if priority:
            items = [i for i in items if (i.priority or "medium") in priority]

        return sorted(items, key=lambda i: (-i.priority_score, -i.numeric_id))

    def _get_type_directory(self, item_type: WorkItemType, board_name: str | None = None) -> Path:
        """Get the directory for placing a file of this item type.

        Resolution order:
        1. Theme item_types[type].path (explicit per-type directory)
           In multi-board mode, searches all boards' themes for the type.
        2. PathConfig attributes (features, bugs, epics, tasks)
        3. scan_paths keyword match (e.g., "expeditions/" for expedition type)
        4. Fall back to paths.root
        """
        # Priority 1: Theme-defined path
        if self.config.is_multi_board:
            # Multi-board: check specific board or search all boards
            if board_name:
                theme = self.config.get_theme(board_name)
                if theme and "item_types" in theme:
                    type_def = theme["item_types"].get(item_type.value, {})
                    if "path" in type_def:
                        return self.repo_root / type_def["path"]
            else:
                # Search all boards for a theme that defines this item type
                for board in self.config.boards:
                    board_theme = board.get_theme(self.repo_root)
                    if board_theme and "item_types" in board_theme:
                        type_def = board_theme["item_types"].get(item_type.value, {})
                        if "path" in type_def:
                            return self.repo_root / type_def["path"]
        else:
            theme = self.config.get_theme()
            if theme and "item_types" in theme:
                type_def = theme["item_types"].get(item_type.value, {})
                if "path" in type_def:
                    return self.repo_root / type_def["path"]

        # Priority 2: Legacy PathConfig attributes (features, bugs, epics, tasks)
        type_path = getattr(self.config.paths, item_type.value + "s", None)
        if type_path:
            return self.repo_root / type_path

        # Priority 3: Match scan_paths by type keyword
        irregular_plurals = {
            "hypothesis": "hypotheses",
            "literature": "literature",
        }
        plural = irregular_plurals.get(item_type.value, item_type.value + "s")
        for scan_path in self.config.paths.scan_paths:
            if plural in scan_path.lower() or item_type.value in scan_path.lower():
                return self.repo_root / scan_path

        # Priority 4: Fall back to root
        return self.repo_root / (self.config.paths.root or "work/")

    @staticmethod
    def _slugify(title: str) -> str:
        """Convert title to filename-safe slug."""
        slug = re.sub(r"[^a-zA-Z0-9\s-]", "", title)
        slug = re.sub(r"\s+", "-", slug.strip())
        return slug[:50]  # Limit length

    def create_item(
        self,
        item_type: WorkItemType,
        title: str,
        path: Path | None = None,
        priority: str = "medium",
        assignee: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        content: str | None = None,
        item_id: str | None = None,
    ) -> WorkItem:
        """Create a new work item.

        Args:
            content: Pre-rendered file content (e.g., from TemplateEngine).
                     When provided, writes this instead of item.to_markdown().
            item_id: Explicit ID to use instead of auto-allocating.
                     Useful for HDD types with non-standard ID formats
                     (e.g., H130.1, EXPR-130, PAPER-130).
        """
        # Generate or use provided ID
        if item_id is None:
            prefix = self._get_type_prefix(item_type)
            next_num = self._get_next_id_number(prefix)
            item_id = f"{prefix}-{next_num:03d}"

        # Determine file path
        if path is None:
            type_dir = self._get_type_directory(item_type)
            slug = self._slugify(title)
            filename = f"{item_id}-{slug}.md" if slug else f"{item_id}.md"
            path = type_dir / filename

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Create work item
        item = WorkItem(
            id=item_id,
            title=title,
            item_type=item_type,
            status=WorkItemStatus.BACKLOG,
            file_path=path,
            priority=priority,
            assignee=assignee,
            created=date.today(),
            description=description,
            tags=tags or [],
        )

        # Write file — use pre-rendered content if provided
        file_content = content if content is not None else item.to_markdown()
        path.write_text(file_content)

        # Parse RDF graph from written content
        item.graph = self._parse_graph(file_content)

        # Add to cache
        self._items[item_id] = item

        # Fire hooks (after successful create)
        self._fire_create_hook(item)

        return item

    def _has_remote(self) -> bool:
        """Check if git remote 'origin' is configured."""
        try:
            result = subprocess.run(
                ["git", "remote"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
            )
            return "origin" in result.stdout.split()
        except Exception:
            return False

    def create_item_and_push(
        self,
        item_type: WorkItemType,
        title: str,
        priority: str = "medium",
        assignee: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        max_retries: int = 3,
        content: str | None = None,
        item_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically create a work item, commit, and push to remote.

        Single-command flow that prevents ID conflicts:
        1. Pull latest from remote (if remote exists)
        2. Scan for highest ID
        3. Create item file + update _ID_ALLOCATIONS.json
        4. Commit both files
        5. Push to remote (if remote exists)
        6. On push failure: reset, pull --rebase, retry with new ID

        If no remote is configured, gracefully degrades to local
        allocate + create + commit (no push, no retry needed).

        Args:
            content: Pre-rendered file content (e.g., from TemplateEngine).
            item_id: Explicit ID (bypasses auto-allocation). For HDD types
                     with non-standard formats (H130.1, EXPR-130, PAPER-130).

        Returns:
            dict with 'success', 'item', 'id', 'pushed', and 'message' keys
        """
        import json as json_mod

        has_remote = self._has_remote()
        attempts = max_retries if has_remote else 1

        for attempt in range(attempts):
            # Step 1: Pull latest (only if remote exists)
            if has_remote:
                try:
                    subprocess.run(
                        ["git", "pull", "origin", "main"],
                        cwd=self.repo_root,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except Exception as e:
                    logger.warning(f"Git pull failed: {e}")

            # Step 2: Re-scan
            self._items.clear()
            self.scan()

            # Step 3: Allocate ID (or use provided)
            if item_id is not None:
                current_id = item_id
            else:
                prefix = self._get_type_prefix(item_type)
                next_num = self._get_next_id_number(prefix)
                current_id = f"{prefix}-{next_num:03d}"

            # Step 4: Create the file
            type_dir = self._get_type_directory(item_type)
            slug = self._slugify(title)
            filename = f"{current_id}-{slug}.md" if slug else f"{current_id}.md"
            file_path = type_dir / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)

            item = WorkItem(
                id=current_id,
                title=title,
                item_type=item_type,
                status=WorkItemStatus.BACKLOG,
                file_path=file_path,
                priority=priority,
                assignee=assignee,
                created=date.today(),
                description=description,
                tags=tags or [],
            )
            if content is not None:
                file_path.write_text(content)
            else:
                file_path.write_text(item.to_markdown())

            # Step 5: Update _ID_ALLOCATIONS.json
            lock_file = self.repo_root / ".kanban" / "_ID_ALLOCATIONS.json"
            lock_file.parent.mkdir(parents=True, exist_ok=True)

            allocations = []
            if lock_file.exists():
                try:
                    allocations = json_mod.loads(lock_file.read_text())
                except Exception:
                    allocations = []

            alloc_prefix = self._get_type_prefix(item_type)
            # Extract trailing number for allocation record
            num_match = re.search(r"(\d+)$", current_id)
            alloc_num = int(num_match.group(1)) if num_match else 0

            allocations.append(
                {
                    "id": current_id,
                    "prefix": alloc_prefix,
                    "number": alloc_num,
                    "allocated_at": datetime.now().isoformat(),
                    "allocated_by": self._get_git_user(),
                }
            )
            allocations = allocations[-100:]
            lock_file.write_text(json_mod.dumps(allocations, indent=2))

            # Step 6: Commit both files
            try:
                subprocess.run(
                    ["git", "add", str(file_path), str(lock_file)],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"Create {current_id}: {title}"],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                logger.warning(f"Git commit failed: {e}")
                return {
                    "success": False,
                    "item": None,
                    "id": None,
                    "pushed": False,
                    "message": f"Git commit failed: {e}",
                }

            # Step 7: Push (only if remote exists)
            if not has_remote:
                self._items[current_id] = item
                self._fire_create_hook(item)
                return {
                    "success": True,
                    "item": item,
                    "id": current_id,
                    "pushed": False,
                    "message": (
                        f"Created and committed {current_id}: "
                        f"{title} (no remote configured)"
                    ),
                }

            push_result = subprocess.run(
                ["git", "push"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if push_result.returncode == 0:
                self._items[current_id] = item
                self._fire_create_hook(item)
                return {
                    "success": True,
                    "item": item,
                    "id": current_id,
                    "pushed": True,
                    "message": f"Created and pushed {current_id}: {title}",
                }

            # Push failed — another agent got there first
            logger.warning(f"Push failed (attempt {attempt + 1}), rebasing: {push_result.stderr}")

            # Clean up: remove the file and reset the commit
            try:
                subprocess.run(
                    ["git", "reset", "HEAD~1"],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                )
                if file_path.exists():
                    file_path.unlink()
            except Exception:
                pass

            # Pull latest and retry
            try:
                subprocess.run(
                    ["git", "pull", "origin", "main", "--rebase"],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
            except Exception as e:
                logger.warning(f"Rebase failed on retry {attempt + 1}: {e}")

        return {
            "success": False,
            "item": None,
            "id": None,
            "pushed": False,
            "message": f"Failed to create item after {max_retries} retries",
        }

    def _get_type_prefix(self, item_type: WorkItemType) -> str:
        """Get ID prefix for item type."""
        theme = self.config.get_theme()
        if theme and "item_types" in theme:
            for type_id, type_def in theme["item_types"].items():
                if type_id == item_type.value:
                    return type_def.get("id_prefix", item_type.value[:4].upper())
        # Default prefixes (software + nautical + HDD themes)
        prefixes = {
            # Software theme
            WorkItemType.FEATURE: "FEAT",
            WorkItemType.BUG: "BUG",
            WorkItemType.EPIC: "EPIC",
            WorkItemType.ISSUE: "ISSUE",
            WorkItemType.TASK: "TASK",
            WorkItemType.IDEA: "IDEA",
            # Nautical theme
            WorkItemType.EXPEDITION: "EXP",
            WorkItemType.VOYAGE: "VOY",
            WorkItemType.DIRECTIVE: "DIR",
            WorkItemType.HAZARD: "HAZ",
            WorkItemType.SIGNAL: "SIG",
            WorkItemType.CHORE: "CHORE",
            # HDD theme
            WorkItemType.LITERATURE: "LIT",
            WorkItemType.PAPER: "PAPER",
            WorkItemType.HYPOTHESIS: "H",
            WorkItemType.EXPERIMENT: "EXPR",
            WorkItemType.MEASURE: "M",
        }
        return prefixes.get(item_type, "ITEM")

    def _get_next_id_number(self, prefix: str) -> int:
        """Get next available ID number for a prefix.

        Scans all three sources of truth:
        1. _ID_ALLOCATIONS.json (allocated but possibly not yet on disk)
        2. Item IDs from frontmatter (via self._items)
        3. Filenames directly (to catch files without proper frontmatter)

        Returns max across all sources + 1.
        """
        import json

        if not self._items:
            self.scan()

        max_num = 0

        # Source 1: Check _ID_ALLOCATIONS.json for previously allocated IDs
        lock_file = self.repo_root / ".kanban" / "_ID_ALLOCATIONS.json"
        if lock_file.exists():
            try:
                allocations = json.loads(lock_file.read_text())
                for alloc in allocations:
                    if alloc.get("prefix") != prefix:
                        continue
                    # ⚠ The `prefix` field alone is NOT the id space. A
                    # paper-scoped hypothesis H130.1 is stamped
                    # `prefix: "H", number: 1`, so matching on the prefix
                    # counted it toward the DASHED H-NNN space: in a repo with a
                    # 3-hypothesis paper, the first unparented hypothesis came
                    # out H-004. Sources 2 and 3 below have always filtered on
                    # `prefix + "-"`; this one did not, so the three sources
                    # disagreed about what counts.
                    #
                    # A legacy record with no stored id keeps counting — this
                    # can then only ever SKIP an id, never mint a duplicate.
                    alloc_id = alloc.get("id")
                    if alloc_id is not None and not str(alloc_id).startswith(
                        prefix + "-"
                    ):
                        continue
                    max_num = max(max_num, alloc.get("number", 0))
            except (json.JSONDecodeError, Exception):
                pass

        # Source 2: Check IDs from parsed items
        # Use regex to extract trailing number — handles multi-segment prefixes
        # like IDEA-R-003 where split("-")[1] would give "R" not "003"
        for existing_id in self._items.keys():
            if existing_id.startswith(prefix + "-"):
                match = re.search(r"(\d+)$", existing_id)
                if match:
                    max_num = max(max_num, int(match.group(1)))

        # Source 3: Scan filenames directly to catch files without frontmatter
        for scan_path in self.config.get_work_paths():
            full_path = self.repo_root / scan_path
            if full_path.exists():
                for md_file in full_path.rglob("*.md"):
                    filename = md_file.stem  # e.g., "EXP-608-Some-Title"
                    if filename.startswith(prefix + "-"):
                        # Extract the number immediately after the prefix
                        suffix = filename[len(prefix) + 1:]  # e.g., "608-Some-Title"
                        match = re.match(r"(\d+)", suffix)
                        if match:
                            max_num = max(max_num, int(match.group(1)))

        return max_num + 1

    def get_next_unparented_hypothesis_id(self) -> str:
        """Get the next id for a hypothesis that belongs to no paper.

        Returns the ordinary `H-NNN` form every other work type uses, allocated
        through the same three-source scan as everything else — so an unparented
        hypothesis is not a special case, it is a normal item.

        The two id spaces do not collide: paper-scoped ids are `H{paper}.{n}`
        with a DOT and no dash, and all three sources in `_get_next_id_number`
        now filter on `prefix + "-"`, so `H130.1` is invisible to this allocator
        and `H-001` is invisible to `get_next_hypothesis_number` (which scans
        for the `H{paper}.` prefix directly and never reads this one).

        ⚠ That is true BY REPAIR, not by construction — do not restate it as
        self-evident. Source 1 matched on the `prefix` FIELD alone, and every
        `--push`-created `H130.1` is stamped `prefix: "H"`, so before the fix
        the first unparented hypothesis in a repo with a 3-hypothesis paper
        came out `H-004`. The non-collision is a property this code has to
        keep, which is why `--paper` is now constrained to a non-negative int:
        `--paper=-1` yields `H-1.1`, which lands squarely in the dashed space.
        """
        return f"H-{self._get_next_id_number('H'):03d}"

    @staticmethod
    def _is_deliberately_unparented(item_id: str) -> bool:
        """True if this hypothesis id declares that it belongs to no paper.

        Paper-scoped ids are `H{paper}.{n}` — a DOT. So an id in the DASHED
        space is unparented BY ITS FORM, not by an omission someone should fix,
        and the validators must not call it orphaned.

        This subsumes the pre-existing `H-DRAFT...` convention rather than
        sitting beside it: that spelling also starts with `H-`, and it meant the
        same thing — a hypothesis deliberately not attached to a paper.
        """
        return item_id.startswith("H-")

    def get_next_hypothesis_number(self, paper_num: str) -> int:
        """Get next hypothesis number for a paper.

        Scans existing items for H{paper_num}.N patterns and returns max N + 1.
        E.g., if H130.1 and H130.2 exist, returns 3.
        """
        if not self._items:
            self.scan()

        max_n = 0
        prefix = f"H{paper_num}."

        for existing_id in self._items.keys():
            if existing_id.startswith(prefix):
                suffix = existing_id[len(prefix):]
                try:
                    max_n = max(max_n, int(suffix))
                except ValueError:
                    pass

        return max_n + 1

    # ------------------------------------------------------------------
    # Parent turtle block auto-update (EXP-1026)
    # ------------------------------------------------------------------

    # Inverse relationships: when a child HDD item is created, what triple
    # to add to the parent's turtle block.
    _INVERSE_RELATIONS: dict[str, dict[str, str]] = {
        "hypothesis": {
            "predicate_ns": "paper",
            "predicate_local": "hasHypothesis",
            "child_prefix": "hyp",
        },
        "experiment": {
            "predicate_ns": "hyp",
            "predicate_local": "hasExperiment",
            "child_prefix": "expr",
        },
        "literature": {
            "predicate_ns": "idea",
            "predicate_local": "hasLiterature",
            "child_prefix": "lit",
        },
    }

    # Regex to find the first fenced ```turtle or ```yurtle block.
    _TURTLE_BLOCK_RE = re.compile(
        r"(```(?:turtle|yurtle)\s*\r?\n)(.*?)(^```)",
        re.DOTALL | re.MULTILINE,
    )

    def update_parent_turtle_block(
        self,
        parent_id: str,
        child_type: str,
        child_id: str,
        push: bool = False,
    ) -> bool:
        """Update a parent item's turtle block with an inverse reference to a child.

        When a child HDD item is created (hypothesis, experiment, literature),
        this method adds a triple to the parent's turtle block so the parent
        knows about its children.

        Uses rdflib for correct Turtle parsing, triple addition, and
        serialization. Requires yurtle-rdflib to be installed.

        Args:
            parent_id: ID of the parent item (e.g., "PAPER-130", "H130.1").
            child_type: Type of the child item ("hypothesis", "experiment",
                        "literature").
            child_id: ID of the child item (e.g., "H130.1", "EXPR-130").
            push: Whether to commit and push the parent file update.

        Returns:
            True if the parent was updated, False otherwise.
        """
        relation = self._INVERSE_RELATIONS.get(child_type)
        if relation is None:
            logger.debug(f"No inverse relation defined for child type: {child_type}")
            return False

        parent = self.get_item(parent_id)
        if parent is None:
            logger.warning(f"Parent {parent_id} not found — skipping inverse reference")
            return False

        if not parent.file_path.exists():
            logger.warning(f"Parent file {parent.file_path} missing — skipping")
            return False

        content = parent.file_path.read_text()
        match = self._TURTLE_BLOCK_RE.search(content)
        if not match:
            logger.warning(f"No turtle block in {parent_id} — skipping inverse reference")
            return False

        # Build rdflib URIs for the predicate and child object
        pred_ns = Namespace(PREFIXES[relation["predicate_ns"]])
        predicate = pred_ns[relation["predicate_local"]]
        child_ns = Namespace(PREFIXES[relation["child_prefix"]])
        child_uri = child_ns[child_id]

        # Modify the turtle block using rdflib
        old_inner = match.group(2)
        new_inner, changed = self._modify_turtle_block(old_inner, predicate, child_uri)
        if not changed:
            return False

        # Replace the turtle block in the file
        new_block = match.group(1) + new_inner + "\n" + match.group(3)
        new_content = content[: match.start()] + new_block + content[match.end() :]
        parent.file_path.write_text(new_content)

        # Re-parse graph for the updated parent
        parent.graph = self._parse_graph(new_content)

        if push:
            self._commit_and_push_file(
                parent.file_path,
                f"chore(hdd): link {child_id} → {parent_id}",
            )

        return True

    def _modify_turtle_block(
        self,
        turtle_content: str,
        predicate_uri: Any,
        child_uri: Any,
    ) -> tuple[str, bool]:
        """Parse a turtle block, add a triple, and serialize back.

        Uses rdflib for correct Turtle parsing and serialization.
        Handles relative URIs like <#PAPER-130> via a synthetic base URI.

        Note: rdflib's Turtle serializer may reorder triples and change
        indentation compared to the original hand-written block. This is
        the correct trade-off (correctness over formatting).

        Args:
            turtle_content: Inner content of a fenced turtle block (between
                            the ``` fences, not including them).
            predicate_uri: rdflib URIRef for the predicate to add.
            child_uri: rdflib URIRef for the child object to add.

        Returns:
            Tuple of (new_content, changed). changed is False if the triple
            already exists or no subject was found.
        """
        base_uri = URIRef("urn:yurtle:block")
        g = Graph()
        try:
            g.parse(data=turtle_content, format="turtle", publicID=str(base_uri))
        except Exception as e:
            logger.warning(f"Failed to parse turtle block for modification: {e}")
            return turtle_content, False

        # Find subject — first URIRef (skip BNodes)
        subject = next(
            (s for s in g.subjects() if isinstance(s, URIRef)),
            None,
        )
        if subject is None:
            return turtle_content, False

        # Idempotency: skip if triple already present
        if (subject, predicate_uri, child_uri) in g:
            return turtle_content, False

        # Add the inverse triple
        g.add((subject, predicate_uri, child_uri))

        # Bind all HDD prefixes for clean serialization
        for name, uri in PREFIXES.items():
            g.bind(name, Namespace(uri))

        # Serialize with base to preserve <#ID> relative URIs
        result = g.serialize(format="turtle", base=base_uri)
        if isinstance(result, bytes):
            result = result.decode("utf-8")

        # Strip @base declaration (original blocks don't have it)
        lines = result.strip().split("\n")
        lines = [line for line in lines if not line.startswith("@base ")]
        return "\n".join(lines), True

    def _merge_into_existing_block(
        self, content: str, match: re.Match, missing: Graph
    ) -> str:
        """Merge missing triples into an existing fenced turtle block.

        Parses the existing block with rdflib, adds the missing triples,
        serializes back, and replaces the block in place.
        """
        base_uri = URIRef("urn:yurtle:block")
        g = Graph()
        try:
            g.parse(data=match.group(2), format="turtle", publicID=str(base_uri))
        except Exception as e:
            logger.warning(f"Failed to parse existing block for merge: {e}")
            # Fall back to inserting a new block
            block = self._serialize_as_turtle_block(missing)
            return self._insert_turtle_block(content, block)

        # Add missing triples (subjects already normalized by caller)
        for s, p, o in missing:
            g.add((s, p, o))

        # Bind prefixes and serialize
        for name, uri in PREFIXES.items():
            g.bind(name, Namespace(uri))
        result = g.serialize(format="turtle", base=base_uri)
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        lines = result.strip().split("\n")
        lines = [line for line in lines if not line.startswith("@base ")]
        merged_inner = "\n".join(lines) + "\n"

        # Replace the old block content with the merged content
        return content[:match.start(2)] + merged_inner + content[match.end(2):]

    def _commit_and_push_file(self, file_path: Path, message: str) -> bool:
        """Commit a single file change and push to remote.

        Used for follow-up operations (e.g., parent turtle block updates)
        after the main create-and-push.

        Returns:
            True if commit (and optional push) succeeded, False otherwise.
        """
        try:
            subprocess.run(
                ["git", "add", str(file_path)],
                cwd=self.repo_root,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.repo_root,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f"Git commit failed for parent update: {e}")
            return False

        if self._has_remote():
            try:
                subprocess.run(
                    ["git", "push"],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                logger.warning(f"Git push failed for parent update: {e}")
                return False

        return True

    # ------------------------------------------------------------------
    # Backfill: add turtle blocks to HDD files that lack them
    # ------------------------------------------------------------------

    def backfill_turtle_blocks(
        self, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        """Scan HDD items and add missing turtle knowledge blocks.

        Uses graph diffing: builds an "expected" rdflib Graph from each file's
        YAML frontmatter, compares against the file's existing graph (parsed
        by yurtle-rdflib), and serializes only the missing triples as a new
        fenced turtle block.

        Args:
            dry_run: If True, report what would change without modifying files.

        Returns:
            List of result dicts with keys: path, id, type, action,
            triples_added.
        """
        results: list[dict[str, Any]] = []
        for item in self.get_items():
            content = item.file_path.read_text()
            frontmatter = self._parse_frontmatter(content)
            if not frontmatter:
                continue

            raw_type = frontmatter.get("type", "")
            hdd_type = _TYPE_ALIASES.get(raw_type, raw_type)
            if hdd_type not in _BACKFILL_TYPES:
                continue

            # Build expected graph from frontmatter fields
            want = self._build_expected_graph(hdd_type, frontmatter)
            if len(want) == 0:
                continue

            # Parse existing graph (frontmatter TTL + fenced blocks)
            have_raw = self._parse_graph(content) or Graph()

            # Normalize subjects: parse_yurtle resolves <#ID> relative to
            # the file path (file:///.../#ID), but _build_expected_graph
            # uses the synthetic base (urn:yurtle:block#ID).  Remap any
            # subject whose fragment matches the item ID so the diff works.
            item_id = str(frontmatter.get("id", ""))
            target_subject = URIRef(f"urn:yurtle:block#{item_id}")
            have = Graph()
            for s, p, o in have_raw:
                if str(s).endswith(f"#{item_id}"):
                    have.add((target_subject, p, o))
                else:
                    have.add((s, p, o))

            # Diff: triples we want but don't have
            missing = want - have
            if len(missing) == 0:
                results.append({
                    "path": str(item.file_path),
                    "id": item.id,
                    "type": hdd_type,
                    "action": "up_to_date",
                    "triples_added": 0,
                })
                continue

            if not dry_run:
                # If file already has a turtle block, merge into it;
                # otherwise insert a new one.
                match = self._TURTLE_BLOCK_RE.search(content)
                if match:
                    new_content = self._merge_into_existing_block(
                        content, match, missing,
                    )
                else:
                    block = self._serialize_as_turtle_block(missing)
                    new_content = self._insert_turtle_block(content, block)
                item.file_path.write_text(new_content)
                item.graph = self._parse_graph(new_content) or Graph()

            results.append({
                "path": str(item.file_path),
                "id": item.id,
                "type": hdd_type,
                "action": "backfill" if not dry_run else "would_backfill",
                "triples_added": len(missing),
            })

        return results

    def _build_expected_graph(
        self, hdd_type: str, frontmatter: dict[str, Any]
    ) -> Graph:
        """Build an rdflib Graph of expected triples from frontmatter fields.

        Maps YAML frontmatter relationship fields to the RDF triples that
        TurtleBlockBuilder would generate for a new item of this type.
        """
        g = Graph()
        item_id = str(frontmatter.get("id", ""))
        if not item_id:
            return g

        # Use synthetic base URI for relative <#ID> subjects
        subject = URIRef(f"urn:yurtle:block#{item_id}")
        title = frontmatter.get("title", "")

        # Type triple (all HDD types)
        rdf_class = _HDD_TYPE_CLASSES.get(hdd_type)
        if rdf_class:
            g.add((subject, RDF.type, rdf_class))

        # Label (all types)
        if title:
            g.add((subject, RDFS.label, Literal(title)))

        # Type-specific relationship triples
        if hdd_type == "hypothesis":
            paper = frontmatter.get("paper")
            if paper:
                paper_num = _normalize_paper_num(paper)
                g.add((subject, _HYP.paper, _PAPER_NS[f"PAPER-{paper_num}"]))
            target = frontmatter.get("target")
            if target:
                g.add((subject, _HYP.target, Literal(str(target))))
            measures = frontmatter.get("measures")
            if measures:
                for m in (measures if isinstance(measures, list) else [measures]):
                    g.add((subject, _HYP.measuredBy, _MEASURE[str(m)]))
            source_idea = frontmatter.get("source_idea")
            if source_idea:
                g.add((subject, _HYP.sourceIdea, _IDEA[str(source_idea)]))
            literature = frontmatter.get("literature")
            if literature:
                for lit in (literature if isinstance(literature, list) else [literature]):
                    g.add((subject, _HYP.informedBy, _LIT[str(lit)]))

        elif hdd_type == "experiment":
            paper = frontmatter.get("paper")
            if paper:
                paper_num = _normalize_paper_num(paper)
                g.add((subject, _EXPR.paper, _PAPER_NS[f"PAPER-{paper_num}"]))
            hypotheses = frontmatter.get("hypotheses", [])
            if hypotheses:
                first_hyp = hypotheses[0] if isinstance(hypotheses, list) else hypotheses
                g.add((subject, _EXPR.hypothesis, _HYP[str(first_hyp)]))
            measures = frontmatter.get("measures")
            if measures:
                for m in (measures if isinstance(measures, list) else [measures]):
                    g.add((subject, _EXPR.measure, _MEASURE[str(m)]))

        elif hdd_type == "measure":
            unit = frontmatter.get("unit")
            if unit:
                g.add((subject, _MEASURE.unit, Literal(str(unit))))
            category = frontmatter.get("category")
            if category:
                g.add((subject, _MEASURE.category, Literal(str(category))))

        elif hdd_type == "literature":
            source_idea = frontmatter.get("source_idea")
            if source_idea:
                g.add((subject, _LIT.explores, _IDEA[str(source_idea)]))

        return g

    def _serialize_as_turtle_block(self, graph: Graph) -> str:
        """Serialize an rdflib Graph as a fenced ```turtle block.

        Uses rdflib's Turtle serializer with HDD prefix bindings.
        Same pattern as _modify_turtle_block(): synthetic base URI,
        strip @base declaration from output.

        Works on a copy to avoid mutating the caller's graph.
        """
        base_uri = URIRef("urn:yurtle:block")

        # Work on a copy to avoid mutating the input graph
        work = Graph() + graph

        # Bind all HDD prefixes for clean serialization
        for name, uri in PREFIXES.items():
            work.bind(name, Namespace(uri))

        result = work.serialize(format="turtle", base=base_uri)
        if isinstance(result, bytes):
            result = result.decode("utf-8")

        # Strip @base declaration (not used in our blocks)
        lines = result.strip().split("\n")
        lines = [line for line in lines if not line.startswith("@base ")]
        inner = "\n".join(lines)

        return f"```turtle\n{inner}\n```"

    def _insert_turtle_block(self, content: str, block: str) -> str:
        """Insert a fenced turtle block after YAML frontmatter.

        Places the block between the closing --- and the first # heading.
        """
        if not content.startswith("---"):
            return content + "\n\n" + block + "\n"

        parts = content.split("---", 2)
        if len(parts) < 3:
            return content + "\n\n" + block + "\n"

        after_frontmatter = parts[2]
        return (
            parts[0] + "---" + parts[1] + "---\n\n"
            + block + "\n" + after_frontmatter.lstrip("\n")
        )

    def allocate_next_id(
        self,
        prefix: str,
        sync_remote: bool = True,
        commit_allocation: bool = True,
    ) -> dict[str, Any]:
        """Allocate the next available ID for a prefix with git synchronization.

        This method prevents duplicate IDs when multiple agents create work items
        concurrently by:
        1. Fetching latest changes from remote (if sync_remote=True)
        2. Scanning all files to find the highest ID
        3. Writing an allocation lock file and committing it
        4. Returning the allocated ID

        Args:
            prefix: The ID prefix (e.g., "EXP", "FEAT", "BUG")
            sync_remote: Whether to fetch latest from remote first
            commit_allocation: Whether to commit the allocation lock file

        Returns:
            dict with 'id', 'prefix', 'number', and 'success' keys
        """
        import json
        from datetime import datetime

        prefix = prefix.upper()

        # Step 1: Fetch latest from remote
        if sync_remote:
            try:
                result = subprocess.run(
                    ["git", "fetch", "origin"],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    logger.warning(f"Git fetch failed: {result.stderr}")
            except subprocess.TimeoutExpired:
                logger.warning("Git fetch timed out")
            except Exception as e:
                logger.warning(f"Git fetch error: {e}")

        # Step 2: Re-scan to get latest items
        self._items.clear()
        self.scan()

        # Step 3: Find next available number
        next_num = self._get_next_id_number(prefix)
        item_id = f"{prefix}-{next_num:03d}"

        # Step 4: Write allocation lock file (only if committing)
        if commit_allocation:
            lock_file = self.repo_root / ".kanban" / "_ID_ALLOCATIONS.json"
            lock_file.parent.mkdir(parents=True, exist_ok=True)

            # Load existing allocations
            allocations = []
            if lock_file.exists():
                try:
                    allocations = json.loads(lock_file.read_text())
                except (json.JSONDecodeError, Exception):
                    allocations = []

            # Add new allocation
            allocation = {
                "id": item_id,
                "prefix": prefix,
                "number": next_num,
                "allocated_at": datetime.now().isoformat(),
                "allocated_by": self._get_git_user(),
            }
            allocations.append(allocation)

            # Keep only recent allocations (last 100)
            allocations = allocations[-100:]
            lock_file.write_text(json.dumps(allocations, indent=2))

        # Step 5: Commit the allocation
        if commit_allocation:
            try:
                subprocess.run(
                    ["git", "add", str(lock_file)],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"Allocate ID: {item_id}"],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                )
                # Push to remote to claim the ID
                if sync_remote:
                    push_result = subprocess.run(
                        ["git", "push"],
                        cwd=self.repo_root,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if push_result.returncode != 0:
                        # Push failed - likely another agent allocated an ID
                        # Pull and retry
                        logger.warning(f"Push failed, will retry: {push_result.stderr}")
                        return self._retry_allocation(prefix)
            except subprocess.CalledProcessError as e:
                logger.warning(f"Git commit failed: {e}")

        return {
            "success": True,
            "id": item_id,
            "prefix": prefix,
            "number": next_num,
            "message": f"Allocated {item_id}",
        }

    def _retry_allocation(self, prefix: str, max_retries: int = 3) -> dict[str, Any]:
        """Retry allocation after a conflict."""
        for attempt in range(max_retries):
            try:
                # Pull latest
                subprocess.run(
                    ["git", "pull", "--rebase"],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                # Try allocation again
                return self.allocate_next_id(prefix, sync_remote=False, commit_allocation=True)
            except Exception as e:
                logger.warning(f"Retry {attempt + 1} failed: {e}")

        return {
            "success": False,
            "id": None,
            "prefix": prefix,
            "number": None,
            "message": "Failed to allocate ID after retries",
        }

    def _get_git_user(self) -> str:
        """Get current git user name."""
        try:
            result = subprocess.run(
                ["git", "config", "user.name"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    def move_item(
        self,
        item_id: str,
        new_status: WorkItemStatus,
        commit: bool = True,
        message: str | None = None,
        validate_workflow: bool = True,
        assignee: str | None = None,
        skip_wip_check: bool = False,
        closed_by: str | None = None,
        skip_gates: bool = False,
        gate_context: dict[str, Any] | None = None,
    ) -> WorkItem:
        """Move a work item to a new status.

        Args:
            item_id: The work item ID to move
            new_status: The target status
            commit: Whether to git commit the change
            message: Optional commit message
            validate_workflow: Whether to validate against workflow rules
            assignee: Optional assignee to set (e.g., 'Claude-M5', 'Claude-DGX')
            skip_wip_check: Whether to skip WIP limit validation
            closed_by: Optional URI recording what triggered this move
                (e.g., a PR URL). Written as kb:closedBy in the TTL block.
            skip_gates: Whether to skip transition gate checks
            gate_context: Extra context for gate evaluation (e.g., CLI flags
                like ``{"self_reviewed": True}``)
        """
        item = self.get_item(item_id)
        if not item:
            raise ValueError(f"Item not found: {item_id}")

        old_status = item.status

        # Validate transition using workflow if available
        if validate_workflow:
            valid, error_msg = self._validate_transition(item, new_status)
            if not valid:
                raise ValueError(error_msg)

        # Check WIP limits (unless skipped)
        if not skip_wip_check:
            # Determine which board this item belongs to
            board_name = None
            board_config = None
            if self.config.is_multi_board and item.file_path:
                board_config = self.config.get_board_for_path(
                    item.file_path, self.repo_root
                )
                if board_config:
                    board_name = board_config.name
            board = self.get_board(board_name=board_name)
            exempt_types = set(board_config.wip_exempt_types) if board_config else set()
            item_type_str = item.item_type.value
            # If the item being moved is itself exempt, skip WIP check entirely
            if item_type_str not in exempt_types:
                for col in board.columns:
                    if col.id == new_status.value:
                        if col.type_wip_limits is not None:
                            # Per-type WIP check
                            type_count = len(
                                board.get_items_by_status_and_type(
                                    new_status, item.item_type
                                )
                            )
                            limit = col.get_wip_limit(item_type_str)
                            if limit is not None and type_count >= limit:
                                raise ValueError(
                                    f"WIP limit reached for {item_type_str}s "
                                    f"in {col.name} on {board.name} "
                                    f"({type_count}/{limit})"
                                )
                        else:
                            # Aggregate WIP check — exclude exempt types from count
                            items_in_status = board.get_items_by_status(new_status)
                            if exempt_types:
                                items_in_status = [
                                    i for i in items_in_status
                                    if i.item_type.value not in exempt_types
                                ]
                            current_count = len(items_in_status)
                            if col.wip_limit and current_count >= col.wip_limit:
                                raise ValueError(
                                    f"WIP limit reached for {col.name} on "
                                    f"{board.name} "
                                    f"({current_count}/{col.wip_limit})"
                                )

        # Evaluate transition gates (unless skipped)
        gates_skipped = False
        if not skip_gates:
            gate_results = self._evaluate_gates(
                item, old_status, new_status, gate_context or {}
            )
            blocking = [
                r for r in gate_results
                if not r.passed and r.severity == "blocking"
            ]
            if blocking:
                msgs = "; ".join(r.message for r in blocking)
                raise ValueError(f"Gate check failed: {msgs}")
        elif self._has_gates_configured(item):
            # Only record gates_skipped when gates actually exist
            gates_skipped = True

        # Update item
        item.status = new_status
        item.updated = datetime.now()

        # Update assignee if provided
        if assignee:
            item.assignee = assignee

        # Update file with status history
        forced = not validate_workflow
        self._update_item_file_with_history(
            item, old_status, new_status, assignee,
            forced=forced, closed_by=closed_by,
            gates_skipped=gates_skipped,
        )

        # Git commit if requested
        if commit:
            commit_msg = message
            if not commit_msg:
                commit_msg = f"Move {item_id} to {new_status.value}"
                if forced:
                    commit_msg += " (forced)"
                if assignee:
                    commit_msg += f" (assigned to {assignee})"
            self._git_commit(item.file_path, commit_msg)

        # Fire hooks (after successful move)
        self._hook_engine.trigger(
            HookEvent.STATUS_CHANGE,
            HookContext(
                event=HookEvent.STATUS_CHANGE,
                item_id=item.id,
                item_type=item.item_type.value,
                title=item.title,
                old_status=old_status.value,
                new_status=new_status.value,
                assignee=item.assignee,
                forced=forced,
            ),
        )

        # Fire ASSIGNED hook when an assignee is set
        if assignee:
            self._hook_engine.trigger(
                HookEvent.ASSIGNED,
                HookContext(
                    event=HookEvent.ASSIGNED,
                    item_id=item.id,
                    item_type=item.item_type.value,
                    title=item.title,
                    new_status=new_status.value,
                    assignee=assignee,
                ),
            )

        # Fire BLOCKED hook when item moves to blocked status
        if new_status.value == "blocked":
            self._hook_engine.trigger(
                HookEvent.BLOCKED,
                HookContext(
                    event=HookEvent.BLOCKED,
                    item_id=item.id,
                    item_type=item.item_type.value,
                    title=item.title,
                    new_status="blocked",
                    assignee=item.assignee,
                ),
            )

        return item

    def _fire_create_hook(self, item: WorkItem) -> None:
        """Fire on_create hooks after successful item creation."""
        self._hook_engine.trigger(
            HookEvent.ITEM_CREATED,
            HookContext(
                event=HookEvent.ITEM_CREATED,
                item_id=item.id,
                item_type=item.item_type.value,
                title=item.title,
                new_status=item.status.value,
                assignee=item.assignee,
            ),
        )

    def _hook_create_item(
        self,
        item_type: str,
        title: str,
        priority: str = "medium",
        tags: list[str] | None = None,
    ) -> dict[str, str] | None:
        """Callback for the hook ``create_item`` action.

        Creates a work item **locally only** (no git commit or push).
        This is intentional: hooks must be lightweight and side-effect-safe.
        The calling service (e.g., Bosun) is responsible for pushing if
        the item needs to reach the remote repo.

        Returns a dict with ``item_id`` and ``file_path`` on success,
        or ``None`` on failure.
        """
        try:
            wit = WorkItemType.from_string(item_type)
            item = self.create_item(
                item_type=wit,
                title=title,
                priority=priority,
                tags=tags or [],
            )
            return {"item_id": item.id, "file_path": str(item.file_path)}
        except Exception as e:
            logger.warning(f"Hook create_item failed: {e}")
            return None

    def _validate_transition(self, item: WorkItem, new_status: WorkItemStatus) -> tuple[bool, str]:
        """Validate a status transition using workflow rules if available.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check board-specific transitions first (e.g., HDD theme)
        board_config = self._get_board_for_item(item)
        theme = self._load_board_theme(board_config)
        board_transitions = self._get_board_transitions(board_config, theme)

        if board_transitions:
            reverse_mapping = self._get_reverse_status_mapping(
                board_config, theme,
            )
            from_native = reverse_mapping.get(
                item.status.value, item.status.value,
            )
            to_native = reverse_mapping.get(
                new_status.value, new_status.value,
            )

            allowed = board_transitions.get(from_native, [])
            if to_native in allowed:
                return True, ""
            else:
                return False, f"Invalid transition from {from_native} to {to_native}"

        # Try to load workflow for this item type
        item_type = item.item_type.value
        workflow = self._workflow_parser.load_workflow(item_type)

        if workflow:
            # Use workflow validation
            return self._workflow_parser.validate_transition(item, new_status, workflow)
        else:
            # Fall back to default validation
            if self._is_valid_transition(item.status, new_status):
                return True, ""
            else:
                return False, f"Invalid transition from {item.status.value} to {new_status.value}"

    def _evaluate_gates(
        self,
        item: WorkItem,
        old_status: WorkItemStatus,
        new_status: WorkItemStatus,
        context: dict[str, Any],
    ) -> list[GateResult]:
        """Evaluate transition gates from board config.

        Returns list of GateResult objects (both passed and failed).
        Supports both v2 multi-board (per-board gates) and v1 single-board
        (top-level gates) configurations.
        """
        # v2: per-board gates
        board_config = self._get_board_for_item(item)
        if board_config and board_config.gates:
            gate_configs = board_config.gates
        elif self.config.gates:
            # v1: top-level gates on KanbanConfig
            gate_configs = self.config.gates
        else:
            return []

        from .gates import GateEvaluator

        evaluator = GateEvaluator(gate_configs)
        return evaluator.evaluate(item, old_status.value, new_status.value, context)

    def _has_gates_configured(self, item: WorkItem) -> bool:
        """Check if any gates are configured for this item's board."""
        board_config = self._get_board_for_item(item)
        if board_config and board_config.gates:
            return True
        return bool(self.config.gates)

    def _is_valid_transition(self, from_status: WorkItemStatus, to_status: WorkItemStatus) -> bool:
        """Check if a status transition is valid (default rules)."""
        # Define valid transitions
        valid_transitions = {
            WorkItemStatus.BACKLOG: [WorkItemStatus.READY, WorkItemStatus.BLOCKED],
            WorkItemStatus.READY: [
                WorkItemStatus.IN_PROGRESS,
                WorkItemStatus.BACKLOG,
                WorkItemStatus.BLOCKED,
            ],
            WorkItemStatus.IN_PROGRESS: [
                WorkItemStatus.REVIEW,
                WorkItemStatus.DONE,
                WorkItemStatus.BLOCKED,
                WorkItemStatus.READY,
            ],
            WorkItemStatus.REVIEW: [
                WorkItemStatus.DONE,
                WorkItemStatus.IN_PROGRESS,
                WorkItemStatus.BLOCKED,
            ],
            WorkItemStatus.BLOCKED: [
                WorkItemStatus.READY,
                WorkItemStatus.IN_PROGRESS,
                WorkItemStatus.BACKLOG,
            ],
            WorkItemStatus.DONE: [],  # Terminal state
        }

        return to_status in valid_transitions.get(from_status, [])

    def get_workflow(self, item_type: str) -> WorkflowConfig | None:
        """Get the workflow for a specific item type."""
        return self._workflow_parser.load_workflow(item_type)

    def get_allowed_transitions(self, item: WorkItem) -> list[str]:
        """Get list of allowed transitions for an item."""
        workflow = self._workflow_parser.load_workflow(item.item_type.value)
        if workflow:
            return workflow.get_allowed_transitions(item.status.value)
        else:
            # Default transitions
            default = self._get_default_transitions(item.status)
            return [s.value for s in default]

    def _get_default_transitions(self, status: WorkItemStatus) -> list[WorkItemStatus]:
        """Get default allowed transitions for a status."""
        transitions = {
            WorkItemStatus.BACKLOG: [WorkItemStatus.READY, WorkItemStatus.BLOCKED],
            WorkItemStatus.READY: [
                WorkItemStatus.IN_PROGRESS,
                WorkItemStatus.BACKLOG,
                WorkItemStatus.BLOCKED,
            ],
            WorkItemStatus.IN_PROGRESS: [
                WorkItemStatus.REVIEW,
                WorkItemStatus.DONE,
                WorkItemStatus.BLOCKED,
                WorkItemStatus.READY,
            ],
            WorkItemStatus.REVIEW: [
                WorkItemStatus.DONE,
                WorkItemStatus.IN_PROGRESS,
                WorkItemStatus.BLOCKED,
            ],
            WorkItemStatus.BLOCKED: [
                WorkItemStatus.READY,
                WorkItemStatus.IN_PROGRESS,
                WorkItemStatus.BACKLOG,
            ],
            WorkItemStatus.DONE: [],
        }
        return transitions.get(status, [])

    def _update_item_file(self, item: WorkItem) -> None:
        """Update the work item file with current state."""
        item.file_path.write_text(item.to_markdown())

    def _update_item_file_with_history(
        self,
        item: WorkItem,
        old_status: WorkItemStatus,
        new_status: WorkItemStatus,
        assignee: str | None = None,
        forced: bool = False,
        closed_by: str | None = None,
        gates_skipped: bool = False,
    ) -> None:
        """Update file and append status change to yurtle knowledge block.

        Status history is stored in TTL (Turtle RDF) format:
        ```yurtle
        @prefix kb: <https://yurtle.dev/kanban/> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

        <> kb:statusChange [
            kb:status kb:ready ;
            kb:at "2024-01-15T10:30:00"^^xsd:dateTime ;
            kb:by "Claude-M5" ;
        ] .
        ```

        When forced=True, an additional kb:forcedMove triple is recorded.
        When closed_by is set, a kb:closedBy triple records the triggering
        artifact (e.g., a PR URL), making closure provenance graph-queryable.
        When gates_skipped=True, a kb:gatesSkipped triple is recorded.
        """
        content = item.file_path.read_text()

        # Determine board-native status name (e.g., 'active' for HDD)
        board_config = self._get_board_for_item(item)
        theme = self._load_board_theme(board_config)
        reverse_mapping = self._get_reverse_status_mapping(
            board_config, theme,
        )
        native_status = reverse_mapping.get(
            new_status.value, new_status.value,
        )

        # Update frontmatter for status and assignee (use board-native name)
        content = self._update_frontmatter_field(content, "status", native_status)
        if assignee:
            content = self._update_frontmatter_field(content, "assignee", assignee)

        # Create TTL status change entry (use canonical name for RDF consistency)
        timestamp = datetime.now().isoformat(timespec="seconds")
        agent = assignee or self._get_git_user()
        ttl_entry = f'''    kb:status kb:{new_status.value} ;
    kb:at "{timestamp}"^^xsd:dateTime ;
    kb:by "{agent}" ;'''
        if forced:
            ttl_entry += '\n    kb:forcedMove "true"^^xsd:boolean ;'
        if gates_skipped:
            ttl_entry += '\n    kb:gatesSkipped "true"^^xsd:boolean ;'
        if closed_by:
            # Sanitize: reject characters that could break TTL string syntax
            if re.search(r'[\n\r\\" <>]', closed_by):
                raise ValueError(
                    f"Invalid value for closed_by: contains disallowed characters: {closed_by!r}"
                )
            ttl_entry += f'\n    kb:closedBy <{closed_by}> ;'

        # Check if yurtle block with status changes exists
        # Match block with prefix declarations and statusChange predicates
        yurtle_pattern = (
            r"```yurtle\n@prefix kb: <https://yurtle\.dev/"
            r"kanban/> \.\n@prefix xsd: <http://www\.w3\.org/"
            r"2001/XMLSchema#> \.\n\n<> kb:statusChange"
            r"(.*?)\.\n```"
        )
        match = re.search(yurtle_pattern, content, re.DOTALL)

        if match:
            # Append to existing block - add new blank node
            existing = match.group(1).rstrip()
            # Remove trailing period and add comma for new entry
            new_block = f"""```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<> kb:statusChange{existing.rstrip(" ;")},
  [
{ttl_entry}
  ] .
```"""
            content = content[: match.start()] + new_block + content[match.end() :]
        else:
            # Add new yurtle block at end
            new_block = f"""```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<> kb:statusChange [
{ttl_entry}
  ] .
```"""
            content = content.rstrip() + "\n\n" + new_block + "\n"

        item.file_path.write_text(content)

    def _update_frontmatter_field(self, content: str, field: str, value: str) -> str:
        """Update a single field in the frontmatter."""
        import re

        pattern = rf"^{field}:.*$"
        replacement = f"{field}: {value}"
        # Only replace in frontmatter (between first two ---)
        parts = content.split("---", 2)
        if len(parts) >= 3:
            parts[1] = re.sub(pattern, replacement, parts[1], flags=re.MULTILINE)
            return "---".join(parts)
        return content

    def _add_or_update_frontmatter_field(self, content: str, field: str, value: str) -> str:
        """Add or update a field in the frontmatter.

        If the field exists, update it. If not, insert it before the closing ---.
        """
        import re

        parts = content.split("---", 2)
        if len(parts) < 3:
            return content

        frontmatter = parts[1]
        pattern = rf"^{field}:.*$"
        if re.search(pattern, frontmatter, flags=re.MULTILINE):
            # Field exists — update it
            frontmatter = re.sub(pattern, f"{field}: {value}", frontmatter, flags=re.MULTILINE)
        else:
            # Field doesn't exist — append before end
            frontmatter = frontmatter.rstrip() + f"\n{field}: {value}\n"

        parts[1] = frontmatter
        return "---".join(parts)

    def _git_commit(self, file_path: Path, message: str) -> None:
        """Commit changes to git."""
        try:
            subprocess.run(
                ["git", "add", str(file_path)],
                cwd=self.repo_root,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.repo_root,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f"Git commit failed: {e}")

    def add_comment(
        self,
        item_id: str,
        content: str,
        author: str,
        commit: bool = True,
    ) -> WorkItem:
        """Add a comment to a work item."""
        item = self.get_item(item_id)
        if not item:
            raise ValueError(f"Item not found: {item_id}")

        comment = Comment(content=content, author=author)
        item.comments.append(comment)
        item.updated = datetime.now()

        # Update file (comments go in a special section)
        self._update_item_with_comment(item, comment)

        if commit:
            self._git_commit(
                item.file_path,
                f"Add comment to {item_id}",
            )

        return item

    def _update_item_with_comment(self, item: WorkItem, comment: Comment) -> None:
        """Update item file to include new comment."""
        content = item.file_path.read_text()

        # Add comment section if not exists
        if "## Comments" not in content:
            content += "\n\n## Comments\n"

        # Add comment
        timestamp = comment.created_at.strftime("%Y-%m-%d %H:%M")
        content += f"\n### {comment.author} ({timestamp})\n\n{comment.content}\n"

        item.file_path.write_text(content)

    def get_status_history(self, item_id: str) -> list[dict[str, Any]]:
        """Get status history for an item.

        Returns list of dicts: [{'status': str, 'at': datetime, 'by': str}, ...]

        Parses TTL (Turtle RDF) format in yurtle blocks:
        ```yurtle
        @prefix kb: <https://yurtle.dev/kanban/> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

        <> kb:statusChange [
            kb:status kb:ready ;
            kb:at "2024-01-15T10:30:00"^^xsd:dateTime ;
            kb:by "Claude-M5" ;
        ] .
        ```
        """
        item = self.get_item(item_id)
        if not item:
            return []

        content = item.file_path.read_text()

        # Parse yurtle block with TTL format
        import re

        # Find yurtle blocks
        yurtle_blocks = re.findall(r"```yurtle\n(.*?)```", content, re.DOTALL)
        if not yurtle_blocks:
            return []

        history = []

        for block in yurtle_blocks:
            # Find all blank nodes with statusChange data
            # Pattern matches: kb:status kb:XXX ; kb:at "..." ; kb:by "..." ;
            # Optional: kb:forcedMove "true"^^xsd:boolean ;
            entry_pattern = (
                r'kb:status kb:(\w+)\s*;\s*'
                r'kb:at "([^"]+)"(?:\^\^xsd:dateTime)?'
                r'\s*;\s*kb:by "([^"]+)"'
            )
            for entry_match in re.finditer(entry_pattern, block):
                try:
                    entry: dict[str, Any] = {
                        "status": entry_match.group(1),
                        "at": datetime.fromisoformat(entry_match.group(2)),
                        "by": entry_match.group(3),
                        "forced": False,
                    }
                    # Check for forcedMove triple in the surrounding blank node
                    # Look ahead from the match end for kb:forcedMove within the same node
                    rest = block[entry_match.end():]
                    # The forced triple appears before the next ']' (end of blank node)
                    node_end = rest.find("]")
                    if node_end != -1:
                        node_rest = rest[:node_end]
                        if 'kb:forcedMove "true"' in node_rest:
                            entry["forced"] = True
                    history.append(entry)
                except ValueError:
                    pass

        return history

    def get_flow_metrics(self, item_id: str) -> dict[str, Any]:
        """Calculate flow metrics for an item.

        Returns:
            cycle_time: Time from in_progress to done (working time)
            lead_time: Time from ready to done (total queue + work time)
            time_in_status: Dict of status -> hours spent
        """
        history = self.get_status_history(item_id)
        if not history:
            return {"error": "No status history found"}

        metrics: dict[str, Any] = {
            "item_id": item_id,
            "transitions": len(history),
            "time_in_status": {},
            "cycle_time_hours": None,
            "lead_time_hours": None,
        }

        # Calculate time in each status
        for i, entry in enumerate(history):
            status = entry["status"]
            start_time = entry["at"]

            # End time is next transition or now
            if i + 1 < len(history):
                end_time = history[i + 1]["at"]
            else:
                end_time = datetime.now()

            hours = (end_time - start_time).total_seconds() / 3600
            metrics["time_in_status"][status] = metrics["time_in_status"].get(status, 0) + hours

        # Calculate cycle time (in_progress to done)
        in_progress_time = None
        done_time = None
        for entry in history:
            if entry["status"] == "in_progress" and in_progress_time is None:
                in_progress_time = entry["at"]
            if entry["status"] == "done":
                done_time = entry["at"]

        if in_progress_time and done_time:
            metrics["cycle_time_hours"] = (done_time - in_progress_time).total_seconds() / 3600

        # Calculate lead time (ready to done)
        ready_time = None
        for entry in history:
            if entry["status"] == "ready" and ready_time is None:
                ready_time = entry["at"]

        if ready_time and done_time:
            metrics["lead_time_hours"] = (done_time - ready_time).total_seconds() / 3600

        return metrics

    def get_board_metrics(self) -> dict[str, Any]:
        """Calculate aggregate flow metrics for the board."""
        board = self.get_board()

        total_cycle_time = 0
        total_lead_time = 0
        cycle_time_count = 0
        lead_time_count = 0
        total_time_in_status: dict[str, float] = {}

        for item in board.items:
            metrics = self.get_flow_metrics(item.id)
            if "error" not in metrics:
                if metrics["cycle_time_hours"]:
                    total_cycle_time += metrics["cycle_time_hours"]
                    cycle_time_count += 1
                if metrics["lead_time_hours"]:
                    total_lead_time += metrics["lead_time_hours"]
                    lead_time_count += 1
                for status, hours in metrics.get("time_in_status", {}).items():
                    total_time_in_status[status] = total_time_in_status.get(status, 0) + hours

        return {
            "total_items": len(board.items),
            "items_with_history": cycle_time_count,
            "avg_cycle_time_hours": total_cycle_time / cycle_time_count
            if cycle_time_count
            else None,
            "avg_lead_time_hours": total_lead_time / lead_time_count if lead_time_count else None,
            "total_time_in_status": total_time_in_status,
        }

    def get_blocked_items(self) -> list[WorkItem]:
        """Get all blocked items."""
        return self.get_items(status=WorkItemStatus.BLOCKED)

    def get_my_items(self, assignee: str) -> list[WorkItem]:
        """Get items assigned to a specific person."""
        return self.get_items(assignee=assignee)

    def suggest_next_item(self, assignee: str | None = None) -> WorkItem | None:
        """Suggest the next highest-priority item to work on."""
        items = self.get_items(status=WorkItemStatus.READY)

        if assignee:
            # Prefer items assigned to this person
            my_items = [i for i in items if i.assignee == assignee]
            if my_items:
                items = my_items

        if not items:
            return None

        # Sort by priority
        items.sort(key=lambda i: -i.priority_score)
        return items[0]

    def update_item(
        self,
        item_id: str,
        title: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        commit: bool = True,
        message: str | None = None,
    ) -> WorkItem:
        """Update a work item's properties (not status - use move_item for that).

        Args:
            item_id: The work item ID to update
            title: New title (optional)
            priority: New priority level (optional)
            assignee: New assignee (optional)
            description: New description (optional)
            tags: New tags list (optional, replaces existing)
            commit: Whether to git commit the change
            message: Optional commit message
        """
        item = self.get_item(item_id)
        if not item:
            raise ValueError(f"Item not found: {item_id}")

        # Track what changed for commit message
        changes = []

        if title is not None and title != item.title:
            item.title = title
            changes.append("title")

        if priority is not None and priority != item.priority:
            item.priority = priority
            changes.append("priority")

        if assignee is not None and assignee != item.assignee:
            item.assignee = assignee
            changes.append("assignee")

        if description is not None and description != item.description:
            item.description = description
            changes.append("description")

        if tags is not None and tags != item.tags:
            item.tags = tags
            changes.append("tags")

        if not changes:
            return item  # Nothing to update

        item.updated = datetime.now()

        # Update file
        self._update_item_file(item)

        # Git commit if requested
        if commit:
            change_summary = ", ".join(changes)
            self._git_commit(
                item.file_path,
                message or f"Update {item_id}: {change_summary}",
            )

        return item

    def rank_item(
        self,
        item_id: str,
        rank: int,
        value_summary: str | None = None,
        commit: bool = True,
        message: str | None = None,
    ) -> WorkItem:
        """Set the priority rank for a work item.

        Args:
            item_id: The work item ID to rank
            rank: Priority rank (lower = higher priority, 1 = top)
            value_summary: Optional brief value statement
            commit: Whether to git commit the change
            message: Optional commit message
        """
        if rank < 1:
            raise ValueError(f"Rank must be >= 1, got {rank}")

        item = self.get_item(item_id)
        if not item:
            raise ValueError(f"Item not found: {item_id}")

        item.priority_rank = rank
        if value_summary is not None:
            item.value_summary = value_summary

        # Update file using field-level updates (preserves existing content)
        content = item.file_path.read_text()
        content = self._add_or_update_frontmatter_field(content, "priority_rank", str(rank))
        if value_summary is not None:
            escaped = value_summary.replace('"', '\\"')
            content = self._add_or_update_frontmatter_field(
                content, "value_summary", f'"{escaped}"'
            )
        item.file_path.write_text(content)

        if commit:
            self._git_commit(
                item.file_path,
                message or f"Rank {item_id} as #{rank}",
            )

        return item

    def get_ranked_items(self, status: WorkItemStatus | None = None) -> list[WorkItem]:
        """Get items sorted by priority_rank (ranked items first, then by priority_score).

        Items with priority_rank are sorted ascending (1 = highest).
        Items without priority_rank follow, sorted by priority_score descending.
        """
        items = self.get_items(status=status)
        items = [i for i in items if i.status != WorkItemStatus.DONE]

        ranked = [i for i in items if i.priority_rank is not None]
        unranked = [i for i in items if i.priority_rank is None]

        ranked.sort(key=lambda i: i.priority_rank)
        # unranked already sorted by priority_score from get_items()

        return ranked + unranked

    # ------------------------------------------------------------------
    # Experiment run tracking (Phase 3)
    # ------------------------------------------------------------------

    def create_experiment_run(
        self,
        expr_id: str,
        being: str,
        params: dict[str, str] | None = None,
        run_by: str | None = None,
    ) -> Path:
        """Create a timestamped experiment run folder with config.yaml.

        Args:
            expr_id: Experiment ID (e.g., EXPR-130)
            being: Being name/version (e.g., santiago-toddler-v12.4)
            params: Optional key=value parameters
            run_by: Who started the run (default: git user.name)

        Returns:
            Path to the created run folder.
        """
        # Validate expr_id to prevent path traversal
        # Allow dotted sub-IDs like EXPR-131.5 (common for sub-experiments)
        if not re.match(r"^[A-Za-z]+-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*$", expr_id):
            raise ValueError(
                f"Invalid experiment ID format: {expr_id!r} — "
                "expected PREFIX-ID (e.g., EXPR-130 or EXPR-131.5)"
            )

        # Resolve run_by from git config if not provided
        if run_by is None:
            try:
                result = subprocess.run(
                    ["git", "config", "user.name"],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                run_by = result.stdout.strip() or "unknown"
            except subprocess.CalledProcessError:
                run_by = "unknown"

        # Look up the experiment to get hypothesis link
        item = self.get_item(expr_id)
        hypothesis = ""
        if item and item.file_path and item.file_path.exists():
            content = item.file_path.read_text()
            fm = self._parse_frontmatter(content)
            hypothesis = fm.get("hypothesis", "")

        # Create timestamped folder (microseconds to avoid collisions)
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H%M%S") + f"-{now.microsecond:06d}"
        runs_dir = self.repo_root / "research" / "runs" / expr_id / timestamp
        runs_dir.mkdir(parents=True, exist_ok=True)

        # Write config.yaml
        config_data: dict[str, Any] = {
            "experiment": expr_id,
            "hypothesis": hypothesis,
            "being": being,
            "created": now.isoformat(timespec="seconds"),
            "run_by": run_by,
            "status": "running",
        }
        if params:
            config_data["params"] = params

        config_path = runs_dir / "config.yaml"
        config_path.write_text(yaml.dump(config_data, default_flow_style=False, sort_keys=False))

        return runs_dir

    def get_experiment_runs(self, expr_id: str) -> list[dict[str, Any]]:
        """Get all runs for an experiment, sorted by date descending.

        Args:
            expr_id: Experiment ID (e.g., EXPR-130)

        Returns:
            List of run metadata dicts with keys: timestamp, being, status,
            outcome, run_by, params, run_path.
        """
        # Validate expr_id to prevent path traversal
        # Allow dotted sub-IDs like EXPR-131.5 (common for sub-experiments)
        if not re.match(r"^[A-Za-z]+-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*$", expr_id):
            raise ValueError(
                f"Invalid experiment ID format: {expr_id!r} — "
                "expected PREFIX-ID (e.g., EXPR-130 or EXPR-131.5)"
            )
        runs_base = self.repo_root / "research" / "runs" / expr_id
        if not runs_base.exists():
            return []

        runs = []
        for run_dir in sorted(runs_base.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            config_path = run_dir / "config.yaml"
            if not config_path.exists():
                continue

            try:
                config_data = yaml.safe_load(config_path.read_text()) or {}
            except yaml.YAMLError:
                continue

            run_info: dict[str, Any] = {
                "timestamp": config_data.get("created", run_dir.name),
                "being": config_data.get("being", ""),
                "status": config_data.get("status", "unknown"),
                "run_by": config_data.get("run_by", ""),
                "params": config_data.get("params", {}),
                "run_path": run_dir,
            }

            # Check for metrics.json
            metrics_path = run_dir / "metrics.json"
            if metrics_path.exists():
                import json

                try:
                    metrics = json.loads(metrics_path.read_text())
                    run_info["outcome"] = metrics.get("outcome", "")
                    run_info["summary"] = metrics.get("summary", "")
                except (json.JSONDecodeError, OSError):
                    pass

            runs.append(run_info)

        return runs

    def update_run_status(
        self,
        run_path: Path,
        status: str,
        outcome: str | None = None,
    ) -> None:
        """Update the status (and optionally outcome) of an experiment run.

        Args:
            run_path: Path to the run folder
            status: New status (e.g., "running", "complete", "failed")
            outcome: Optional outcome string (e.g., "VALIDATED", "REFUTED")
        """
        config_path = run_path / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"No config.yaml in {run_path}")

        config_data = yaml.safe_load(config_path.read_text()) or {}
        config_data["status"] = status
        if outcome is not None:
            config_data["outcome"] = outcome

        config_path.write_text(
            yaml.dump(config_data, default_flow_style=False, sort_keys=False)
        )

    # ------------------------------------------------------------------
    # HDD Registry & Validation (Phase 4)
    # ------------------------------------------------------------------

    def _get_hdd_frontmatter(self, item: WorkItem) -> dict[str, Any]:
        """Read raw frontmatter for an HDD item (includes paper, hypothesis, etc.)."""
        if not item.file_path or not item.file_path.exists():
            return {}
        content = item.file_path.read_text()
        return self._parse_frontmatter(content) or {}

    def get_hdd_cross_references(self) -> dict[str, Any]:
        """Build a full cross-reference map of all HDD items.

        Returns a dict with keys: papers, hypotheses, experiments, measures,
        ideas, literature, orphaned. Each value is a list of dicts.
        """
        hdd_types = {
            WorkItemType.PAPER, WorkItemType.HYPOTHESIS, WorkItemType.EXPERIMENT,
            WorkItemType.MEASURE, WorkItemType.IDEA, WorkItemType.LITERATURE,
        }
        all_items = self.get_items()
        hdd_items = [i for i in all_items if i.item_type in hdd_types]

        # Group by type
        by_type: dict[str, list[WorkItem]] = {
            "paper": [], "hypothesis": [], "experiment": [],
            "measure": [], "idea": [], "literature": [],
        }
        for item in hdd_items:
            type_key = item.item_type.value
            if type_key in by_type:
                by_type[type_key].append(item)

        # Build cross-reference maps
        # Paper → hypotheses
        paper_hyps: dict[str, list[str]] = {}
        # Hypothesis → experiments
        hyp_exps: dict[str, list[str]] = {}
        # Hypothesis → paper
        hyp_paper: dict[str, str] = {}
        # Experiment → hypothesis
        exp_hyp: dict[str, str] = {}
        # Idea → literature
        idea_lits: dict[str, list[str]] = {}
        # Literature → idea
        lit_idea: dict[str, str] = {}

        for item in by_type["hypothesis"]:
            fm = self._get_hdd_frontmatter(item)
            paper_ref = fm.get("paper", "")
            if paper_ref:
                paper_id = str(paper_ref)
                if not paper_id.startswith("PAPER-"):
                    paper_id = f"PAPER-{paper_id}"
                hyp_paper[item.id] = paper_id
                paper_hyps.setdefault(paper_id, []).append(item.id)

        for item in by_type["experiment"]:
            fm = self._get_hdd_frontmatter(item)
            hyp_ref = fm.get("hypothesis", "")
            if hyp_ref:
                exp_hyp[item.id] = str(hyp_ref)
                hyp_exps.setdefault(str(hyp_ref), []).append(item.id)

        for item in by_type["literature"]:
            fm = self._get_hdd_frontmatter(item)
            idea_ref = fm.get("source_idea", "") or fm.get("idea", "")
            if idea_ref:
                lit_idea[item.id] = str(idea_ref)
                idea_lits.setdefault(str(idea_ref), []).append(item.id)

        # Build result
        result: dict[str, Any] = {
            "papers": [],
            "hypotheses": [],
            "experiments": [],
            "measures": [],
            "ideas": [],
            "literature": [],
            "orphaned": [],
        }

        for p in by_type["paper"]:
            result["papers"].append({
                "id": p.id, "title": p.title, "status": p.status.value,
                "hypotheses": paper_hyps.get(p.id, []),
            })

        for h in by_type["hypothesis"]:
            fm = self._get_hdd_frontmatter(h)
            result["hypotheses"].append({
                "id": h.id, "title": h.title, "status": h.status.value,
                "paper": hyp_paper.get(h.id, ""),
                "target": fm.get("target", ""),
                "experiments": hyp_exps.get(h.id, []),
            })

        for e in by_type["experiment"]:
            runs = self.get_experiment_runs(e.id)
            last_outcome = ""
            if runs:
                last_outcome = runs[0].get("outcome", runs[0].get("summary", ""))
            result["experiments"].append({
                "id": e.id, "title": e.title, "status": e.status.value,
                "hypothesis": exp_hyp.get(e.id, ""),
                "runs": len(runs),
                "last_outcome": last_outcome,
            })

        for m in by_type["measure"]:
            fm = self._get_hdd_frontmatter(m)
            result["measures"].append({
                "id": m.id, "title": m.title,
                "unit": fm.get("unit", ""),
                "category": fm.get("category", ""),
            })

        for idea_item in by_type["idea"]:
            result["ideas"].append({
                "id": idea_item.id, "title": idea_item.title,
                "status": idea_item.status.value,
                "literature": idea_lits.get(idea_item.id, []),
            })

        for lit in by_type["literature"]:
            result["literature"].append({
                "id": lit.id, "title": lit.title, "status": lit.status.value,
                "idea": lit_idea.get(lit.id, ""),
            })

        # Orphaned items. A paper is OPTIONAL (issue #77), so "has no paper" is
        # not by itself orphanhood — see _is_deliberately_unparented. An
        # experiment need not test a filed hypothesis either; a DANGLING
        # reference is still caught, as an error, further down.
        for h in by_type["hypothesis"]:
            paper = hyp_paper.get(h.id, "")
            if not paper and not self._is_deliberately_unparented(h.id):
                result["orphaned"].append({
                    "id": h.id, "reason": "No paper assignment",
                })

        return result

    def validate_hdd_links(self) -> dict[str, Any]:
        """Validate bidirectional links between HDD items.

        Returns a validation report dict with keys: errors, warnings, summary.
        """
        xrefs = self.get_hdd_cross_references()

        # Collect all known IDs
        all_ids: set[str] = set()
        for section in ("papers", "hypotheses", "experiments", "measures", "ideas", "literature"):
            for item in xrefs[section]:
                all_ids.add(item["id"])

        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        # Check hypotheses → paper links
        for h in xrefs["hypotheses"]:
            paper = h.get("paper", "")
            if not paper:
                if not self._is_deliberately_unparented(h["id"]):
                    warnings.append({
                        "id": h["id"],
                        "issue": "hypothesis missing paper link",
                    })
            elif paper not in all_ids:
                errors.append({
                    "id": h["id"],
                    "issue": f"references {paper} (not found)",
                })

        # Check experiments → hypothesis links. An experiment that names NO
        # hypothesis is a legitimate standing state (issue #77) — a dangling
        # reference to one that does not exist is still an error below.
        for e in xrefs["experiments"]:
            hyp = e.get("hypothesis", "")
            if hyp and hyp not in all_ids:
                errors.append({
                    "id": e["id"],
                    "issue": f"references {hyp} (not found)",
                })

        # Check literature → idea links (warning, not error)
        for lit in xrefs["literature"]:
            idea = lit.get("idea", "")
            if idea and idea not in all_ids:
                warnings.append({
                    "id": lit["id"],
                    "issue": f"references {idea} (not found)",
                })

        # Check for unused measures
        used_measures: set[str] = set()
        for h in xrefs["hypotheses"]:
            item = self.get_item(h["id"])
            if item and item.graph:
                for triple_obj in item.get_knowledge_triples(_HYP.measuredBy):
                    # Extract measure ID from URI
                    obj_str = str(triple_obj)
                    if "/" in obj_str:
                        used_measures.add(obj_str.split("/")[-1])
                    else:
                        used_measures.add(obj_str)

        for m in xrefs["measures"]:
            if m["id"] not in used_measures:
                warnings.append({
                    "id": m["id"],
                    "issue": "measure not referenced by any hypothesis",
                })

        summary = {
            "papers": len(xrefs["papers"]),
            "hypotheses": len(xrefs["hypotheses"]),
            "experiments": len(xrefs["experiments"]),
            "measures": len(xrefs["measures"]),
            "ideas": len(xrefs["ideas"]),
            "literature": len(xrefs["literature"]),
            "errors": len(errors),
            "warnings": len(warnings),
            "orphaned": len(xrefs["orphaned"]),
        }

        return {
            "errors": errors,
            "warnings": warnings,
            "orphaned": xrefs["orphaned"],
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # HDD Cross-Board Dependency Engine (Phase 5)
    # ------------------------------------------------------------------

    def _resolve_implements(
        self, fm: dict[str, Any],
    ) -> tuple[list[str], dict[str, str], bool]:
        """Parse implements field and resolve expedition statuses.

        Returns (implements_list, implements_status, all_dev_done).
        """
        implements_raw = fm.get("implements", [])
        if isinstance(implements_raw, str):
            implements_list = [implements_raw]
        elif isinstance(implements_raw, list):
            implements_list = [str(x) for x in implements_raw]
        else:
            implements_list = []

        implements_status: dict[str, str] = {}
        all_dev_done = True
        for exp_id in implements_list:
            dev_item = self.get_item(exp_id)
            if dev_item:
                implements_status[exp_id] = dev_item.status.value
                if dev_item.status.value != "done":
                    all_dev_done = False
            else:
                implements_status[exp_id] = "not_found"
                all_dev_done = False

        return implements_list, implements_status, all_dev_done

    def _compute_readiness(
        self,
        implements_list: list[str],
        all_dev_done: bool,
        runs: list[dict[str, Any]],
    ) -> tuple[str, bool, bool, bool]:
        """Determine experiment readiness from dev status and training runs.

        Returns (readiness, has_completed_run, has_running_run, has_metrics).
        """
        has_completed_run = any(
            r.get("status") == "complete" for r in runs
        )
        has_running_run = any(
            r.get("status") == "running" for r in runs
        )
        has_metrics = any(
            r.get("outcome") or r.get("summary") for r in runs
        )

        if not implements_list:
            readiness = "no_dev_dependency"
        elif not all_dev_done:
            readiness = "blocked_by_dev"
        elif has_completed_run and has_metrics:
            readiness = "needs_analysis"
        elif has_completed_run:
            readiness = "training_complete"
        elif has_running_run:
            readiness = "training_in_progress"
        else:
            readiness = "ready_for_training"

        return readiness, has_completed_run, has_running_run, has_metrics

    def build_cross_board_graph(self) -> dict[str, Any]:
        """Build a cross-board dependency graph spanning research and dev boards.

        Traverses: Paper → Hypothesis → Experiment → Expedition (dev board)
                                                    → Training Runs

        Returns a dict with:
          - experiments: list of experiment nodes with full dependency chains
          - dev_blockers: expedition IDs that block research experiments
          - ready_for_training: experiments where all dev work is done
          - training_in_progress: experiments with running training
          - needs_analysis: experiments with completed training
        """
        xrefs = self.get_hdd_cross_references()

        # Build paper → hypothesis lookup (paper_id → [hyp_ids])
        paper_hyps: dict[str, list[str]] = {}
        hyp_paper: dict[str, str] = {}
        for h in xrefs["hypotheses"]:
            paper = h.get("paper", "")
            if paper:
                paper_hyps.setdefault(paper, []).append(h["id"])
                hyp_paper[h["id"]] = paper

        # Build hypothesis → experiment lookup (hyp_id → [expr_ids])
        hyp_exps: dict[str, list[str]] = {}
        for e in xrefs["experiments"]:
            hyp = e.get("hypothesis", "")
            if hyp:
                hyp_exps.setdefault(hyp, []).append(e["id"])

        # For each experiment, resolve implements → dev board expedition statuses
        experiment_nodes: list[dict[str, Any]] = []
        dev_blockers: list[str] = []
        ready_for_training: list[str] = []
        training_in_progress: list[str] = []
        needs_analysis: list[str] = []

        for exp_info in xrefs["experiments"]:
            expr_id = exp_info["id"]
            item = self.get_item(expr_id)
            if not item:
                continue

            fm = self._get_hdd_frontmatter(item)
            impl_list, impl_status, all_done = self._resolve_implements(fm)

            runs = self.get_experiment_runs(expr_id)
            readiness, _, _, _ = self._compute_readiness(
                impl_list, all_done, runs,
            )

            # Calculate downstream impact
            hyp_id = exp_info.get("hypothesis", "")
            paper_id = hyp_paper.get(hyp_id, "")
            blocking_chain: list[str] = []
            if hyp_id:
                blocking_chain.append(hyp_id)
            if paper_id:
                blocking_chain.append(paper_id)

            # Count other experiments sharing the same hypothesis (exclude self)
            sibling_experiments = hyp_exps.get(hyp_id, [])
            other_experiments = max(0, len(sibling_experiments) - 1)
            # Count other hypotheses sharing the same paper (exclude self)
            sibling_hypotheses = paper_hyps.get(paper_id, [])
            other_hypotheses = max(0, len(sibling_hypotheses) - 1)

            downstream_impact = other_experiments + other_hypotheses

            node: dict[str, Any] = {
                "experiment_id": expr_id,
                "title": exp_info.get("title", ""),
                "status": exp_info.get("status", ""),
                "hypothesis_id": hyp_id,
                "paper_id": paper_id,
                "implements": impl_list,
                "implements_status": impl_status,
                "readiness": readiness,
                "runs": len(runs),
                "last_run_status": runs[0].get("status", "") if runs else "",
                "last_outcome": runs[0].get("outcome", "") if runs else "",
                "downstream_impact": downstream_impact,
                "blocking_chain": blocking_chain,
                "compute_requirement": fm.get("compute_requirement", ""),
                "assignee": item.assignee or "",
            }
            experiment_nodes.append(node)

            # Categorize
            if readiness == "blocked_by_dev":
                for eid, status in impl_status.items():
                    if status != "done":
                        dev_blockers.append(eid)
            elif readiness == "ready_for_training":
                ready_for_training.append(expr_id)
            elif readiness == "training_in_progress":
                training_in_progress.append(expr_id)
            elif readiness == "needs_analysis":
                needs_analysis.append(expr_id)

        # Sort experiments by downstream impact (high first), then readiness priority
        readiness_order = {
            "ready_for_training": 0,
            "training_in_progress": 1,
            "blocked_by_dev": 2,
            "needs_analysis": 3,
            "training_complete": 4,
            "no_dev_dependency": 5,
        }
        experiment_nodes.sort(
            key=lambda n: (
                readiness_order.get(n["readiness"], 9),
                -n["downstream_impact"],
            )
        )

        return {
            "experiments": experiment_nodes,
            "dev_blockers": sorted(set(dev_blockers)),
            "ready_for_training": ready_for_training,
            "training_in_progress": training_in_progress,
            "needs_analysis": needs_analysis,
            "summary": {
                "total_experiments": len(experiment_nodes),
                "ready_for_training": len(ready_for_training),
                "blocked_by_dev": sum(
                    1 for n in experiment_nodes
                    if n["readiness"] == "blocked_by_dev"
                ),
                "training_in_progress": len(training_in_progress),
                "needs_analysis": len(needs_analysis),
                "dev_blockers": len(set(dev_blockers)),
            },
        }

    def get_experiment_readiness(self, expr_id: str) -> dict[str, Any]:
        """Check a single experiment's readiness for training.

        Returns a dict with: readiness, implements, implements_status,
        runs, blocking_chain, compute_requirement.
        """
        item = self.get_item(expr_id)
        if not item:
            return {"error": f"{expr_id} not found", "readiness": "unknown"}

        fm = self._get_hdd_frontmatter(item)
        impl_list, impl_status, all_done = self._resolve_implements(fm)

        runs = self.get_experiment_runs(expr_id)
        readiness, has_completed_run, _, has_metrics = (
            self._compute_readiness(impl_list, all_done, runs)
        )

        # Build blocking chain from frontmatter
        hyp_id = fm.get("hypothesis", "")
        paper_id = fm.get("paper", "")
        blocking_chain: list[str] = []
        if hyp_id:
            blocking_chain.append(str(hyp_id))
        if paper_id:
            blocking_chain.append(str(paper_id))

        return {
            "experiment_id": expr_id,
            "title": item.title,
            "readiness": readiness,
            "implements": impl_list,
            "implements_status": impl_status,
            "runs": len(runs),
            "has_completed_run": has_completed_run,
            "has_metrics": has_metrics,
            "blocking_chain": blocking_chain,
            "compute_requirement": fm.get("compute_requirement", ""),
            "assignee": item.assignee or "",
        }

    def get_critical_path(
        self,
        agent: str | None = None,
        ready_only: bool = False,
        dev_blockers_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get the prioritized critical path for research experiments.

        Traverses Paper → Hypothesis → Experiment → Expedition (dev board)
        to determine what's ready for training, what's blocked, and what
        has the highest downstream impact.

        Args:
            agent: Filter to experiments relevant to this agent/assignee.
            ready_only: Only return experiments ready for training.
            dev_blockers_only: Only return dev board items blocking research.

        Returns:
            List of critical path items sorted by readiness then downstream impact.
        """
        graph = self.build_cross_board_graph()
        experiments = graph["experiments"]

        # Filter by agent if specified
        if agent:
            agent_lower = agent.lower()
            experiments = [
                e for e in experiments
                if e.get("assignee", "").lower() == agent_lower
            ]

        # Filter by readiness
        if ready_only:
            experiments = [
                e for e in experiments
                if e["readiness"] == "ready_for_training"
            ]

        if dev_blockers_only:
            # Return the dev board items that block research
            blocker_ids = graph["dev_blockers"]
            blockers: list[dict[str, Any]] = []
            for bid in blocker_ids:
                dev_item = self.get_item(bid)
                if dev_item:
                    # Count how many experiments this blocker unblocks
                    unblocks = [
                        e["experiment_id"] for e in graph["experiments"]
                        if bid in e.get("implements", [])
                        and e["readiness"] == "blocked_by_dev"
                    ]
                    blockers.append({
                        "expedition_id": bid,
                        "title": dev_item.title,
                        "status": dev_item.status.value,
                        "assignee": dev_item.assignee or "",
                        "unblocks_experiments": unblocks,
                        "impact": len(unblocks),
                    })
            blockers.sort(key=lambda b: -b["impact"])
            return blockers

        return experiments
