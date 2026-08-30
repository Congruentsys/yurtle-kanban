"""
yurtle-kanban: File-based kanban using Yurtle (Turtle RDF in Markdown).

Git is your database.

Usage:
    # CLI
    yurtle-kanban init
    yurtle-kanban board
    yurtle-kanban list
    yurtle-kanban create feature "My feature"
    yurtle-kanban move FEAT-001 done

    # Python API
    from yurtle_kanban import KanbanService, KanbanConfig
    config = KanbanConfig.load(Path(".kanban/config.yaml"))
    service = KanbanService(config, Path.cwd())
    items = service.get_items()

    # MCP Server
    yurtle-kanban-mcp
"""

__version__ = "2.2.0"

# yurtle-kanban is a graph-native product — rdflib + yurtle-rdflib are required.
# Register the yurtle_rdflib plugin before any Graph.parse(format="yurtle") calls.
try:
    import yurtle_rdflib  # noqa: F401
except ImportError as e:
    raise ImportError(
        "yurtle-kanban is a graph-native product — please install rdflib and "
        "yurtle-rdflib, or I'll stay KAN'Tban instead of kanban!\n\n"
        "  pip install rdflib yurtle-rdflib\n"
    ) from e

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.gates import GateDefinition, GateEvaluator, GateResult
from yurtle_kanban.hooks import HookContext, HookEngine, HookEvent
from yurtle_kanban.models import (
    Board,
    Column,
    Comment,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)
from yurtle_kanban.query import NLDecomposer, QueryEngine, QueryResult, UnifiedGraph
from yurtle_kanban.service import KanbanService
from yurtle_kanban.workflow import (
    StateConfig,
    TransitionRule,
    WorkflowConfig,
    WorkflowParser,
    get_default_workflow,
)

__all__ = [
    # Config
    "KanbanConfig",
    # Gates
    "GateDefinition",
    "GateEvaluator",
    "GateResult",
    # Hooks
    "HookContext",
    "HookEngine",
    "HookEvent",
    # Models
    "Board",
    "Column",
    "Comment",
    "WorkItem",
    "WorkItemStatus",
    "WorkItemType",
    # Query Engine
    "NLDecomposer",
    "QueryEngine",
    "QueryResult",
    "UnifiedGraph",
    # Service
    "KanbanService",
    # Workflow
    "StateConfig",
    "TransitionRule",
    "WorkflowConfig",
    "WorkflowParser",
    "get_default_workflow",
]
