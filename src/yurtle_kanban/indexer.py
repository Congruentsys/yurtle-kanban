"""
Work item indexer - discovers and parses Yurtle work items.

Unused: nothing in yurtle-kanban imports this module; ``KanbanService.scan()``
is the scanner. Kept only because it is part of the published package. Its
``paths.ignore`` handling is single-board only: multi-board scanning applies each
board's own ``BoardConfig.ignore`` (#124, #129, #153).
"""

from collections.abc import Iterator
from pathlib import Path

from rdflib import BNode, Graph, Namespace

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType

KB = Namespace("https://yurtle.dev/kanban/")


class WorkItemIndexer:
    """Discovers and indexes work items from Yurtle markdown files."""

    def __init__(self, config: KanbanConfig, repo_root: Path):
        self.config = config
        self.repo_root = repo_root
        self._items: dict[str, WorkItem] = {}

    def scan(self) -> list[WorkItem]:
        """Scan configured paths for work items."""
        self._items.clear()

        for path in self.config.get_work_paths():
            full_path = self.repo_root / path
            if full_path.exists():
                for item in self._scan_directory(full_path):
                    self._items[item.id] = item

        return list(self._items.values())

    def _scan_directory(self, directory: Path) -> Iterator[WorkItem]:
        """Scan a directory for Yurtle work items."""
        for md_file in directory.rglob("*.md"):
            # Check ignore patterns
            if self._should_ignore(md_file):
                continue

            item = self._parse_file(md_file)
            if item:
                yield item

    def _should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored."""
        path_str = str(path)
        for pattern in self.config.paths.ignore:
            # Simple glob-style matching
            if "**" in pattern:
                pattern_part = pattern.replace("**", "")
                if pattern_part.strip("/") in path_str:
                    return True
        return False

    def _parse_file(self, file_path: Path) -> WorkItem | None:
        """Parse a markdown file for Yurtle work item data."""
        try:
            # Try to parse as Yurtle
            g = Graph()
            g.parse(file_path, format="yurtle")

            def own(pattern: tuple) -> list:
                # a blank node (e.g. a `kb:statusChange [ kb:status … ]` history
                # entry) is never the item: it can't decide its type, id or status
                return [t for t in g.triples(pattern) if not isinstance(t[0], BNode)]

            # Get type
            item_type = None
            for type_name in WorkItemType:
                type_uri = KB[type_name.value.title()]
                if own((None, None, type_uri)):  # (#407)
                    item_type = type_name
                    break

            if not item_type:
                return None

            # Get ID
            item_id = None
            for _, _, obj in own((None, KB.id, None)):
                item_id = str(obj)
                break

            if not item_id:
                # Generate ID from filename
                item_id = file_path.stem.upper()

            # Get status
            status = WorkItemStatus.BACKLOG
            for _, _, obj in own((None, KB.status, None)):
                status_str = str(obj).split("/")[-1]
                try:
                    status = WorkItemStatus(status_str)
                except ValueError:
                    pass
                break

            # Get title from frontmatter or first heading
            title = file_path.stem.replace("-", " ").replace("_", " ").title()
            content = file_path.read_text()
            for line in content.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip("\"'")
                    break

            return WorkItem(
                id=item_id,
                title=title,
                item_type=item_type,
                status=status,
                file_path=file_path,
                graph=g,
            )

        except Exception:
            # Not a valid Yurtle file or parsing error
            return None

    def get_item(self, item_id: str) -> WorkItem | None:
        """Get a work item by ID."""
        return self._items.get(item_id)

    def get_items_by_status(self, status: WorkItemStatus) -> list[WorkItem]:
        """Get all work items with a specific status."""
        return [item for item in self._items.values() if item.status == status]

    def get_items_by_type(self, item_type: WorkItemType) -> list[WorkItem]:
        """Get all work items of a specific type."""
        return [item for item in self._items.values() if item.item_type == item_type]
