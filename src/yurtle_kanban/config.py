"""
Configuration management for yurtle-kanban.

Supports both single-board (v1) and multi-board (v2) configurations.
Multi-board is opt-in: detected when config has 'version: 2.0' and 'boards' key.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Cache for loaded themes
_theme_cache: dict[str, dict[str, Any]] = {}

# Config version constants
CONFIG_VERSION_SINGLE = "1.0"
CONFIG_VERSION_MULTI = "2.0"


def _load_builtin_theme(theme_name: str, repo_root: Path | None = None) -> dict[str, Any] | None:
    """Load a theme from local .kanban/themes/ or package resources."""
    if theme_name in _theme_cache:
        return _theme_cache[theme_name]

    # Priority 1: Local .kanban/themes/ folder
    search_paths = []
    if repo_root:
        search_paths.append(repo_root / ".kanban" / "themes" / f"{theme_name}.yaml")
    search_paths.append(Path.cwd() / ".kanban" / "themes" / f"{theme_name}.yaml")

    # Priority 2: sys.prefix share directory (pip installed via Hatchling)
    import sys

    share_path = Path(sys.prefix) / "share" / "yurtle-kanban" / "themes" / f"{theme_name}.yaml"
    search_paths.append(share_path)

    # Priority 3: Source directory (development)
    try:
        import yurtle_kanban

        package_dir = Path(yurtle_kanban.__file__).parent.parent.parent
        search_paths.append(package_dir / "themes" / f"{theme_name}.yaml")
    except Exception:
        pass
    search_paths.append(Path(__file__).parent.parent.parent / "themes" / f"{theme_name}.yaml")

    # Try each path
    for theme_path in search_paths:
        try:
            if theme_path.exists():
                with open(theme_path) as f:
                    theme = yaml.safe_load(f)
                    _theme_cache[theme_name] = theme
                    return theme
        except Exception:
            continue

    return None


@dataclass
class PathConfig:
    """Configuration for work item paths."""

    root: str | None = "work/"
    scan_paths: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=lambda: ["**/archive/**", "**/templates/**"])

    # Type-specific paths (optional)
    features: str | None = None
    bugs: str | None = None
    epics: str | None = None
    tasks: str | None = None


@dataclass
class BoardConfig:
    """Configuration for a single board in multi-board setup.

    wip_limits supports three formats:
    - None: no WIP limits on this board
    - {"column": int}: legacy aggregate limit for all types in column
    - {"column": {"type": int|None, "_default": int}}: per-type limits
      (None = unlimited for that type)
    """

    name: str
    preset: str = "software"
    path: str = "work/"
    scan_paths: list[str] = field(default_factory=list)
    wip_limits: dict[str, int | dict[str, int | None] | None] | None = field(
        default_factory=dict
    )
    wip_exempt_types: list[str] = field(default_factory=list)
    gates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=lambda: ["**/archive/**", "**/templates/**"])

    def get_path(self) -> Path:
        """Get the board's work path."""
        return Path(self.path)

    def get_theme(self, repo_root: Path | None = None) -> dict[str, Any] | None:
        """Get the theme/preset configuration."""
        return _load_builtin_theme(self.preset, repo_root)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BoardConfig":
        """Create BoardConfig from dictionary.

        wip_limits can be:
        - missing/empty dict: no overrides (use theme defaults)
        - null: explicitly disable all WIP limits on this board
        - dict with int values: legacy aggregate limits
        - dict with dict values: per-type limits
        """
        raw_wip = data.get("wip_limits", {})
        # Preserve None (explicitly unlimited board)
        wip_limits = raw_wip if raw_wip is not None else None
        return cls(
            name=data.get("name", "default"),
            preset=data.get("preset", "software"),
            path=data.get("path", "work/"),
            scan_paths=data.get("scan_paths", []),
            wip_limits=wip_limits,
            wip_exempt_types=data.get("wip_exempt_types", []),
            gates=data.get("gates", {}),
            ignore=data.get("ignore", ["**/archive/**", "**/templates/**"]),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "name": self.name,
            "preset": self.preset,
            "path": self.path,
        }
        if self.scan_paths:
            result["scan_paths"] = self.scan_paths
        # Serialize wip_limits: None means "explicitly unlimited" (must be preserved),
        # empty dict {} means "no overrides" (omit for cleaner output)
        if self.wip_limits is None or self.wip_limits:
            result["wip_limits"] = self.wip_limits
        if self.wip_exempt_types:
            result["wip_exempt_types"] = self.wip_exempt_types
        if self.gates:
            result["gates"] = self.gates
        if self.ignore != ["**/archive/**", "**/templates/**"]:
            result["ignore"] = self.ignore
        return result


@dataclass
class KanbanConfig:
    """Main configuration for yurtle-kanban.

    Supports both single-board (v1) and multi-board (v2) configurations.
    Multi-board mode is detected when config has 'version: 2.0' and 'boards' key.
    """

    # Single-board config (v1, backward compatible)
    theme: str = "software"
    paths: PathConfig = field(default_factory=PathConfig)
    workflows: dict[str, str] = field(default_factory=dict)
    gates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    # Multi-board config (v2)
    version: str = CONFIG_VERSION_SINGLE
    boards: list[BoardConfig] = field(default_factory=list)
    namespace: str | None = None  # RDF namespace for graph-queryable items
    default_board: str | None = None  # Name of default board

    @property
    def is_multi_board(self) -> bool:
        """Check if this is a multi-board configuration."""
        return self.version == CONFIG_VERSION_MULTI and len(self.boards) > 0

    def get_board(self, name: str) -> BoardConfig | None:
        """Get a board by name."""
        for board in self.boards:
            if board.name == name:
                return board
        return None

    def get_board_for_path(self, path: Path, repo_root: Path | None = None) -> BoardConfig | None:
        """Get the board that matches a given path.

        Matches are based on the path being inside the board's configured path.
        """
        if not self.is_multi_board:
            return None

        # Resolve absolute path for comparison
        if repo_root:
            abs_path = (repo_root / path).resolve() if not path.is_absolute() else path.resolve()
        else:
            abs_path = path.resolve()

        for board in self.boards:
            if repo_root:
                board_path = (repo_root / board.path).resolve()
            else:
                board_path = Path(board.path).resolve()
            try:
                abs_path.relative_to(board_path)
                return board
            except ValueError:
                continue

        return None

    def get_default_board(self) -> BoardConfig | None:
        """Get the default board."""
        if not self.is_multi_board:
            return None

        if self.default_board:
            return self.get_board(self.default_board)

        # Fall back to first board
        return self.boards[0] if self.boards else None

    @classmethod
    def load(cls, config_path: Path) -> "KanbanConfig":
        """Load configuration from a YAML file."""
        if not config_path.exists():
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        # Check for v2 multi-board config
        version = data.get("version", CONFIG_VERSION_SINGLE)
        if version == CONFIG_VERSION_MULTI and "boards" in data:
            return cls._load_v2(data)

        # Fall back to v1 single-board config
        return cls._load_v1(data)

    @classmethod
    def _load_v1(cls, data: dict[str, Any]) -> "KanbanConfig":
        """Load v1 single-board configuration."""
        kanban_data = data.get("kanban", data)

        paths_data = kanban_data.get("paths", {})
        paths = PathConfig(
            root=paths_data.get("root", "work/"),
            scan_paths=paths_data.get("scan_paths", []),
            ignore=paths_data.get("ignore", ["**/archive/**", "**/templates/**"]),
            features=paths_data.get("features"),
            bugs=paths_data.get("bugs"),
            epics=paths_data.get("epics"),
            tasks=paths_data.get("tasks"),
        )

        return cls(
            version=CONFIG_VERSION_SINGLE,
            theme=kanban_data.get("theme", "software"),
            paths=paths,
            workflows=kanban_data.get("workflows", {}),
            gates=kanban_data.get("gates", {}),
        )

    @classmethod
    def _load_v2(cls, data: dict[str, Any]) -> "KanbanConfig":
        """Load v2 multi-board configuration."""
        boards = [BoardConfig.from_dict(b) for b in data.get("boards", [])]

        # Aggregate scan_paths from all boards for Priority 3 fallback
        all_scan_paths: list[str] = []
        for board in boards:
            all_scan_paths.extend(board.scan_paths)

        return cls(
            version=CONFIG_VERSION_MULTI,
            boards=boards,
            namespace=data.get("namespace"),
            default_board=data.get("default_board"),
            # Keep v1 fields for backward compatibility in code
            theme=boards[0].preset if boards else "software",
            paths=PathConfig(
                root=boards[0].path if boards else "work/",
                scan_paths=all_scan_paths,
            ),
        )

    def save(self, config_path: Path) -> None:
        """Save configuration to a YAML file."""
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if self.is_multi_board:
            data = self._to_dict_v2()
        else:
            data = self._to_dict_v1()

        with open(config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def _to_dict_v1(self) -> dict[str, Any]:
        """Convert to v1 dictionary format."""
        data: dict[str, Any] = {
            "kanban": {
                "theme": self.theme,
                "paths": {
                    "root": self.paths.root,
                },
                "workflows": self.workflows,
            }
        }

        if self.paths.scan_paths:
            data["kanban"]["paths"]["scan_paths"] = self.paths.scan_paths

        if self.paths.ignore:
            data["kanban"]["paths"]["ignore"] = self.paths.ignore

        if self.gates:
            data["kanban"]["gates"] = self.gates

        return data

    def _to_dict_v2(self) -> dict[str, Any]:
        """Convert to v2 dictionary format."""
        data: dict[str, Any] = {
            "version": CONFIG_VERSION_MULTI,
            "boards": [b.to_dict() for b in self.boards],
        }

        if self.namespace:
            data["namespace"] = self.namespace

        if self.default_board:
            data["default_board"] = self.default_board

        return data

    def add_board(self, board: BoardConfig) -> None:
        """Add a board to the configuration.

        If this was a single-board config, upgrade to multi-board.
        """
        if not self.is_multi_board:
            # Upgrade to multi-board
            self.version = CONFIG_VERSION_MULTI
            # Convert existing single-board config to a board
            existing = BoardConfig(
                name="default",
                preset=self.theme,
                path=self.paths.root or "work/",
            )
            self.boards = [existing]

        self.boards.append(board)

    def get_work_paths(self) -> list[Path]:
        """Get all paths where work items might be found."""
        if self.is_multi_board:
            return [board.get_path() for board in self.boards]

        # Single-board mode
        paths = []

        if self.paths.scan_paths:
            paths.extend(Path(p) for p in self.paths.scan_paths)
        elif self.paths.root:
            paths.append(Path(self.paths.root))

        # Add type-specific paths
        for type_path in [self.paths.features, self.paths.bugs, self.paths.epics, self.paths.tasks]:
            if type_path:
                paths.append(Path(type_path))

        return paths

    def get_theme(self, board_name: str | None = None) -> dict[str, Any] | None:
        """Get the theme configuration.

        In multi-board mode, get theme for specific board.
        """
        if self.is_multi_board and board_name:
            board = self.get_board(board_name)
            if board:
                return board.get_theme()
            return None

        return _load_builtin_theme(self.theme)


# ---------------------------------------------------------------------------
# Yurtle WIP Policy Loader
# ---------------------------------------------------------------------------

# RDF namespace constants for WIP policy triples
WIP_NS = "https://yurtle.dev/kanban/wip/"


def load_wip_policy(
    config_dir: Path,
) -> dict[str, dict[str, int | dict[str, int | None] | None]] | None:
    """Load WIP policy from a Yurtle file (.yurtle-kanban/wip-policy.md).

    The Yurtle file defines WIP limits as RDF triples, making them
    graph-queryable. Format:

        ```turtle
        @prefix wip: <https://yurtle.dev/kanban/wip/> .
        @prefix kb: <https://yurtle.dev/kanban/> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

        <#development> a wip:Policy ;
            wip:board "development" ;
            wip:unlimited "false"^^xsd:boolean .

        <#dev-underway-expedition> a wip:TypeLimit ;
            wip:policy <#development> ;
            wip:column "in_progress" ;
            wip:itemType "expedition" ;
            wip:limit 5 .

        <#dev-underway-chore> a wip:TypeLimit ;
            wip:policy <#development> ;
            wip:column "in_progress" ;
            wip:itemType "chore" ;
            wip:unlimited "true"^^xsd:boolean .
        ```

    Returns:
        Dict mapping board_name -> wip_limits dict (same shape as
        BoardConfig.wip_limits), or None if no policy file exists.
    """
    policy_path = config_dir / "wip-policy.md"
    if not policy_path.exists():
        return None

    try:
        from rdflib import Graph as RDFGraph
        from rdflib import Namespace

        g = RDFGraph()
        # Try yurtle-rdflib parser first, fall back to parsing turtle blocks
        try:
            g.parse(str(policy_path), format="yurtle")
        except Exception:
            # Fall back: extract turtle fenced blocks and parse
            _parse_turtle_blocks(g, policy_path)

        if len(g) == 0:
            return None

        wip = Namespace(WIP_NS)

        result: dict[str, dict[str, int | dict[str, int | None] | None]] = {}

        # Find all wip:Policy subjects
        for policy_node in g.subjects(predicate=None, object=wip.Policy):
            board_name = str(g.value(policy_node, wip.board) or "default")
            board_unlimited = g.value(policy_node, wip.unlimited)
            if board_unlimited and str(board_unlimited).lower() == "true":
                result[board_name] = None  # type: ignore[assignment]
                continue
            result[board_name] = {}

        # Find all wip:TypeLimit subjects
        for limit_node in g.subjects(predicate=None, object=wip.TypeLimit):
            policy_ref = g.value(limit_node, wip.policy)
            # Resolve board name from policy ref
            board_name = "default"
            if policy_ref:
                board_val = g.value(policy_ref, wip.board)
                if board_val:
                    board_name = str(board_val)

            if board_name not in result or result[board_name] is None:
                continue

            column = str(g.value(limit_node, wip.column) or "")
            item_type = str(g.value(limit_node, wip.itemType) or "")
            limit_val = g.value(limit_node, wip.limit)
            unlimited = g.value(limit_node, wip.unlimited)

            if not column or not item_type:
                continue

            board_wip = result[board_name]
            if not isinstance(board_wip, dict):
                continue

            # Initialize column entry as dict if needed
            if column not in board_wip:
                board_wip[column] = {}
            col_entry = board_wip[column]
            if isinstance(col_entry, int):
                # Upgrade from legacy int to dict
                col_entry = {"_default": col_entry}
                board_wip[column] = col_entry

            if unlimited and str(unlimited).lower() == "true":
                col_entry[item_type] = None  # type: ignore[index]
            elif limit_val is not None:
                col_entry[item_type] = int(limit_val)  # type: ignore[index]

        # Find aggregate wip:ColumnLimit subjects (legacy-style per-column limits)
        for limit_node in g.subjects(predicate=None, object=wip.ColumnLimit):
            policy_ref = g.value(limit_node, wip.policy)
            board_name = "default"
            if policy_ref:
                board_val = g.value(policy_ref, wip.board)
                if board_val:
                    board_name = str(board_val)

            if board_name not in result or result[board_name] is None:
                continue

            column = str(g.value(limit_node, wip.column) or "")
            limit_val = g.value(limit_node, wip.limit)

            if column and limit_val is not None:
                board_wip = result[board_name]
                if not isinstance(board_wip, dict):
                    continue
                if column not in board_wip:
                    board_wip[column] = int(limit_val)

        return result if result else None

    except ImportError:
        return None


def _parse_turtle_blocks(g: Any, md_path: Path) -> None:
    """Extract and parse turtle fenced code blocks from a markdown file."""
    content = md_path.read_text()
    import re
    blocks = re.findall(r"```turtle\s*\n(.*?)```", content, re.DOTALL)
    for block in blocks:
        try:
            g.parse(data=block, format="turtle")
        except Exception:
            continue
