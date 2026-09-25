# Query Engine

yurtle-kanban 2.0 introduces a hybrid query engine that combines SPARQL graph queries with semantic search. Every work item is already an RDF graph node — the query engine connects them into a single queryable knowledge graph.

## Installation

```bash
# Core (SPARQL + structured NL queries) — no extra deps
pip install yurtle-kanban

# With semantic search (sentence-transformers)
pip install yurtle-kanban[search]

# Everything (search + LLM + MCP)
pip install yurtle-kanban[all]
```

## Three Query Modes

### 1. Natural Language (Hybrid)

The most powerful mode. Decompose natural language into structured graph filters + semantic ranking:

```bash
yurtle-kanban query "not-done expeditions above 700 that improve brain functioning"
```

This automatically:
1. **Extracts structured filters**: `status != done`, `type = expedition`, `numericId > 700`
2. **Generates SPARQL** to narrow the candidate set
3. **Embeds the semantic intent** ("improve brain functioning") and ranks results by cosine similarity

Use `--verbose` to see the decomposition:

```bash
yurtle-kanban query "not-done expeditions above 700 that improve brain" -v
```

```
Parsed query:
  Status exclude: ['done']
  Type: ['expedition']
  ID min: 700
  Semantic: "improve brain functioning"
```

#### What the NL Decomposer Understands

| Pattern | Extracted As | Example |
|---------|-------------|---------|
| "not done", "not-done", "incomplete", "open" | Status exclude: done | `"not-done items"` |
| "in progress", "blocked", "backlog", "in review" | Status include | `"blocked expeditions"` |
| "above N", "over N", "greater than N" | ID min | `"above 700"` |
| "below N", "under N", "less than N" | ID max | `"below 500"` |
| "between N and M" | ID range | `"between 100 and 200"` |
| "expeditions", "chores", "voyages", etc. | Type filter | `"all chores"` |
| "assigned to X", "by X" | Assignee filter | `"assigned to Mini"` |
| "tagged X", "with tag X" | Tag filter | `"tagged brain"` |
| Everything else | Semantic query | `"improve brain functioning"` |

#### Graph-Only Mode

If `sentence-transformers` is not installed, or you use `--no-semantic`, the engine falls back to graph-only mode. Structured filters still work:

```bash
yurtle-kanban query "not-done expeditions above 700" --no-semantic
```

### 2. Raw SPARQL

Query the unified RDF graph directly with SPARQL:

```bash
# All in-progress items with their assignees
yurtle-kanban query --sparql "
  PREFIX kb: <https://yurtle.dev/kanban/>
  SELECT ?id ?title ?assignee WHERE {
    ?item kb:id ?id .
    ?item kb:title ?title .
    ?item kb:status kb:in_progress .
    OPTIONAL { ?item kb:assignee ?assignee . }
  }
  ORDER BY ?id
"
```

#### Available Predicates

The unified graph materializes all frontmatter metadata as RDF triples:

| Predicate | Type | Description |
|-----------|------|-------------|
| `kb:id` | Literal | Work item ID (e.g., "EXP-1090") |
| `kb:title` | Literal | Title text |
| `kb:status` | URI | Status as `kb:backlog`, `kb:in_progress`, `kb:review`, `kb:done`, `kb:blocked` |
| `kb:priority` | URI | Priority as `kb:critical`, `kb:high`, `kb:medium`, `kb:low` |
| `kb:assignee` | Literal | Assignee name |
| `kb:created` | xsd:date | Creation date |
| `kb:tag` | Literal | Tags (one triple per tag) |
| `kb:numericId` | xsd:integer | Numeric part of ID for range queries |
| `kb:dependsOn` | URI | Dependency link to another item |
| `kb:related` | URI | Related item link |
| `kb:blocks` | URI | Blocking relationship |
| `kb:supersededBy` | URI | Supersession link |
| `kb:description` | Literal | Full description text |
| `kb:priorityRank` | xsd:integer | Captain's priority rank |
| `kb:computeRequirement` | Literal | Compute requirement (e.g., "dgx-training") |

Items are typed as `kb:Expedition`, `kb:Chore`, `kb:Voyage`, `kb:Feature`, etc.

Item URIs follow the pattern `item:EXP-1090` (namespace `https://yurtle.dev/kanban/item/`).

#### SPARQL Examples

```bash
# Items that depend on EXP-1075
yurtle-kanban query --sparql "
  PREFIX kb: <https://yurtle.dev/kanban/>
  PREFIX item: <https://yurtle.dev/kanban/item/>
  SELECT ?id WHERE {
    ?x kb:id ?id .
    ?x kb:dependsOn item:EXP-1075 .
  }
"

# High-priority backlog items tagged 'brain'
yurtle-kanban query --sparql "
  PREFIX kb: <https://yurtle.dev/kanban/>
  SELECT ?id ?title WHERE {
    ?item kb:id ?id .
    ?item kb:title ?title .
    ?item kb:status kb:backlog .
    ?item kb:priority kb:high .
    ?item kb:tag \"brain\" .
  }
"

# Count items by status
yurtle-kanban query --sparql "
  PREFIX kb: <https://yurtle.dev/kanban/>
  SELECT ?status (COUNT(?item) AS ?count) WHERE {
    ?item kb:status ?status .
  }
  GROUP BY ?status
  ORDER BY DESC(?count)
"

# Items with numeric ID between 800 and 1000
yurtle-kanban query --sparql "
  PREFIX kb: <https://yurtle.dev/kanban/>
  SELECT ?id WHERE {
    ?item kb:id ?id .
    ?item kb:numericId ?n .
    FILTER(?n >= 800 && ?n <= 1000)
  }
  ORDER BY ?id
"
```

### 3. Pure Semantic Search

Search by meaning using sentence-transformers embeddings:

```bash
yurtle-kanban query --semantic "knowledge graph reasoning improvements"
yurtle-kanban query --semantic "training and fine-tuning" --top 10
```

Returns items ranked by cosine similarity to the query. Uses `all-MiniLM-L6-v2` by default (~80MB model).

Embeddings are cached in `.yurtle-kanban/embeddings/` with hash-based invalidation — only re-embeds changed items.

## Output Formats

```bash
# Rich table (default)
yurtle-kanban query "in progress items"

# JSON for scripting/piping
yurtle-kanban query "in progress items" --json

# Limit results
yurtle-kanban query "all expeditions" --top 5
```

Every mode, including `--json`, returns at most `--top` results (default 20). When more
matched, a one-line note on stderr says so; stdout stays pure JSON. Raise `--top` to get
them all.

## What fenced blocks can add

Triples from an item's ```` ```yurtle ```` / ```` ```turtle ```` blocks are merged into the
graph, except the single-valued facts the frontmatter owns: `kb:id`, `kb:status`,
`kb:title`, `kb:priority`, `kb:created`, `kb:priorityRank`, `kb:description` and
`kb:numericId` on any IRI. Blocks can add types, tags, assignees, relations and any other
predicate; to describe an external thing, use e.g. `rdfs:label`. Blank nodes (such as the
`kb:statusChange [ kb:status … ]` history `move` records) are kept as written.

## Architecture

```
┌──────────────────────────────────────────────────┐
│              yurtle-kanban query                  │
│                                                  │
│  ┌─────────────┐    ┌────────────────────────┐   │
│  │ NL Decomposer│──>│ Structured constraints  │   │
│  │ (rule-based) │    │ + Semantic intent       │   │
│  └─────────────┘    └───────────┬────────────┘   │
│                                 │                │
│         ┌───────────────────────┤                │
│         │                       │                │
│         v                       v                │
│  ┌─────────────┐    ┌──────────────────┐         │
│  │ Unified RDF  │    │ Embedding Index  │         │
│  │ Graph        │    │ (sentence-tfmrs) │         │
│  │ → SPARQL     │    │ → cosine sim     │         │
│  └──────┬──────┘    └────────┬─────────┘         │
│         │                    │                   │
│         └──────────┬─────────┘                   │
│                    v                             │
│         ┌──────────────────┐                     │
│         │ Hybrid Ranker    │                     │
│         │ graph ∩ semantic  │                     │
│         └──────────────────┘                     │
└──────────────────────────────────────────────────┘
```

The **Unified Graph** merges all per-file `WorkItem.graph` instances (from yurtle/turtle fenced blocks) with materialized frontmatter triples into a single rdflib `Graph`. This means SPARQL can query both structured metadata AND any custom RDF triples in your work item files.

## Python API

```python
from yurtle_kanban.query import QueryEngine, UnifiedGraph, EmbeddingIndex, NLDecomposer
from yurtle_kanban.service import KanbanService
from yurtle_kanban.config import KanbanConfig

# Build from service
config = KanbanConfig.load(".kanban/config.yaml")
service = KanbanService(config, repo_root=Path("."))
engine = QueryEngine.from_service(service)

# Hybrid NL query
results = engine.query("not-done expeditions that improve brain functioning")
for r in results:
    print(f"{r.item.id}: {r.item.title} (score: {r.combined_score:.3f})")

# Direct SPARQL
ug = UnifiedGraph.from_service(service)
rows = ug.sparql(
    "PREFIX kb: <https://yurtle.dev/kanban/> "
    "SELECT ?id WHERE { ?item kb:id ?id . FILTER(isIRI(?item)) }"
)

# Pure semantic search
emb = EmbeddingIndex.from_service(service)
hits = emb.search("knowledge graph", top_k=5)

# NL decomposition (for inspection/debugging)
decomposer = NLDecomposer()
parsed = decomposer.parse("not-done expeditions above 700 tagged brain")
print(parsed.status_filter)   # ['done']
print(parsed.type_filter)     # ['expedition']
print(parsed.id_min)          # 700
print(parsed.tag)             # 'brain'
print(parsed.semantic_query)  # ''
```
