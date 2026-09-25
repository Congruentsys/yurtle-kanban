"""
Configuration management for yurtle-kanban.

Supports both single-board (v1) and multi-board (v2) configurations.
Multi-board is opt-in: detected when config has 'version: 2.0' and 'boards' key.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ._logging import get_logger

logger = get_logger("yurtle-kanban")

# Cache for loaded themes
_theme_cache: dict[str, dict[str, Any] | None] = {}  # None: not a mapping (#338)

# Config version constants
CONFIG_VERSION_SINGLE = "1.0"
CONFIG_VERSION_MULTI = "2.0"


def _theme_dirs(repo_root: Path | None = None) -> list[Path]:
    """Where themes are looked up, first match wins: the repo's .kanban/themes/,
    the cwd's, the pip-installed share directory, then the source tree."""
    import sys

    dirs = []
    if repo_root:
        dirs.append(repo_root / ".kanban" / "themes")
    dirs.append(Path.cwd() / ".kanban" / "themes")
    dirs.append(Path(sys.prefix) / "share" / "yurtle-kanban" / "themes")
    try:
        import yurtle_kanban

        dirs.append(Path(yurtle_kanban.__file__).parent.parent.parent / "themes")
    except Exception:
        pass
    dirs.append(Path(__file__).parent.parent.parent / "themes")
    return dirs


def _available_themes(repo_root: Path | None = None) -> list[str]:
    """Every theme name a config could use (#272)."""
    names = set()
    for d in _theme_dirs(repo_root):
        try:
            names.update(p.stem for p in d.glob("*.yaml"))
        except OSError:
            continue
    return sorted(names)


def _load_builtin_theme(theme_name: str, repo_root: Path | None = None) -> dict[str, Any] | None:
    """Load a theme from local .kanban/themes/ or package resources.

    The cache is keyed by the theme FILE that wins the lookup, not by the name:
    two repos with different `.kanban/themes/nautical.yaml` never share an entry,
    and a repo without an override still shares the built-in (#287)."""
    for theme_dir in _theme_dirs(repo_root):
        theme_path = theme_dir / f"{theme_name}.yaml"
        try:
            if not theme_path.exists():
                continue
            key = str(theme_path.resolve())
            if key not in _theme_cache:
                with open(theme_path) as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    # a YAML list or scalar is no theme: treat it as missing, and
                    # cache that so it is said once, not on every lookup (#338)
                    logger.warning(
                        f"theme file {theme_path} is not a mapping "
                        f"({type(data).__name__}); ignored"
                    )
                    data = None
                _theme_cache[key] = data
            return _theme_cache[key]
        except Exception:
            continue

    return None


def _or_default(data: dict[str, Any], key: str, default: str) -> Any:
    """`data[key]`, or `default` when the key is absent or null (#220). Only null:
    an explicit `""` is a value (#241)."""
    value = data.get(key)
    return default if value is None else value


def _theme_name(
    data: dict[str, Any], key: str, where: str, repo_root: Path | None = None
) -> Any:
    """A theme/preset name: null means the default (#220), an explicit value is
    kept (#241), but an empty or blank one is never a theme, so say so (#256)."""
    value = _or_default(data, key, "software")
    if not isinstance(value, str):
        # a list or mapping crashed the theme lookup; a number loaded nothing (#272)
        raise ValueError(
            f"`{key}`{where} must be a string theme name, got {type(value).__name__} {value!r}"
        )
    if not value.strip():
        logger.warning(
            f"config: `{key}` is empty{where}; no theme is loaded "
            "(no WIP limits or workflows). Remove the key for the default."
        )
    elif _load_builtin_theme(value, repo_root) is None:
        logger.warning(
            f"config: `{key}`{where} is {value!r}, which is not a known theme; no theme is "
            f"loaded. Available: {', '.join(_available_themes(repo_root))}"
        )
    return value


def _ignore_list(data: dict[str, Any]) -> list[str]:
    """`ignore` patterns from a config mapping: absent → the defaults; a bare
    `ignore:` (YAML null) → none, not a crash in the scan (#194)."""
    if "ignore" not in data:
        return ["**/archive/**", "**/templates/**"]
    value = data["ignore"]
    # one pattern given as a string is that pattern, not its characters (#204)
    if isinstance(value, str):
        return [value]
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            f"ignore: expected a list of glob patterns, got {type(value).__name__} {value!r}"
        )
    return list(value)


@dataclass
class PathConfig:
    """Configuration for work item paths."""

    root: str | None = "work/"
    scan_paths: list[str] = field(default_factory=list)
    # Single-board only: multi-board scanning uses each BoardConfig.ignore (#124, #129)
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
    def from_dict(cls, data: dict[str, Any], repo_root: Path | None = None) -> "BoardConfig":
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
            # a bare (null) key means its default, like an absent one (#220); an
            # explicit "" keeps its meaning (`path: ""` is the repo root, #241)
            name=_or_default(data, "name", "default"),
            preset=_theme_name(
                data, "preset", f" for board {data.get('name')!r}", repo_root
            ),
            path=_or_default(data, "path", "work/"),
            # a bare key (YAML null) means empty, never None (#194, #204)
            scan_paths=data.get("scan_paths") or [],
            wip_limits=wip_limits,
            wip_exempt_types=data.get("wip_exempt_types") or [],
            gates=data.get("gates") or {},
            ignore=_ignore_list(data),
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
    # the repo this config belongs to (set by load); themes resolve there first (#287)
    repo_root: Path | None = field(default=None, repr=False, compare=False)

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
        if not isinstance(data, dict):
            # the CLI reports a ValueError as an invalid config, not a traceback (#338)
            raise ValueError(f"the config must be a mapping, got {type(data).__name__}")
        # themes are looked up in the config's own repo first (`<repo>/.kanban/…`),
        # whatever the cwd, as the service does (#272)
        repo_root = config_path.absolute().parent.parent  # as given, like the service

        # Check for v2 multi-board config
        version = data.get("version", CONFIG_VERSION_SINGLE)
        # a bare `boards:` is the same as none: fall back to v1 (#204)
        if version == CONFIG_VERSION_MULTI and data.get("boards") is not None:
            config = cls._load_v2(data, repo_root)
            config.repo_root = repo_root
            return config
        dropped = [k for k in ("namespace", "default_board") if data.get(k) is not None]
        if version == CONFIG_VERSION_MULTI and "boards" in data and dropped:
            logger.warning(
                f"config: `boards:` is empty, so this loads as a single-board config and "
                f"ignores {', '.join(dropped)} (#220)"
            )

        # Fall back to v1 single-board config
        config = cls._load_v1(data, repo_root)
        config.repo_root = repo_root
        return config

    @classmethod
    def _load_v1(cls, data: dict[str, Any], repo_root: Path | None = None) -> "KanbanConfig":
        """Load v1 single-board configuration."""
        # a bare key (YAML null) means empty, never None (#194, #204)
        kanban_data = data.get("kanban", data) or {}

        paths_data = kanban_data.get("paths") or {}
        paths = PathConfig(
            root=_or_default(paths_data, "root", "work/"),
            scan_paths=paths_data.get("scan_paths") or [],
            ignore=_ignore_list(paths_data),
            features=paths_data.get("features"),
            bugs=paths_data.get("bugs"),
            epics=paths_data.get("epics"),
            tasks=paths_data.get("tasks"),
        )

        return cls(
            version=CONFIG_VERSION_SINGLE,
            theme=_theme_name(kanban_data, "theme", "", repo_root),
            paths=paths,
            workflows=kanban_data.get("workflows") or {},
            gates=kanban_data.get("gates") or {},
        )

    @classmethod
    def _load_v2(cls, data: dict[str, Any], repo_root: Path | None = None) -> "KanbanConfig":
        """Load v2 multi-board configuration."""
        # a bare `- ` list entry is skipped, not a crash (#220)
        boards = [
            BoardConfig.from_dict(b, repo_root) for b in data.get("boards", []) if b is not None
        ]

        # Aggregate scan_paths from all boards for Priority 3 fallback. Ignore
        # patterns stay per board: scan() applies each board's own (#124)
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

        # `[]` is written too: left out, it would reload as the defaults (#194)
        data["kanban"]["paths"]["ignore"] = list(self.paths.ignore)

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

    def _single_board_path(self) -> str:
        """The directory a single-board config really scans, as one board path.

        A multi-board board scans only its ``path``, so upgrading must not use a
        ``root`` the board never scanned. A default ``init`` writes ``root: work/``
        but scans ``kanban-work/*`` (#94). When a scan path contains ``root``, use that
        scan path (the board must cover everything the config scanned, not narrow
        to ``root``); else the common parent of the scan paths.
        """
        root = self.paths.root
        scans = [Path(p) for p in self.paths.scan_paths]
        if not scans:
            return root or "work/"
        if root:
            for raw, scan in zip(self.paths.scan_paths, scans):
                if Path(root) == scan or scan in Path(root).parents:
                    return raw
        try:
            common = Path(os.path.commonpath([str(s) for s in scans]))
        except ValueError:  # absolute and relative scan paths mixed (#147)
            return root or "work/"
        return f"{common.as_posix()}/" if str(common) not in ("", ".") else (root or "work/")

    def add_board(self, board: BoardConfig) -> None:
        """Add a board to the configuration.

        If this was a single-board config, upgrade to multi-board.
        """
        if not self.is_multi_board:
            # Upgrade to multi-board
            self.version = CONFIG_VERSION_MULTI
            # Convert existing single-board config to a board, keeping it where
            # its items actually are (#94)
            existing = BoardConfig(
                name="default",
                preset=self.theme,
                path=self._single_board_path(),
                scan_paths=list(self.paths.scan_paths),
                ignore=list(self.paths.ignore),
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
                return board.get_theme(self.repo_root)
            return None

        return _load_builtin_theme(self.theme, self.repo_root)


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
