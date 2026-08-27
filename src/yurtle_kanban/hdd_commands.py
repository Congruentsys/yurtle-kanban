"""
HDD (Hypothesis-Driven Development) CLI commands for yurtle-kanban.

Provides Click subgroups for creating HDD research items:
- idea: Research or feature ideas
- literature: Literature reviews
- paper: Research papers
- hypothesis: Testable hypotheses
- experiment: Experiment protocols
- measure: Metric definitions
"""

from __future__ import annotations

import click
from rich.console import Console

from .models import WorkItemType
from .template_engine import TemplateEngine

console = Console()


def _get_service():
    """Get the kanban service for the current directory."""
    from .cli import get_service

    return get_service()


def _get_engine() -> TemplateEngine:
    """Get the template engine."""
    # Import from cli.py to avoid duplication (lazy import to avoid circular import)
    from .cli import _get_templates_dir

    return TemplateEngine(_get_templates_dir())


def _update_parent(
    service,
    parent_id: str,
    child_type: str,
    child_id: str,
    push: bool,
) -> None:
    """Best-effort update of parent's turtle block with inverse reference.

    Silent on failure — the child creation is the primary operation.
    """
    try:
        updated = service.update_parent_turtle_block(
            parent_id, child_type, child_id, push=push,
        )
        if updated:
            console.print(f"  [dim]Updated {parent_id} with inverse reference[/dim]")
    except Exception as e:
        console.print(f"  [yellow]Warning: could not update {parent_id}: {e}[/yellow]")


# ---------------------------------------------------------------------------
# hdd (top-level group for cross-type operations)
# ---------------------------------------------------------------------------


@click.group()
def hdd():
    """Hypothesis-Driven Development — cross-type operations."""
    pass


@hdd.command("backfill")
@click.option("--dry-run", is_flag=True, help="Preview changes without modifying files")
def hdd_backfill(dry_run):
    """Add missing turtle knowledge blocks to HDD files.

    Scans all HDD items, builds expected RDF triples from frontmatter,
    diffs against the file's existing graph, and inserts a fenced turtle
    block with only the missing triples.
    """
    from rich.table import Table

    service = _get_service()
    results = service.backfill_turtle_blocks(dry_run=dry_run)

    backfilled = [r for r in results if r["action"] in ("backfill", "would_backfill")]
    up_to_date = [r for r in results if r["action"] == "up_to_date"]

    if not backfilled and not up_to_date:
        console.print("[dim]No HDD items found.[/dim]")
        return

    if backfilled:
        table = Table(title="Backfill Results" + (" (dry run)" if dry_run else ""))
        table.add_column("ID", width=16)
        table.add_column("Type", width=12)
        table.add_column("Triples", width=8, justify="right")
        table.add_column("Action")
        for r in backfilled:
            action_text = "would add" if dry_run else "added"
            table.add_row(r["id"], r["type"], str(r["triples_added"]), action_text)
        console.print(table)

    summary_parts = []
    if backfilled:
        verb = "Would backfill" if dry_run else "Backfilled"
        summary_parts.append(f"[bold]{verb}[/bold] {len(backfilled)} files")
    if up_to_date:
        summary_parts.append(f"{len(up_to_date)} already up to date")
    console.print("  ".join(summary_parts))


@hdd.command("registry")
@click.option(
    "--output", "output_path", default=None,
    help="Output file path (default: research/REGISTRY.md)",
)
@click.option("--push", is_flag=True, help="Commit and push the registry file")
def hdd_registry(output_path: str | None, push: bool):
    """Auto-generate a research registry from all HDD items.

    Scans papers, hypotheses, experiments, measures, ideas, and literature,
    then writes a cross-referenced REGISTRY.md file.

    Examples:
        yurtle-kanban hdd registry
        yurtle-kanban hdd registry --output research/REGISTRY.md --push
    """
    import subprocess

    service = _get_service()
    xrefs = service.get_hdd_cross_references()

    lines = [
        "# HDD Research Registry",
        "*Auto-generated — do not edit manually*",
        "",
    ]

    # Papers section
    papers = xrefs["papers"]
    lines.append(f"## Papers ({len(papers)} total)")
    if papers:
        lines.append("")
        lines.append("| ID | Title | Status | Hypotheses |")
        lines.append("|----|-------|--------|------------|")
        for p in papers:
            hyps = ", ".join(p["hypotheses"]) or "—"
            lines.append(f"| {p['id']} | {p['title']} | {p['status']} | {hyps} |")
    else:
        lines.append("No papers found.")
    lines.append("")

    # Hypotheses section
    hypotheses = xrefs["hypotheses"]
    lines.append(f"## Hypotheses ({len(hypotheses)} total)")
    if hypotheses:
        lines.append("")
        lines.append("| ID | Paper | Statement | Target | Status | Experiments |")
        lines.append("|----|-------|-----------|--------|--------|-------------|")
        for h in hypotheses:
            exps = ", ".join(h["experiments"]) or "—"
            target = h.get("target", "") or "—"
            lines.append(
                f"| {h['id']} | {h['paper']} | {h['title']} | {target} | {h['status']} | {exps} |"
            )
    else:
        lines.append("No hypotheses found.")
    lines.append("")

    # Experiments section
    experiments = xrefs["experiments"]
    lines.append(f"## Experiments ({len(experiments)} total)")
    if experiments:
        lines.append("")
        lines.append("| ID | Hypothesis | Status | Runs | Last Outcome |")
        lines.append("|----|------------|--------|------|--------------|")
        for e in experiments:
            outcome = e['last_outcome'] or '—'
            lines.append(
                f"| {e['id']} | {e['hypothesis']} "
                f"| {e['status']} | {e['runs']} | {outcome} |"
            )
    else:
        lines.append("No experiments found.")
    lines.append("")

    # Measures section
    measures = xrefs["measures"]
    lines.append(f"## Measures ({len(measures)} total)")
    if measures:
        lines.append("")
        lines.append("| ID | Name | Unit | Category |")
        lines.append("|----|------|------|----------|")
        for m in measures:
            lines.append(f"| {m['id']} | {m['title']} | {m['unit']} | {m['category']} |")
    else:
        lines.append("No measures found.")
    lines.append("")

    # Orphaned section
    orphaned = xrefs["orphaned"]
    if orphaned:
        lines.append(f"## Orphaned Items ({len(orphaned)})")
        lines.append("")
        for o in orphaned:
            lines.append(f"- {o['id']}: {o['reason']}")
        lines.append("")

    # Write registry file
    from pathlib import Path

    if output_path:
        out = Path(output_path)
    else:
        out = service.repo_root / "research" / "REGISTRY.md"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))

    console.print(f"[green]Registry written to {out}[/green]")

    total = (
        len(papers) + len(hypotheses) + len(experiments)
        + len(measures) + len(xrefs["ideas"]) + len(xrefs["literature"])
    )
    console.print(f"  {total} items indexed, {len(orphaned)} orphaned")

    if push:
        try:
            subprocess.run(
                ["git", "add", str(out)],
                cwd=str(service.repo_root),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "hdd: update research registry"],
                cwd=str(service.repo_root),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "push"],
                cwd=str(service.repo_root),
                capture_output=True,
                check=True,
            )
            console.print("  [dim]Committed and pushed[/dim]")
        except subprocess.CalledProcessError as e:
            console.print(f"  [yellow]Warning: git push failed: {e}[/yellow]")


@hdd.command("validate")
@click.option("--strict", is_flag=True, help="Treat warnings as errors (exit 1)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def hdd_validate(strict: bool, as_json: bool):
    """Validate bidirectional links between HDD research items.

    Checks that hypotheses reference papers, experiments reference hypotheses,
    and all cross-references point to existing items.

    Examples:
        yurtle-kanban hdd validate
        yurtle-kanban hdd validate --strict
        yurtle-kanban hdd validate --json
    """
    import json as json_mod
    import sys

    service = _get_service()
    report = service.validate_hdd_links()

    if as_json:
        click.echo(json_mod.dumps(report, indent=2, default=str))
        if report["errors"] or (strict and report["warnings"]):
            sys.exit(1)
        return

    console.print("[bold]HDD Validation Report[/bold]")
    console.print("=" * 40)
    console.print()

    s = report["summary"]

    # Papers
    paper_ok = s["papers"] > 0
    mark = "[green]OK[/green]" if paper_ok else "[dim]—[/dim]"
    console.print(f"  {mark} {s['papers']} papers")

    # Hypotheses
    hyp_warns = [w for w in report["warnings"] if "hypothesis" in w.get("issue", "")]
    hyp_errs = [e for e in report["errors"] if e["id"].startswith("H")]
    if not hyp_warns and not hyp_errs:
        console.print(f"  [green]OK[/green] {s['hypotheses']} hypotheses — all linked to papers")
    else:
        console.print(f"  [yellow]!![/yellow] {s['hypotheses']} hypotheses")
        for w in hyp_warns:
            console.print(f"    [yellow]Warning:[/yellow] {w['id']}: {w['issue']}")
        for e in hyp_errs:
            console.print(f"    [red]Error:[/red] {e['id']}: {e['issue']}")

    # Experiments
    exp_warns = [w for w in report["warnings"] if "experiment" in w.get("issue", "")]
    exp_errs = [e for e in report["errors"] if e["id"].startswith("EXPR")]
    if not exp_warns and not exp_errs:
        msg = f"{s['experiments']} experiments — all linked to hypotheses"
        console.print(f"  [green]OK[/green] {msg}")
    else:
        console.print(f"  [yellow]!![/yellow] {s['experiments']} experiments")
        for w in exp_warns:
            console.print(f"    [yellow]Warning:[/yellow] {w['id']}: {w['issue']}")
        for e in exp_errs:
            console.print(f"    [red]Error:[/red] {e['id']}: {e['issue']}")

    # Measures
    measure_warns = [w for w in report["warnings"] if "measure" in w.get("issue", "")]
    if not measure_warns:
        console.print(f"  [green]OK[/green] {s['measures']} measures — all referenced")
    else:
        referenced = s["measures"] - len(measure_warns)
        unused = len(measure_warns)
        console.print(
            f"  [yellow]!![/yellow] {s['measures']} measures"
            f" — {referenced} referenced, {unused} unused"
        )
        for w in measure_warns:
            console.print(f"    [yellow]Warning:[/yellow] {w['id']}: {w['issue']}")

    console.print()
    console.print(
        f"  Summary: {s['errors']} errors, {s['warnings']} warnings"
    )

    if report["errors"] or (strict and report["warnings"]):
        sys.exit(1)


@hdd.command("critical-path")
@click.option("--agent", default=None, help="Filter to items relevant to this agent")
@click.option(
    "--ready-for-training", "ready_only", is_flag=True,
    help="Only show experiments ready for DGX training",
)
@click.option(
    "--dev-blockers", "dev_blockers_only", is_flag=True,
    help="Only show dev work that blocks research experiments",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def hdd_critical_path(
    agent: str | None,
    ready_only: bool,
    dev_blockers_only: bool,
    as_json: bool,
):
    """Show the cross-board critical path for research experiments.

    Traverses Paper → Hypothesis → Experiment → Expedition (dev board)
    to determine what's ready for training, blocked by dev work, or
    needs analysis. Prioritizes by downstream research impact.

    This is the Bosun's primary tool for research queue scheduling.

    Examples:
        yurtle-kanban hdd critical-path
        yurtle-kanban hdd critical-path --agent DGX
        yurtle-kanban hdd critical-path --ready-for-training
        yurtle-kanban hdd critical-path --dev-blockers
        yurtle-kanban hdd critical-path --json
    """
    import json as json_mod

    service = _get_service()
    results = service.get_critical_path(
        agent=agent,
        ready_only=ready_only,
        dev_blockers_only=dev_blockers_only,
    )

    if as_json:
        click.echo(json_mod.dumps(results, indent=2, default=str))
        return

    if dev_blockers_only:
        _render_dev_blockers(results)
        return

    _render_critical_path(results, agent=agent, ready_only=ready_only)


def _render_dev_blockers(blockers: list[dict]) -> None:
    """Render dev board items that block research experiments."""
    if not blockers:
        console.print("[green]No dev blockers found — all research is unblocked.[/green]")
        return

    console.print("[bold]Dev Work Blocking Research[/bold]")
    console.print("=" * 50)
    console.print()

    for i, b in enumerate(blockers, 1):
        impact_color = "red" if b["impact"] >= 3 else "yellow" if b["impact"] >= 2 else "white"
        console.print(
            f"  {i}. [bold]{b['expedition_id']}[/bold] — {b['title']}"
        )
        console.print(f"     Status: {b['status']}  Assignee: {b['assignee'] or 'unassigned'}")
        console.print(
            f"     [{impact_color}]Unblocks {b['impact']} experiment(s):[/{impact_color}] "
            + ", ".join(b["unblocks_experiments"])
        )
        console.print()


def _render_critical_path(
    experiments: list[dict],
    agent: str | None = None,
    ready_only: bool = False,
) -> None:
    """Render the critical path with Rich formatting."""
    if not experiments:
        if ready_only:
            console.print("[dim]No experiments ready for training.[/dim]")
        elif agent:
            console.print(f"[dim]No experiments found for agent '{agent}'.[/dim]")
        else:
            console.print("[dim]No experiments with dev dependencies found.[/dim]")
        return

    title = "HDD Critical Path"
    if agent:
        title += f" (Agent: {agent})"
    console.print(f"[bold]{title}[/bold]")
    console.print("=" * 50)

    # Group by readiness
    groups: dict[str, list[dict]] = {}
    for exp in experiments:
        groups.setdefault(exp["readiness"], []).append(exp)

    group_labels = {
        "ready_for_training": ("Ready for Training", "green", "DGX GPU slot available"),
        "training_in_progress": ("Training In Progress", "blue", "Currently running"),
        "blocked_by_dev": ("Blocked by Dev Work", "yellow", "Needs expedition first"),
        "needs_analysis": ("Needs Analysis", "cyan", "Training done, results pending"),
        "training_complete": ("Training Complete", "green", "Awaiting hypothesis validation"),
        "no_dev_dependency": ("No Dev Dependency", "dim", "Direct research items"),
    }

    for readiness_key, (label, color, desc) in group_labels.items():
        items = groups.get(readiness_key, [])
        if not items:
            continue

        console.print()
        console.print(f"[bold {color}]{label}[/bold {color}] [dim]({desc})[/dim]")
        console.print()

        for exp in items:
            # Experiment header
            eid = exp['experiment_id']
            console.print(
                f"  [{color}]●[/{color}] [bold]{eid}[/bold]"
                f" — {exp['title']}"
            )

            # Chain: hypothesis → paper
            chain_parts = []
            if exp.get("hypothesis_id"):
                chain_parts.append(exp["hypothesis_id"])
            if exp.get("paper_id"):
                chain_parts.append(exp["paper_id"])
            if chain_parts:
                console.print(f"    Chain: {' → '.join(chain_parts)}")

            # Implements (dev board links)
            if exp.get("implements"):
                impl_strs = []
                for eid in exp["implements"]:
                    status = exp["implements_status"].get(eid, "?")
                    mark = "[green]✓[/green]" if status == "done" else "[red]✗[/red]"
                    impl_strs.append(f"{eid} {mark} ({status})")
                console.print(f"    Implements: {', '.join(impl_strs)}")

            # Runs info
            if exp.get("runs"):
                run_info = f"{exp['runs']} run(s)"
                if exp.get("last_outcome"):
                    run_info += f", last: {exp['last_outcome']}"
                elif exp.get("last_run_status"):
                    run_info += f", last: {exp['last_run_status']}"
                console.print(f"    Runs: {run_info}")

            # Impact
            if exp.get("downstream_impact", 0) > 2:
                console.print(
                    f"    [bold yellow]HIGH IMPACT:[/bold yellow] "
                    f"downstream impact score: {exp['downstream_impact']}"
                )

            # Assignee
            if exp.get("assignee"):
                console.print(f"    Assignee: {exp['assignee']}")

            console.print()


# ---------------------------------------------------------------------------
# idea
# ---------------------------------------------------------------------------


@click.group()
def idea():
    """Manage research/feature ideas (HDD)."""
    pass


@idea.command("create")
@click.argument("title")
@click.option(
    "--type",
    "idea_type",
    default="research",
    type=click.Choice(["research", "feature"]),
    help="Idea type: research (IDEA-R) or feature (IDEA-F)",
)
@click.option("--priority", "-p", default="medium", help="Priority: critical, high, medium, low")
@click.option("--push", is_flag=True, help="Atomic: create, commit, and push")
def idea_create(title: str, idea_type: str, priority: str, push: bool):
    """Create a new research or feature idea.

    Examples:
        yurtle-kanban idea create "Explore transfer learning"
        yurtle-kanban idea create "Dashboard widget" --type feature
        yurtle-kanban idea create "New metric" --push
    """
    service = _get_service()
    engine = _get_engine()

    # Determine prefix based on idea type
    prefix = "IDEA-R" if idea_type == "research" else "IDEA-F"
    next_num = service._get_next_id_number(prefix)
    item_id = f"{prefix}-{next_num:03d}"

    # Render template
    variables = {"id": item_id, "title": title}
    try:
        content = engine.render("hdd", "idea", variables)
    except FileNotFoundError:
        raise click.ClickException("HDD idea template not found")

    if push:
        result = service.create_item_and_push(
            item_type=WorkItemType.IDEA,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )
        if result["success"]:
            pushed = " and pushed" if result.get("pushed") else ""
            console.print(f"[green]Created{pushed} {result['id']}: {title}[/green]")
            console.print(f"  File: {result['item'].file_path}")
        else:
            raise click.ClickException(f"Failed: {result['message']}")
    else:
        item = service.create_item(
            item_type=WorkItemType.IDEA,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )
        console.print(f"[green]Created {item.id}: {title}[/green]")
        console.print(f"  File: {item.file_path}")


# ---------------------------------------------------------------------------
# literature
# ---------------------------------------------------------------------------


@click.group()
def literature():
    """Manage literature reviews (HDD)."""
    pass


@literature.command("create")
@click.argument("title")
@click.option("--idea", "source_idea", default=None, help="Source idea ID (e.g., IDEA-R-001)")
@click.option("--priority", "-p", default="medium", help="Priority")
@click.option("--push", is_flag=True, help="Atomic: create, commit, and push")
def literature_create(title: str, source_idea: str | None, priority: str, push: bool):
    """Create a new literature review.

    Examples:
        yurtle-kanban literature create "Transfer learning survey"
        yurtle-kanban literature create "Metric frameworks" --idea IDEA-R-001
    """
    service = _get_service()
    engine = _get_engine()

    prefix = "LIT"
    next_num = service._get_next_id_number(prefix)
    item_id = f"{prefix}-{next_num:03d}"

    variables: dict[str, str] = {"id": item_id, "title": title}
    if source_idea:
        variables["source_idea"] = source_idea

    try:
        content = engine.render("hdd", "literature", variables)
    except FileNotFoundError:
        raise click.ClickException("HDD literature template not found")

    if push:
        result = service.create_item_and_push(
            item_type=WorkItemType.LITERATURE,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )
        if result["success"]:
            pushed = " and pushed" if result.get("pushed") else ""
            console.print(f"[green]Created{pushed} {result['id']}: {title}[/green]")
            console.print(f"  File: {result['item'].file_path}")
            if source_idea:
                _update_parent(service, source_idea, "literature", result["id"], push=True)
        else:
            raise click.ClickException(f"Failed: {result['message']}")
    else:
        item = service.create_item(
            item_type=WorkItemType.LITERATURE,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )
        console.print(f"[green]Created {item.id}: {title}[/green]")
        console.print(f"  File: {item.file_path}")
        if source_idea:
            _update_parent(service, source_idea, "literature", item.id, push=False)


# ---------------------------------------------------------------------------
# paper
# ---------------------------------------------------------------------------


@click.group()
def paper():
    """Manage research papers (HDD)."""
    pass


@paper.command("create")
@click.argument("number", type=int)
@click.argument("title")
@click.option("--authors", default=None, help="Comma-separated author list")
@click.option("--priority", "-p", default="medium", help="Priority")
@click.option("--push", is_flag=True, help="Atomic: create, commit, and push")
def paper_create(number: int, title: str, authors: str | None, priority: str, push: bool):
    """Create a new research paper.

    NUMBER is the paper number (e.g., 130 for PAPER-130).

    Examples:
        yurtle-kanban paper create 130 "NuSy Brain Architecture"
        yurtle-kanban paper create 131 "Training Methodology" --authors "Alice, Bob"
    """
    service = _get_service()
    engine = _get_engine()

    item_id = f"PAPER-{number}"

    # Check for duplicate
    existing = service.get_item(item_id)
    if existing:
        raise click.ClickException(f"{item_id} already exists: {existing.title}")

    variables: dict[str, str] = {
        "id": item_id,
        "title": title,
        "paper_num": str(number),
    }
    if authors:
        variables["authors"] = authors

    try:
        content = engine.render("hdd", "paper", variables)
    except FileNotFoundError:
        raise click.ClickException("HDD paper template not found")

    if push:
        result = service.create_item_and_push(
            item_type=WorkItemType.PAPER,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )
        if result["success"]:
            pushed = " and pushed" if result.get("pushed") else ""
            console.print(f"[green]Created{pushed} {result['id']}: {title}[/green]")
            console.print(f"  File: {result['item'].file_path}")
        else:
            raise click.ClickException(f"Failed: {result['message']}")
    else:
        item = service.create_item(
            item_type=WorkItemType.PAPER,
            title=title,
            priority=priority,
            content=content,
            item_id=item_id,
        )
        console.print(f"[green]Created {item.id}: {title}[/green]")
        console.print(f"  File: {item.file_path}")


# ---------------------------------------------------------------------------
# hypothesis
# ---------------------------------------------------------------------------


@click.group()
def hypothesis():
    """Manage hypotheses (HDD)."""
    pass


@hypothesis.command("create")
@click.argument("statement")
@click.option(
    "--paper", "paper_num", default=None, type=int,
    help="Paper number (e.g., 130). Optional — omit it and the hypothesis "
         "stands on its own as H-001; pass it to scope the id to a paper.",
)
@click.option(
    "--id", "hyp_id", default=None,
    help="Explicit ID (e.g., H130.1). Auto-allocates if omitted.",
)
@click.option("--target", default=None, help="Target metric value (e.g., '>=50%')")
@click.option("--source-idea", default=None, help="Source idea ID (e.g., IDEA-R-001)")
@click.option(
    "--measures", default=None,
    help="Comma-separated measure IDs (e.g., 'M-007,M-025')",
)
@click.option(
    "--literature", default=None,
    help="Comma-separated literature IDs (e.g., 'LIT-001,LIT-003')",
)
@click.option("--priority", "-p", default="medium", help="Priority")
@click.option("--push", is_flag=True, help="Atomic: create, commit, and push")
def hypothesis_create(
    statement: str,
    paper_num: int | None,
    hyp_id: str | None,
    target: str | None,
    source_idea: str | None,
    measures: str | None,
    literature: str | None,
    priority: str,
    push: bool,
):
    """Create a new hypothesis.

    STATEMENT is the hypothesis text.

    A paper is OPTIONAL. State the hypothesis first; attach it to a paper later
    if it ever needs one.

    Examples:
        yurtle-kanban hypothesis create "Most support tickets come from one feature"
        yurtle-kanban hypothesis create "V12 improves accuracy by 15%" --paper 130
        yurtle-kanban hypothesis create "Faster convergence" --paper 130 --id H130.1
        yurtle-kanban hypothesis create "Better recall" --target ">=85%"
    """
    service = _get_service()
    engine = _get_engine()

    # Auto-allocate an id if none was given.
    #
    # --paper used to be required, which meant the FIRST thing a new user tried
    # failed: you had to invent a research paper before you were allowed to
    # state a belief. That inverted the method — the formal artifact demanded
    # before the informal thing it describes (issue #77).
    #
    # The requirement was never about the method; it was about ID ALLOCATION,
    # because ids are formatted H{paper}.{n}. So an unparented hypothesis simply
    # takes the ordinary H-NNN form every other work type already uses. The two
    # spaces cannot collide — `H130.1` has a dot and no dash, `H-001` the
    # reverse — so paper-scoped numbering is untouched.
    if hyp_id is None:
        if paper_num is None:
            hyp_id = service.get_next_unparented_hypothesis_id()
            hyp_n = None
        else:
            next_n = service.get_next_hypothesis_number(str(paper_num))
            hyp_id = f"H{paper_num}.{next_n}"
            hyp_n = str(next_n)
    else:
        # Extract n from user-provided ID (e.g., H130.1 → "1")
        if "." in hyp_id:
            hyp_n = hyp_id.split(".")[-1]
        else:
            hyp_n = None if paper_num is None else "1"

    # Check for duplicate
    existing = service.get_item(hyp_id)
    if existing:
        raise click.ClickException(f"{hyp_id} already exists: {existing.title}")

    variables: dict[str, str | list[str]] = {
        "id": hyp_id,
        "title": statement,
    }
    # Only pass paper/n when there IS a paper. The template engine keys off
    # their presence, and passing "None" as a string would render the literal
    # word into the frontmatter and the turtle block.
    if paper_num is not None:
        variables["paper"] = str(paper_num)
    if hyp_n is not None:
        variables["n"] = hyp_n
    if target:
        variables["target"] = target
    if source_idea:
        variables["source_idea"] = source_idea
    if measures:
        variables["measures"] = [m.strip() for m in measures.split(",")]
    if literature:
        variables["literature"] = [lit.strip() for lit in literature.split(",")]

    try:
        content = engine.render("hdd", "hypothesis", variables)
    except FileNotFoundError:
        raise click.ClickException("HDD hypothesis template not found")

    if push:
        result = service.create_item_and_push(
            item_type=WorkItemType.HYPOTHESIS,
            title=statement,
            priority=priority,
            content=content,
            item_id=hyp_id,
        )
        if result["success"]:
            pushed = " and pushed" if result.get("pushed") else ""
            console.print(f"[green]Created{pushed} {result['id']}: {statement}[/green]")
            console.print(f"  File: {result['item'].file_path}")
            # No paper -> no parent to back-reference. Guarding here rather than
            # inside _update_parent keeps the 'PAPER-None' string from ever existing.
            if paper_num is not None:
                _update_parent(service, f"PAPER-{paper_num}", "hypothesis", result["id"], push=True)
        else:
            raise click.ClickException(f"Failed: {result['message']}")
    else:
        item = service.create_item(
            item_type=WorkItemType.HYPOTHESIS,
            title=statement,
            priority=priority,
            content=content,
            item_id=hyp_id,
        )
        console.print(f"[green]Created {item.id}: {statement}[/green]")
        console.print(f"  File: {item.file_path}")
        if paper_num is not None:
            _update_parent(service, f"PAPER-{paper_num}", "hypothesis", item.id, push=False)


# ---------------------------------------------------------------------------
# experiment
# ---------------------------------------------------------------------------


@click.group()
def experiment():
    """Manage experiments (HDD)."""
    pass


@experiment.command("create")
@click.argument("expr_id", required=False)
@click.option(
    "--hypothesis", "hyp_id", default=None,
    help="Hypothesis ID (e.g., H130.1 or H-001). Optional — an experiment can "
         "stand on its own and be attached to a hypothesis later.",
)
@click.option("--title", required=True, help="Experiment title")
@click.option("--measures", default=None, help="Comma-separated measure IDs (e.g., 'M-007,M-025')")
@click.option("--priority", "-p", default="medium", help="Priority")
@click.option("--push", is_flag=True, help="Atomic: create, commit, and push")
def experiment_create(
    expr_id: str | None, hyp_id: str | None, title: str,
    measures: str | None, priority: str, push: bool,
):
    """Create a new experiment.

    EXPR_ID is optional — omit it and the next EXPR-NNN is allocated.
    A hypothesis is optional too; attach one later if the experiment needs it.

    Examples:
        yurtle-kanban experiment create --title "Does the subject line matter?"
        yurtle-kanban experiment create --hypothesis H-001 --title "First probe"
        yurtle-kanban experiment create EXPR-130 --hypothesis H130.1 --title "V12 accuracy test"
    """
    service = _get_service()
    engine = _get_engine()

    # Allocate an id when none was given, the same way every other type does.
    # Requiring the user to invent EXPR-130 is the same "produce the artifact
    # before the thing it describes" problem `--paper` had on hypotheses.
    if expr_id is None:
        expr_id = f"EXPR-{service._get_next_id_number('EXPR'):03d}"
    elif not expr_id.startswith("EXPR-"):
        expr_id = f"EXPR-{expr_id}"

    # Check for duplicate
    existing = service.get_item(expr_id)
    if existing:
        raise click.ClickException(f"{expr_id} already exists: {existing.title}")

    # The paper comes from the HYPOTHESIS, when there is one that names a paper.
    #
    # This previously read `paper_num = expr_id.replace("EXPR-", "")` — deriving
    # the paper from the EXPERIMENT'S OWN id, so `EXPR-001` produced
    # `paper: PAPER-001` regardless of which paper the hypothesis belonged to.
    # That was wrong whenever the two numbers differed, and meaningless for an
    # auto-allocated id. Paper-scoped hypotheses are `H{paper}.{n}`, so the
    # paper is the part before the dot; an unparented `H-001` names none.
    paper_num: str | None = None
    hyp_n: str | None = None
    if hyp_id and "." in hyp_id:
        head, _, tail = hyp_id.partition(".")
        paper_num = head.lstrip("H") or None
        hyp_n = tail or None

    variables: dict[str, str | list[str]] = {
        "id": expr_id,
        "title": title,
    }
    if paper_num is not None:
        variables["paper"] = paper_num
    if hyp_n is not None:
        variables["n"] = hyp_n
    if hyp_id:
        variables["hypothesis_id"] = hyp_id
    if measures:
        variables["measures"] = [m.strip() for m in measures.split(",")]

    try:
        content = engine.render("hdd", "experiment", variables)
    except FileNotFoundError:
        raise click.ClickException("HDD experiment template not found")

    if push:
        result = service.create_item_and_push(
            item_type=WorkItemType.EXPERIMENT,
            title=title,
            priority=priority,
            content=content,
            item_id=expr_id,
        )
        if result["success"]:
            pushed = " and pushed" if result.get("pushed") else ""
            console.print(f"[green]Created{pushed} {result['id']}: {title}[/green]")
            console.print(f"  File: {result['item'].file_path}")
            # No hypothesis -> no parent to back-reference.
            if hyp_id:
                _update_parent(service, hyp_id, "experiment", result["id"], push=True)
        else:
            raise click.ClickException(f"Failed: {result['message']}")
    else:
        item = service.create_item(
            item_type=WorkItemType.EXPERIMENT,
            title=title,
            priority=priority,
            content=content,
            item_id=expr_id,
        )
        console.print(f"[green]Created {item.id}: {title}[/green]")
        console.print(f"  File: {item.file_path}")
        if hyp_id:
            _update_parent(service, hyp_id, "experiment", item.id, push=False)


@experiment.command("run")
@click.argument("expr_id")
@click.option("--being", required=True, help="Being name/version (e.g., santiago-toddler-v12.4)")
@click.option(
    "--params", "params_str", default=None,
    help="Comma-separated key=value pairs (e.g., 'kbdd_rounds=3,wikidata=true')",
)
@click.option("--run-by", default=None, help="Who started the run (default: git user.name)")
@click.option("--push", is_flag=True, help="Atomic: commit and push the config.yaml")
def experiment_run(
    expr_id: str, being: str, params_str: str | None,
    run_by: str | None, push: bool,
):
    """Create a new experiment run with a timestamped folder.

    Creates research/runs/EXPR-ID/TIMESTAMP/config.yaml with run metadata.
    Scripts and training can write metrics.json to the returned folder.

    Examples:
        yurtle-kanban experiment run EXPR-130 --being santiago-toddler-v12.4
        yurtle-kanban experiment run EXPR-130 --being v12.4 --params "kbdd_rounds=3,wikidata=true"
    """
    service = _get_service()

    # Normalize ID
    if not expr_id.startswith("EXPR-"):
        expr_id = f"EXPR-{expr_id}"

    # Parse params
    params: dict[str, str] | None = None
    if params_str:
        params = {}
        for pair in params_str.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k.strip()] = v.strip()

    run_path = service.create_experiment_run(
        expr_id=expr_id,
        being=being,
        params=params,
        run_by=run_by,
    )

    console.print(f"[green]Created run for {expr_id}[/green]")
    console.print(f"  Path: {run_path}")

    if push:
        import subprocess

        try:
            config_path = run_path / "config.yaml"
            subprocess.run(
                ["git", "add", str(config_path)],
                cwd=str(service.repo_root),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", f"experiment run: {expr_id} ({run_path.name})"],
                cwd=str(service.repo_root),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "push"],
                cwd=str(service.repo_root),
                capture_output=True,
                check=True,
            )
            console.print("  [dim]Committed and pushed[/dim]")
        except subprocess.CalledProcessError as e:
            console.print(f"  [yellow]Warning: git push failed: {e}[/yellow]")


@experiment.command("status")
@click.argument("expr_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON for scripting")
def experiment_status(expr_id: str, as_json: bool):
    """Show all runs for an experiment.

    Displays a table of runs with being, status, and outcome.

    Examples:
        yurtle-kanban experiment status EXPR-130
        yurtle-kanban experiment status EXPR-130 --json
    """
    import json as json_mod

    service = _get_service()

    # Normalize ID
    if not expr_id.startswith("EXPR-"):
        expr_id = f"EXPR-{expr_id}"

    runs = service.get_experiment_runs(expr_id)

    if as_json:
        # Serialize Path objects to strings
        for r in runs:
            r["run_path"] = str(r["run_path"])
        click.echo(json_mod.dumps(runs, indent=2, default=str))
        return

    # Look up experiment title
    item = service.get_item(expr_id)
    title = item.title if item else expr_id

    if not runs:
        console.print(f"[dim]{expr_id}: {title}[/dim]")
        console.print("  No runs found.")
        return

    from rich.table import Table

    table = Table(title=f"{expr_id}: {title}")
    table.add_column("Run", width=22)
    table.add_column("Being", width=28)
    table.add_column("Status", width=12)
    table.add_column("Outcome")

    for run in runs:
        outcome = run.get("outcome", run.get("summary", ""))
        status_style = {
            "running": "yellow",
            "complete": "green",
            "failed": "red",
        }.get(run["status"], "dim")
        table.add_row(
            run["timestamp"][:19],
            run["being"],
            f"[{status_style}]{run['status']}[/{status_style}]",
            outcome or "",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# measure
# ---------------------------------------------------------------------------


@click.group()
def measure():
    """Manage metric definitions (HDD)."""
    pass


@measure.command("create")
@click.argument("title")
@click.option("--unit", required=True, help="Unit of measurement (e.g., 'percent', 'count', 'ms')")
@click.option(
    "--category", required=True,
    help="Category (e.g., 'accuracy', 'performance', 'coverage')",
)
@click.option(
    "--id", "measure_id", default=None,
    help="Explicit ID (e.g., M-042). Auto-allocates if omitted.",
)
@click.option("--priority", "-p", default="medium", help="Priority")
@click.option("--push", is_flag=True, help="Atomic: create, commit, and push")
def measure_create(
    title: str,
    unit: str,
    category: str,
    measure_id: str | None,
    priority: str,
    push: bool,
):
    """Create a new measure definition.

    Examples:
        yurtle-kanban measure create "Reasoning Accuracy" --unit percent --category accuracy
        yurtle-kanban measure create "Response Latency" --unit ms --category performance --id M-042
    """
    service = _get_service()
    engine = _get_engine()

    # Auto-allocate or use provided
    if measure_id is None:
        prefix = "M"
        next_num = service._get_next_id_number(prefix)
        measure_id = f"M-{next_num:03d}"

    # Check for duplicate
    existing = service.get_item(measure_id)
    if existing:
        raise click.ClickException(f"{measure_id} already exists: {existing.title}")

    variables: dict[str, str] = {
        "id": measure_id,
        "title": title,
        "unit": unit,
        "category": category,
    }

    try:
        content = engine.render("hdd", "measure", variables)
    except FileNotFoundError:
        raise click.ClickException("HDD measure template not found")

    if push:
        result = service.create_item_and_push(
            item_type=WorkItemType.MEASURE,
            title=title,
            priority=priority,
            content=content,
            item_id=measure_id,
        )
        if result["success"]:
            pushed = " and pushed" if result.get("pushed") else ""
            console.print(f"[green]Created{pushed} {result['id']}: {title}[/green]")
            console.print(f"  File: {result['item'].file_path}")
        else:
            raise click.ClickException(f"Failed: {result['message']}")
    else:
        item = service.create_item(
            item_type=WorkItemType.MEASURE,
            title=title,
            priority=priority,
            content=content,
            item_id=measure_id,
        )
        console.print(f"[green]Created {item.id}: {title}[/green]")
        console.print(f"  File: {item.file_path}")
