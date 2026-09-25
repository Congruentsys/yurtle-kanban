"""
Hybrid query engine for yurtle-kanban.

Phase 1: Unified RDF graph + SPARQL queries
Phase 2: Semantic search with sentence-transformers embeddings
Phase 3: NL decomposition + hybrid ranking (SPARQL × semantic)

Usage:
    from yurtle_kanban.query import QueryEngine
    engine = QueryEngine(service)
    results = engine.query("not-done expeditions above 700 that improve brain")
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD

from ._logging import get_logger
from .models import WorkItem

if TYPE_CHECKING:
    from .service import KanbanService

logger = get_logger("yurtle-kanban")  # escapes control characters (#215)

KB = Namespace("https://yurtle.dev/kanban/")
ITEM = Namespace("https://yurtle.dev/kanban/item/")


# ---------------------------------------------------------------------------
# Phase 1: Unified Graph
# ---------------------------------------------------------------------------


_ITEM_ID_RE = re.compile(r"[A-Za-z]+-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*")  # used with fullmatch (#183)


class UnifiedGraph:
    """Merge all per-file WorkItem graphs into a single queryable RDF graph.

    Also materializes frontmatter metadata (status, priority, assignee, tags,
    depends_on, related) as RDF triples so they can be queried with SPARQL.
    """

    @staticmethod
    def _ref_or_literal(value):
        """Return an ITEM URI ref for valid IDs, or a Literal for free text."""
        if not isinstance(value, str):
            return Literal(str(value))
        if _ITEM_ID_RE.fullmatch(value):  # `$` would accept a trailing newline
            return ITEM[value]
        return Literal(value)

    def __init__(self) -> None:
        self._graph = Graph()
        self._graph.bind("kb", KB)
        self._graph.bind("item", ITEM)
        self._graph.bind("xsd", XSD)
        self._items: dict[str, WorkItem] = {}

    @property
    def graph(self) -> Graph:
        return self._graph

    @property
    def items(self) -> dict[str, WorkItem]:
        return self._items

    def __len__(self) -> int:
        return len(self._graph)

    def add_item(self, item: WorkItem) -> None:
        """Add a work item to the unified graph."""
        self._items[item.id] = item
        item_uri = ITEM[item.id]

        # Type
        type_uri = KB[item.item_type.value.title()]
        self._graph.add((item_uri, RDF.type, type_uri))

        # Core metadata from frontmatter
        self._graph.add((item_uri, KB.id, Literal(item.id)))
        self._graph.add((item_uri, KB.title, Literal(item.title)))
        self._graph.add((item_uri, KB.status, KB[item.status.value]))

        if item.priority:
            self._graph.add((item_uri, KB.priority, KB[item.priority]))
        if item.assignee:
            self._graph.add((item_uri, KB.assignee, Literal(item.assignee)))
        if item.created:
            created_lit = Literal(item.created.isoformat(), datatype=XSD.date)
            self._graph.add((item_uri, KB.created, created_lit))
        if item.description:
            self._graph.add((item_uri, KB.description, Literal(item.description)))

        # Tags
        for tag in item.tags or []:
            self._graph.add((item_uri, KB.tag, Literal(tag)))

        # Relationships — only create URI refs for valid item IDs;
        # free-text values (with spaces, parens) become Literals to avoid
        # rdflib URI warnings (issue #59)
        for dep in item.depends_on or []:
            self._graph.add((item_uri, KB.dependsOn, self._ref_or_literal(dep)))
        for rel in item.related or []:
            self._graph.add((item_uri, KB.related, self._ref_or_literal(rel)))
        for blk in item.blocks or []:
            self._graph.add((item_uri, KB.blocks, self._ref_or_literal(blk)))
        for sup in item.superseded_by or []:
            self._graph.add((item_uri, KB.supersededBy, self._ref_or_literal(sup)))

        # Extended metadata
        if item.priority_rank is not None:
            rank_lit = Literal(item.priority_rank, datatype=XSD.integer)
            self._graph.add((item_uri, KB.priorityRank, rank_lit))
        if item.compute_requirement:
            self._graph.add((item_uri, KB.computeRequirement, Literal(item.compute_requirement)))

        # Numeric ID for range queries
        self._graph.add((item_uri, KB.numericId, Literal(item.numeric_id, datatype=XSD.integer)))

        # Merge per-file RDF graph (fenced turtle/yurtle blocks)
        if item.graph is not None:
            for triple in item.graph:
                self._graph.add(triple)

    def add_items(self, items: list[WorkItem]) -> None:
        """Add multiple work items."""
        for item in items:
            self.add_item(item)

    # Standard prefixes auto-prepended when used but not declared
    _STANDARD_PREFIXES = {
        "kb:": "PREFIX kb: <https://yurtle.dev/kanban/>\n",
        "item:": "PREFIX item: <https://yurtle.dev/kanban/item/>\n",
        "xsd:": "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n",
    }

    def _prepend_prefixes(self, query: str) -> str:
        """Auto-prepend standard PREFIX declarations if used but not declared."""
        upper = query.upper()
        for prefix_usage, declaration in self._STANDARD_PREFIXES.items():
            prefix_keyword = declaration.split(":")[0].split()[-1]  # e.g. "kb"
            # Check if prefix is used in the query but not declared
            if prefix_usage in query and f"PREFIX {prefix_keyword}:" not in upper:
                query = declaration + query
        return query

    def sparql(
        self, query: str, bindings: dict[str, Any] | None = None
    ) -> list[dict[str, str]]:
        """Execute a SPARQL SELECT query and return results as list of dicts.

        Standard prefixes (kb:, item:, xsd:) are auto-prepended if used but
        not declared in the query. `bindings` pre-binds variables (rdflib
        initBindings): values never pass through the query text, where rdflib
        expands `\\uXXXX` escapes before parsing (#184).
        """
        query = self._prepend_prefixes(query)
        results = []
        for row in self._graph.query(query, initBindings=bindings or {}):
            results.append({
                str(var): str(val) if val is not None else ""
                for var, val in zip(row.labels, row)
            })
        return results

    def sparql_raw(self, query: str):
        """Execute a SPARQL query and return the raw rdflib result."""
        query = self._prepend_prefixes(query)
        return self._graph.query(query)

    def get_item(self, item_id: str) -> WorkItem | None:
        """Look up a WorkItem by ID."""
        return self._items.get(item_id)

    @classmethod
    def from_service(cls, service: KanbanService) -> UnifiedGraph:
        """Build a unified graph from all items in a KanbanService."""
        ug = cls()
        items = service.scan()
        ug.add_items(items)
        return ug


# ---------------------------------------------------------------------------
# Phase 2: Embedding Index
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingHit:
    """A semantic search result."""
    item_id: str
    score: float
    item: WorkItem | None = None


def _importable(module: str) -> bool:
    """Whether `module` can be imported, without importing it (a `None` entry in
    `sys.modules` means it is blocked)."""
    import importlib.util
    import sys

    if module in sys.modules:
        return sys.modules[module] is not None
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


class EmbeddingIndex:
    """Semantic search over work item titles and descriptions.

    Uses sentence-transformers to embed text and numpy for cosine similarity.
    Embeddings are cached to disk with hash-based invalidation.
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"

    def __init__(self, cache_dir: Path | None = None, model_name: str | None = None):
        # say up front when the search extra is missing, so callers can fall back to
        # graph-only instead of failing later inside search() (#346)
        missing = [
            m for m in ("numpy", "sentence_transformers") if not _importable(m)
        ]
        if missing:
            raise ImportError(
                f"{' and '.join(missing)} not installed: semantic search needs "
                "sentence-transformers. Install with: pip install yurtle-kanban[search]"
            )
        self._model_name = model_name or self.DEFAULT_MODEL
        self._model = None  # lazy load
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._embeddings = None  # numpy array, lazy
        self._cache_dir = cache_dir
        self._items: dict[str, WorkItem] = {}

    def _load_model(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
        except ImportError as e:
            # keep the real cause: a broken torch reads very differently from a
            # missing package (#371)
            raise ImportError(
                f"sentence-transformers is required for semantic search ({e}). "
                "Install with: pip install yurtle-kanban[search]"
            ) from e

    def add_item(self, item: WorkItem) -> None:
        """Add a work item to the index."""
        text = item.title
        if item.description:
            text = f"{item.title}\n{item.description}"
        self._ids.append(item.id)
        self._texts.append(text)
        self._items[item.id] = item
        self._embeddings = None  # invalidate

    def add_items(self, items: list[WorkItem]) -> None:
        for item in items:
            self.add_item(item)

    def _ensure_embeddings(self):
        if self._embeddings is not None:
            return

        # Check cache
        if self._cache_dir and self._try_load_cache():
            return

        self._load_model()
        self._embeddings = self._model.encode(self._texts, show_progress_bar=False)

        # Save cache
        if self._cache_dir:
            self._save_cache()

    def _cache_hash(self) -> str:
        """Hash of all item IDs + texts for cache invalidation."""
        h = hashlib.sha256()
        for item_id, text in zip(self._ids, self._texts):
            h.update(f"{item_id}:{text}".encode())
        return h.hexdigest()[:16]

    def _try_load_cache(self) -> bool:
        import numpy as np

        if not self._cache_dir:
            return False
        cache_file = self._cache_dir / "embeddings.npz"
        meta_file = self._cache_dir / "embeddings_meta.json"
        if not cache_file.exists() or not meta_file.exists():
            return False
        try:
            meta = json.loads(meta_file.read_text())
            if meta.get("hash") != self._cache_hash():
                return False
            data = np.load(cache_file)
            self._embeddings = data["embeddings"]
            return True
        except Exception:
            return False

    def _save_cache(self):
        import numpy as np

        if not self._cache_dir or self._embeddings is None:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        np.savez(self._cache_dir / "embeddings.npz", embeddings=self._embeddings)
        meta = {"hash": self._cache_hash(), "model": self._model_name, "count": len(self._ids)}
        (self._cache_dir / "embeddings_meta.json").write_text(json.dumps(meta))

    def search(self, query: str, top_k: int = 10) -> list[EmbeddingHit]:
        """Search for items semantically similar to the query."""
        try:
            import numpy as np
        except ImportError as e:
            # installed but broken: say how to fix it, like a missing extra (#358)
            raise ImportError(
                f"numpy could not be imported ({e}): semantic search needs the search "
                "extra. Install with: pip install yurtle-kanban[search]"
            ) from e

        self._ensure_embeddings()
        self._load_model()

        query_emb = self._model.encode([query], show_progress_bar=False)
        # Cosine similarity
        norms = np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(query_emb)
        norms = np.where(norms == 0, 1, norms)  # avoid div by zero
        scores = (self._embeddings @ query_emb.T).squeeze() / norms

        top_indices = np.argsort(scores)[::-1][:top_k]
        return [
            EmbeddingHit(
                item_id=self._ids[i],
                score=float(scores[i]),
                item=self._items.get(self._ids[i]),
            )
            for i in top_indices
        ]

    @classmethod
    def from_service(cls, service: KanbanService, cache_dir: Path | None = None) -> EmbeddingIndex:
        """Build an embedding index from all items in a KanbanService."""
        if cache_dir is None:
            cache_dir = service.repo_root / ".yurtle-kanban" / "embeddings"
        idx = cls(cache_dir=cache_dir)
        items = service.scan()
        idx.add_items(items)
        return idx


# ---------------------------------------------------------------------------
# Phase 3: NL Decomposer + Hybrid Query
# ---------------------------------------------------------------------------


@dataclass
class ParsedQuery:
    """A natural language query decomposed into structured + semantic parts."""
    status_filter: list[str] = field(default_factory=list)  # e.g. ["done"] to exclude
    status_include: list[str] = field(default_factory=list)  # e.g. ["in_progress"] to include
    type_filter: list[str] = field(default_factory=list)  # e.g. ["expedition"]
    id_min: int | None = None
    id_max: int | None = None
    assignee: str | None = None
    tag: str | None = None
    semantic_query: str = ""  # remaining NL intent for embedding search

    @property
    def has_structured(self) -> bool:
        return bool(
            self.status_filter or self.status_include or self.type_filter
            or self.id_min is not None or self.id_max is not None
            or self.assignee or self.tag
        )

    @property
    def has_semantic(self) -> bool:
        return bool(self.semantic_query.strip())


class NLDecomposer:
    """Rule-based decomposition of NL queries into structured + semantic parts.

    Extracts:
    - Status filters: "not done", "in progress", "backlog"
    - Type filters: "expeditions", "chores", "voyages"
    - ID ranges: "above 700", "below 500", "between 100 and 200"
    - Assignee: "assigned to Mini", "by DGX"
    - Tags: "tagged brain", "with tag safety"
    - Everything else becomes the semantic query
    """

    # Status patterns
    _STATUS_EXCLUDE = re.compile(
        r"\b(?:not?[- ]?done|not?[- ]?arrived|not?[- ]?completed?|incomplete|open|active)\b",
        re.IGNORECASE,
    )
    _STATUS_INCLUDE_MAP = {
        re.compile(r"\bin[- ]?progress\b", re.IGNORECASE): "in_progress",
        re.compile(r"\bblocked\b", re.IGNORECASE): "blocked",
        re.compile(r"\bbacklog\b", re.IGNORECASE): "backlog",
        re.compile(r"\bin[- ]?review\b", re.IGNORECASE): "review",
        re.compile(r"\bstranded\b", re.IGNORECASE): "blocked",
    }

    # Type patterns
    _TYPE_MAP = {
        re.compile(r"\bexpeditions?\b", re.IGNORECASE): "expedition",
        re.compile(r"\bchores?\b", re.IGNORECASE): "chore",
        re.compile(r"\bvoyages?\b", re.IGNORECASE): "voyage",
        re.compile(r"\bhazards?\b", re.IGNORECASE): "hazard",
        re.compile(r"\bsignals?\b", re.IGNORECASE): "signal",
        re.compile(r"\bpapers?\b", re.IGNORECASE): "paper",
        re.compile(r"\bhypothes[ei]s\b", re.IGNORECASE): "hypothesis",
        re.compile(r"\bexperiments?\b", re.IGNORECASE): "experiment",
    }

    # ID range patterns
    _ID_ABOVE = re.compile(
        r"(?:\b(?:above|over|greater than|after)\s+|(?<!\w)[>=]+\s*)(\d+)\b",
        re.IGNORECASE,
    )
    _ID_BELOW = re.compile(
        r"(?:\b(?:below|under|less than|before)\s+|(?<!\w)[<=]+\s*)(\d+)\b",
        re.IGNORECASE,
    )
    _ID_BETWEEN = re.compile(r"\bbetween\s*(\d+)\s*(?:and|to|-)\s*(\d+)\b", re.IGNORECASE)

    # Assignee
    _ASSIGNEE = re.compile(
        r"\b(?:assigned?\s+to|by|from|owner)\s+([A-Za-z0-9_-]+)\b",
        re.IGNORECASE,
    )

    # Tag
    _TAG = re.compile(
        r"\b(?:tagged?|with\s+tag)\s+([A-Za-z0-9_-]+)\b",
        re.IGNORECASE,
    )

    def parse(self, query: str) -> ParsedQuery:
        """Parse a natural language query into structured + semantic parts."""
        parsed = ParsedQuery()
        remaining = query

        # Status exclusions
        if self._STATUS_EXCLUDE.search(remaining):
            parsed.status_filter.append("done")
            remaining = self._STATUS_EXCLUDE.sub("", remaining)

        # Status inclusions
        for pattern, status in self._STATUS_INCLUDE_MAP.items():
            if pattern.search(remaining):
                if status not in parsed.status_include:  # "blocked and stranded" (#355)
                    parsed.status_include.append(status)
                remaining = pattern.sub("", remaining)

        # Type filters
        for pattern, type_name in self._TYPE_MAP.items():
            if pattern.search(remaining):
                parsed.type_filter.append(type_name)
                remaining = pattern.sub("", remaining)

        # ID ranges
        m = self._ID_BETWEEN.search(remaining)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            parsed.id_min = min(a, b)
            parsed.id_max = max(a, b)
            remaining = self._ID_BETWEEN.sub("", remaining)
        else:
            m = self._ID_ABOVE.search(remaining)
            if m:
                parsed.id_min = int(m.group(1))
                remaining = self._ID_ABOVE.sub("", remaining)
            m = self._ID_BELOW.search(remaining)
            if m:
                parsed.id_max = int(m.group(1))
                remaining = self._ID_BELOW.sub("", remaining)

        # Assignee
        m = self._ASSIGNEE.search(remaining)
        if m:
            parsed.assignee = m.group(1)
            remaining = self._ASSIGNEE.sub("", remaining)

        # Tag
        m = self._TAG.search(remaining)
        if m:
            parsed.tag = m.group(1)
            remaining = self._TAG.sub("", remaining)

        # Clean up remaining as semantic query
        remaining = re.sub(r"\s+", " ", remaining).strip()
        # Remove leading/trailing connectors
        remaining = re.sub(
            r"^(?:that|which|who|and|or|the|a|an|all|show|find|list|get)\s+",
            "", remaining, flags=re.IGNORECASE,
        )
        remaining = re.sub(r"\s+(?:that|which)\s*$", "", remaining, flags=re.IGNORECASE)
        parsed.semantic_query = remaining.strip()

        return parsed


@dataclass
class QueryResult:
    """A ranked query result."""
    item: WorkItem
    graph_match: bool = True
    semantic_score: float = 0.0
    combined_score: float = 0.0


class QueryEngine:
    """Hybrid query engine combining SPARQL graph queries with semantic search.

    Usage:
        engine = QueryEngine.from_service(service)
        results = engine.query("not-done expeditions above 700 that improve brain")
    """

    def __init__(
        self,
        unified_graph: UnifiedGraph,
        embedding_index: EmbeddingIndex | None = None,
        alpha: float = 0.3,
    ):
        self._ug = unified_graph
        self._emb = embedding_index
        self._decomposer = NLDecomposer()
        self._alpha = alpha  # weight for graph match in combined score

    @property
    def semantic_enabled(self) -> bool:
        """Whether semantic ranking is available (the search extra is installed)."""
        return self._emb is not None

    def sparql(self, query: str) -> list[dict[str, str]]:
        """Direct SPARQL query pass-through."""
        return self._ug.sparql(query)

    def structured_query(self, parsed: ParsedQuery) -> list[WorkItem]:
        """Execute structured constraints against the unified graph."""
        # Build SPARQL dynamically
        filters = []
        bindings: dict[str, Any] = {}  # user values, bound rather than spliced in
        wheres = [
            "?item kb:id ?id .",
            "?item kb:status ?status .",
            "?item kb:numericId ?numId .",
        ]

        # Statuses and types are bound as IRIs, never spliced into the query text:
        # nothing in a ParsedQuery value can break or inject into it (#355, as #162
        # does for literals); a value that isn't a real status/type just matches nothing
        def iri_list(prefix: str, values: list[str]) -> str:
            names = []
            for i, value in enumerate(values):
                bindings[f"{prefix}{i}"] = URIRef(str(KB) + value)
                names.append(f"?{prefix}{i}")
            return ", ".join(names)  # IN needs commas (#64)

        # Status exclusions
        for i, status in enumerate(parsed.status_filter):
            bindings[f"statusOut{i}"] = URIRef(str(KB) + status)
            filters.append(f"FILTER(?status != ?statusOut{i})")

        # Status inclusions
        if parsed.status_include:
            values = iri_list("statusIn", parsed.status_include)
            filters.append(f"FILTER(?status IN ({values}))")

        # Type filters
        if parsed.type_filter:
            type_values = iri_list("type", [t.title() for t in parsed.type_filter])
            wheres.append(f"?item a ?type . FILTER(?type IN ({type_values}))")

        # ID range
        if parsed.id_min is not None:
            filters.append(f"FILTER(?numId > {parsed.id_min})")
        if parsed.id_max is not None:
            filters.append(f"FILTER(?numId < {parsed.id_max})")

        # Assignee
        if parsed.assignee:
            wheres.append("?item kb:assignee ?assignee .")
            # a bound Literal, never query text: nothing in the value can break or
            # inject into the query (#162), and a literal `\u0041` stays literal (#184)
            bindings["assigneeNeedle"] = Literal(parsed.assignee.lower())
            filters.append("FILTER(CONTAINS(LCASE(?assignee), ?assigneeNeedle))")

        # Tag
        if parsed.tag:
            wheres.append("?item kb:tag ?tag .")
            bindings["tagNeedle"] = Literal(parsed.tag.lower())
            filters.append("FILTER(CONTAINS(LCASE(?tag), ?tagNeedle))")

        sparql_query = (
            "PREFIX kb: <https://yurtle.dev/kanban/>\n"
            "PREFIX item: <https://yurtle.dev/kanban/item/>\n"
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
            # DISTINCT: an item matching through two types/tags/assignees is one row
            # (#349); ?numId is projected so ORDER BY still orders the distinct rows
            "SELECT DISTINCT ?id ?numId WHERE {\n  "
            + "\n  ".join(wheres)
            + "\n  "
            + "\n  ".join(filters)
            + "\n} ORDER BY DESC(?numId)"
        )

        logger.debug("Generated SPARQL:\n%s", sparql_query)

        results = self._ug.sparql(sparql_query, bindings)
        items = []
        seen: set[str] = set()
        for row in results:
            # DISTINCT still leaves one row per numericId when an item's block adds a
            # second one: keep the first (highest) row per id (#373)
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            item = self._ug.get_item(row["id"])
            if item:
                items.append(item)
        return items

    def semantic_search(self, query: str, top_k: int = 50) -> list[EmbeddingHit]:
        """Pure semantic search."""
        if self._emb is None:
            raise RuntimeError(
                "No embedding index available. "
                "Initialize QueryEngine with an EmbeddingIndex for semantic search."
            )
        return self._emb.search(query, top_k=top_k)

    def query(self, nl_query: str, top_k: int = 20) -> list[QueryResult]:
        """Execute a hybrid NL query: decompose → graph filter → semantic rank."""
        parsed = self._decomposer.parse(nl_query)

        # Phase 1: structured query
        if parsed.has_structured:
            graph_items = self.structured_query(parsed)
        else:
            # No structured constraints — all items are candidates
            graph_items = list(self._ug.items.values())

        # If no semantic component or no embedding index, return graph results
        if not parsed.has_semantic or self._emb is None:
            return [
                QueryResult(item=item, graph_match=True, combined_score=1.0)
                for item in graph_items[:top_k]  # graph-only honours top_k too (#371)
            ]

        # Phase 2: semantic ranking within graph results
        try:
            semantic_hits = self._emb.search(parsed.semantic_query, top_k=len(graph_items))
        except ImportError as e:
            # the search extra passed the probe but won't import (e.g. torch
            # missing): graph-only from here on, as if it weren't installed (#358)
            logger.info(f"semantic search disabled: {e}")
            self._emb = None
            return [
                QueryResult(item=item, graph_match=True, combined_score=1.0)
                for item in graph_items[:top_k]
            ]
        score_map = {hit.item_id: hit.score for hit in semantic_hits}

        # Combine: items must pass graph filter, ranked by semantic score
        results = []
        for item in graph_items:
            sem_score = score_map.get(item.id, 0.0)
            combined = self._alpha * 1.0 + (1 - self._alpha) * sem_score
            results.append(
                QueryResult(
                    item=item,
                    graph_match=True,
                    semantic_score=sem_score,
                    combined_score=combined,
                )
            )

        # Sort by combined score descending
        results.sort(key=lambda r: r.combined_score, reverse=True)
        return results[:top_k]

    @classmethod
    def from_service(
        cls,
        service: KanbanService,
        enable_semantic: bool = True,
        cache_dir: Path | None = None,
        alpha: float = 0.3,
    ) -> QueryEngine:
        """Build a QueryEngine from a KanbanService.

        Args:
            service: The kanban service with scanned items
            enable_semantic: Whether to build the embedding index
            cache_dir: Directory for embedding cache (default: .yurtle-kanban/embeddings/)
            alpha: Weight for graph match vs semantic score (0-1)
        """
        ug = UnifiedGraph.from_service(service)

        emb = None
        if enable_semantic:
            try:
                if cache_dir is None:
                    cache_dir = service.repo_root / ".yurtle-kanban" / "embeddings"
                emb = EmbeddingIndex(cache_dir=cache_dir)
                emb.add_items(list(ug.items.values()))
            except ImportError:
                logger.info("sentence-transformers not installed; semantic search disabled")

        return cls(unified_graph=ug, embedding_index=emb, alpha=alpha)
