"""
Research interlink display for campaign/voyage show.

Queries WorkItem.graph (populated by yurtle-rdflib v0.2.0+ from both
YAML frontmatter and fenced ```turtle blocks) for HDD research
relationships: hypothesis→paper, experiment→hypothesis, measure metadata.

Only renders when linked items include HDD types with knowledge graphs.
"""

from __future__ import annotations

from rdflib import RDFS, Namespace
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .models import WorkItem, WorkItemStatus, WorkItemType
from .turtle_builder import PREFIXES

# Derive namespaces from turtle_builder.PREFIXES (single source of truth)
HYP = Namespace(PREFIXES["hyp"])
PAPER = Namespace(PREFIXES["paper"])
EXPR = Namespace(PREFIXES["expr"])
MEASURE = Namespace(PREFIXES["measure"])
LIT = Namespace(PREFIXES["lit"])
IDEA = Namespace(PREFIXES["idea"])

# HDD item types that carry research metadata
HDD_TYPES = frozenset({
    WorkItemType.PAPER,
    WorkItemType.HYPOTHESIS,
    WorkItemType.EXPERIMENT,
    WorkItemType.MEASURE,
    WorkItemType.LITERATURE,
    WorkItemType.IDEA,
})

_STATUS_COLORS = {
    WorkItemStatus.DONE: "green",
    WorkItemStatus.IN_PROGRESS: "cyan",
    WorkItemStatus.REVIEW: "yellow",
    WorkItemStatus.BLOCKED: "red",
    WorkItemStatus.BACKLOG: "dim",
    WorkItemStatus.READY: "blue",
}


def _obj_id(uri_str: str) -> str:
    """Extract display ID from an RDF URI.

    Turtle blocks use ``<#H130.1>`` (fragment) or ``paper:PAPER-130``
    (prefixed name resolved to full URI). Extract the meaningful part.
    """
    s = str(uri_str)
    if "#" in s:
        return s.rsplit("#", 1)[-1]
    for prefix_uri in PREFIXES.values():
        if s.startswith(prefix_uri):
            return s[len(prefix_uri):]
    return s.rsplit("/", 1)[-1] if "/" in s else s


def _first_triple(item: WorkItem, predicate) -> str | None:
    """Get the first object value for a predicate from the item's graph.

    Uses min() for deterministic results — rdflib's graph.triples()
    iterates over an internal set with no guaranteed order.
    """
    values = item.get_knowledge_triples(predicate)
    return min(values) if values else None


def has_research_items(items: list[WorkItem]) -> bool:
    """Check if any items are HDD types with populated graphs."""
    return any(
        item.item_type in HDD_TYPES and item.graph is not None
        for item in items
    )


def render_research_interlinks(items: list[WorkItem], console: Console) -> None:
    """Render research interlinks section for HDD items.

    Queries each item's RDF graph for research predicates and displays
    tables grouped by type. No output if no HDD items with graphs exist.
    """
    hypotheses: list[WorkItem] = []
    experiments: list[WorkItem] = []
    papers: list[WorkItem] = []
    measures: list[WorkItem] = []
    literature: list[WorkItem] = []

    for item in items:
        if item.item_type not in HDD_TYPES or item.graph is None:
            continue
        match item.item_type:
            case WorkItemType.HYPOTHESIS:
                hypotheses.append(item)
            case WorkItemType.EXPERIMENT:
                experiments.append(item)
            case WorkItemType.PAPER:
                papers.append(item)
            case WorkItemType.MEASURE:
                measures.append(item)
            case WorkItemType.LITERATURE:
                literature.append(item)

    if not any([hypotheses, experiments, papers, measures, literature]):
        return

    console.print("\n  [bold]Research Interlinks[/bold]")
    console.print("  " + "\u2501" * 40)

    # Papers
    if papers:
        console.print("\n  [bold dim]Papers:[/bold dim]")
        for p in papers:
            color = _STATUS_COLORS.get(p.status, "white")
            label = _first_triple(p, RDFS.label) or p.title
            console.print(
                f"    [bold]{escape(p.id)}[/bold]  {escape(str(label)[:50])}  "
                f"[{color}]({p.status.value})[/{color}]"
            )

    # Hypotheses
    if hypotheses:
        console.print("\n  [bold dim]Hypotheses:[/bold dim]")
        table = Table(show_header=True, header_style="bold dim", padding=(0, 1))
        table.add_column("ID", width=12)
        table.add_column("Label", min_width=25)
        table.add_column("Paper", width=14)
        table.add_column("Target", width=14)
        table.add_column("Status", width=12)

        for h in hypotheses:
            paper_uri = _first_triple(h, HYP.paper)
            paper_display = _obj_id(paper_uri) if paper_uri else "-"
            target = _first_triple(h, HYP.target) or "-"
            label = _first_triple(h, RDFS.label) or h.title
            color = _STATUS_COLORS.get(h.status, "white")
            table.add_row(
                escape(h.id),
                escape(str(label)[:35]),
                escape(str(paper_display)),
                escape(str(target)[:14]),
                f"[{color}]{h.status.value}[/{color}]",
            )
        console.print(table)

    # Experiments
    if experiments:
        console.print("\n  [bold dim]Experiments:[/bold dim]")
        table = Table(show_header=True, header_style="bold dim", padding=(0, 1))
        table.add_column("ID", width=14)
        table.add_column("Label", min_width=25)
        table.add_column("Hypothesis", width=12)
        table.add_column("Paper", width=14)
        table.add_column("Status", width=12)

        for e in experiments:
            hyp_uri = _first_triple(e, EXPR.hypothesis)
            hyp_display = _obj_id(hyp_uri) if hyp_uri else "-"
            paper_uri = _first_triple(e, EXPR.paper)
            paper_display = _obj_id(paper_uri) if paper_uri else "-"
            label = _first_triple(e, RDFS.label) or e.title
            color = _STATUS_COLORS.get(e.status, "white")
            table.add_row(
                escape(e.id),
                escape(str(label)[:35]),
                escape(str(hyp_display)),
                escape(str(paper_display)),
                f"[{color}]{e.status.value}[/{color}]",
            )
        console.print(table)

    # Measures
    if measures:
        console.print("\n  [bold dim]Measures:[/bold dim]")
        table = Table(show_header=True, header_style="bold dim", padding=(0, 1))
        table.add_column("ID", width=8)
        table.add_column("Label", min_width=25)
        table.add_column("Unit", width=12)
        table.add_column("Category", width=14)

        for m in measures:
            label = _first_triple(m, RDFS.label) or m.title
            unit = _first_triple(m, MEASURE.unit) or "-"
            category = _first_triple(m, MEASURE.category) or "-"
            table.add_row(
                escape(m.id), escape(str(label)[:30]), escape(str(unit)), escape(str(category))
            )
        console.print(table)

    # Literature
    if literature:
        console.print("\n  [bold dim]Literature:[/bold dim]")
        for lit in literature:
            color = _STATUS_COLORS.get(lit.status, "white")
            label = _first_triple(lit, RDFS.label) or lit.title
            explores_uri = _first_triple(lit, LIT.explores)
            explores = f" \u2192 {escape(str(_obj_id(explores_uri)))}" if explores_uri else ""
            console.print(
                f"    [bold]{escape(lit.id)}[/bold]  {escape(str(label)[:40])}{explores}  "
                f"[{color}]({lit.status.value})[/{color}]"
            )
