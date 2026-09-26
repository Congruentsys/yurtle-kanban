"""
Work item indexer - discovers and parses Yurtle work items.

Deprecated (#434), kept until removal: use ``KanbanService.scan()``, the scanner.
Nothing in yurtle-kanban imports this module. Its
``paths.ignore`` handling is single-board only: multi-board scanning applies each
board's own ``BoardConfig.ignore`` (#124, #129, #153).
"""

import warnings
from collections.abc import Iterator
from pathlib import Path

from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef

from yurtle_kanban._graph_iri import set_self_iri
from yurtle_kanban.config import KanbanConfig, _under
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType

KB = Namespace("https://yurtle.dev/kanban/")
# where yurtle_rdflib maps YAML frontmatter keys (`id:`, `status:`) (#426)
SCHEMA = Namespace("https://yurtle.dev/schema/")
PM = Namespace("https://yurtle.dev/pm/")


class WorkItemIndexer:
    """Discovers and indexes work items from Yurtle markdown files.

    Deprecated (#434): it diverges from `KanbanService.scan` (types, status
    spellings, theme types, id fallback); use `KanbanService.scan` instead.
    """

    def __init__(self, config: KanbanConfig, repo_root: Path):
        warnings.warn(
            "WorkItemIndexer is deprecated: it diverges from KanbanService.scan, which "
            "is the one way to read a board (#434)",
            DeprecationWarning,
            stacklevel=2,
        )
        self.config = config
        self.repo_root = repo_root
        self._items: dict[str, WorkItem] = {}

    def scan(self) -> list[WorkItem]:
        """Scan configured paths for work items."""
        self._items.clear()

        for path in self.config.get_work_paths():
            full_path = _under(self.repo_root, path)
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
            set_self_iri(g, Path.cwd().as_uri() + "/")  # what `<>` meant (#421)

            # The item is the (non-blank) subject typed `a kb:<Type>`: only rdf:type
            # decides the type, never `kb:related kb:Feature` (#416), and a blank node
            # (e.g. a `kb:statusChange [ … ]` history entry) is never the item (#407).
            # Its id and status come from that subject alone, not from another one in
            # the file (#416). Type order follows WorkItemType; ties pick the
            # smallest subject IRI, so the choice is stable.
            item_type = None
            subject = None
            for type_name in WorkItemType:
                typed = sorted(
                    str(s) for s in g.subjects(RDF.type, KB[type_name.value.title()])
                    if not isinstance(s, BNode)
                )
                if typed:
                    item_type, subject = type_name, URIRef(typed[0])
                    break

            if not item_type:
                # YAML frontmatter gives `rdf:type "expedition"` (a literal) on the
                # document subject; an IRI type above always wins (#426)
                by_value = {t.value: t for t in WorkItemType}
                found = sorted(
                    (list(WorkItemType).index(by_value[str(o).lower()]), str(s))
                    for s, o in g.subject_objects(RDF.type)
                    if isinstance(o, Literal) and not isinstance(s, BNode)
                    and str(o).lower() in by_value
                )
                if found:
                    index, iri = found[0]
                    item_type, subject = list(WorkItemType)[index], URIRef(iri)

            if not item_type:
                return None

            # Get ID (from the filename when the item has none); frontmatter maps
            # `id:` to schema:id, a turtle block writes kb:id (#426)
            own_id = g.value(subject, KB.id) or g.value(subject, SCHEMA.id)
            item_id = str(own_id) if own_id is not None else file_path.stem.upper()

            # Get status: the first by WorkItemStatus order when there are several,
            # so the pick is stable (#426); frontmatter maps `status:` to pm:status
            known = []
            for value in [*g.objects(subject, KB.status), *g.objects(subject, PM.status)]:
                try:
                    known.append(WorkItemStatus(str(value).split("/")[-1]))
                except ValueError:
                    pass
            order = list(WorkItemStatus)
            status = min(known, key=order.index) if known else WorkItemStatus.BACKLOG

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
