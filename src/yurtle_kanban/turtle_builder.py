"""
Build Turtle knowledge blocks for HDD item templates.

Generates fenced ```turtle blocks from item type and CLI variables.
Relationships between items are expressed as RDF triples, not YAML frontmatter.

Principle: frontmatter describes the thing itself.
           Knowledge blocks define relationships to other items.
"""

from __future__ import annotations

import re

from .models import turtle_string

_SAFE_LOCAL_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class InvalidTurtleName(ValueError):  # noqa: N818 — the name #183 specifies
    """A value refused as a Turtle local name. Its own type, so a CLI can turn
    exactly this into a clean error and let any other ValueError surface (#183)."""


def _escape_turtle_string(value: str) -> str:
    """Escape a value for a Turtle "..." literal (the shared escaper, #141)."""
    return turtle_string(value)


def _validate_turtle_local_name(value: str) -> str:
    """Validate a value used in a Turtle prefixed name (prefix:localName).

    Rejects characters that could break TTL syntax or inject triples.
    Only allows alphanumeric, dot, hyphen, and underscore.

    Raises InvalidTurtleName (a ValueError) if the value contains disallowed characters.
    """
    # fullmatch: `$` in `.match` would also accept a trailing newline (`H1\n`)
    if not _SAFE_LOCAL_NAME.fullmatch(value):
        # the value as typed when printable (`H1\b`), else its repr, so control
        # characters (ESC, CR, newlines) never reach the terminal raw (#161)
        shown = f'"{value}"' if value.isprintable() else repr(value)
        raise InvalidTurtleName(
            f"Invalid Turtle local name: {shown} — only [A-Za-z0-9._-] are allowed"
        )
    return value


# Standard HDD prefixes used across research items.
PREFIXES: dict[str, str] = {
    "hyp": "https://nusy.dev/hypothesis/",
    "paper": "https://nusy.dev/paper/",
    "expr": "https://nusy.dev/experiment/",
    "measure": "https://nusy.dev/measure/",
    "idea": "https://nusy.dev/idea/",
    "lit": "https://nusy.dev/literature/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

# Dispatch table: item_type -> builder function name
_BUILDERS: dict[str, str] = {
    "idea": "_build_idea",
    "literature": "_build_literature",
    "paper": "_build_paper",
    "hypothesis": "_build_hypothesis",
    "experiment": "_build_experiment",
    "measure": "_build_measure",
}


class TurtleBlockBuilder:
    """Build Turtle knowledge blocks for HDD items.

    Given an item type and a variables dict (from CLI flags), generates
    a fenced ```turtle block with RDF triples expressing the item's
    relationships to other items.
    """

    def build(self, item_type: str, variables: dict[str, str | list[str]]) -> str:
        """Build a complete fenced turtle block.

        Args:
            item_type: HDD item type (idea, literature, paper, hypothesis,
                       experiment, measure).
            variables: Dict of CLI variable names to values. Lists are used
                       for multi-value relationships (e.g., measures).

        Returns:
            A fenced ```turtle ... ``` block string, or empty string if
            item_type is not an HDD type.
        """
        builder_name = _BUILDERS.get(item_type)
        if builder_name is None:
            return ""
        builder_fn = getattr(self, builder_name)
        prefixes_used: set[str] = set()
        triples = builder_fn(variables, prefixes_used)
        if not triples:
            return ""
        return self._format_block(prefixes_used, triples)

    # ------------------------------------------------------------------
    # Per-type builders
    # ------------------------------------------------------------------

    def _build_idea(
        self,
        variables: dict[str, str | list[str]],
        prefixes: set[str],
    ) -> list[str]:
        prefixes.update(["idea", "rdfs"])
        item_id = _validate_turtle_local_name(variables.get("id", "IDEA-R-XXX"))
        title = _escape_turtle_string(variables.get("title", ""))
        return [
            f"<#{item_id}> a idea:Idea ;",
            f'    rdfs:label "{title}" .',
        ]

    def _build_literature(
        self,
        variables: dict[str, str | list[str]],
        prefixes: set[str],
    ) -> list[str]:
        prefixes.update(["lit", "rdfs"])
        item_id = _validate_turtle_local_name(variables.get("id", "LIT-XXX"))
        title = _escape_turtle_string(variables.get("title", ""))
        lines = [
            f"<#{item_id}> a lit:Literature ;",
            f'    rdfs:label "{title}"',
        ]
        source_idea = variables.get("source_idea")
        if source_idea:
            prefixes.add("idea")
            _validate_turtle_local_name(source_idea)
            lines[-1] += " ;"
            lines.append(f"    lit:explores idea:{source_idea}")
        lines[-1] += " ."
        return lines

    def _build_paper(
        self,
        variables: dict[str, str | list[str]],
        prefixes: set[str],
    ) -> list[str]:
        prefixes.update(["paper", "rdfs"])
        item_id = _validate_turtle_local_name(variables.get("id", "PAPER-XXX"))
        title = _escape_turtle_string(variables.get("title", ""))
        return [
            f"<#{item_id}> a paper:Paper ;",
            f'    rdfs:label "{title}" .',
        ]

    def _build_hypothesis(
        self,
        variables: dict[str, str | list[str]],
        prefixes: set[str],
    ) -> list[str]:
        prefixes.update(["hyp", "rdfs"])
        item_id = _validate_turtle_local_name(variables.get("id", "H{paper}.{n}"))
        title = _escape_turtle_string(variables.get("title", ""))
        paper = variables.get("paper")
        lines = [
            f"<#{item_id}> a hyp:Hypothesis ;",
            f'    rdfs:label "{title}"',
        ]
        if paper:
            prefixes.add("paper")
            _validate_turtle_local_name(str(paper))
            lines[-1] += " ;"
            lines.append(f"    hyp:paper paper:PAPER-{paper}")
        target = variables.get("target")
        if target:
            lines[-1] += " ;"
            lines.append(f'    hyp:target "{_escape_turtle_string(target)}"')
        source_idea = variables.get("source_idea")
        if source_idea:
            prefixes.add("idea")
            _validate_turtle_local_name(source_idea)
            lines[-1] += " ;"
            lines.append(f"    hyp:sourceIdea idea:{source_idea}")
        literature = variables.get("literature")
        if literature:
            prefixes.add("lit")
            refs = _format_uri_list("lit", literature)
            lines[-1] += " ;"
            lines.append(f"    hyp:informedBy {refs}")
        measures = variables.get("measures")
        if measures:
            prefixes.add("measure")
            refs = _format_uri_list("measure", measures)
            lines[-1] += " ;"
            lines.append(f"    hyp:measuredBy {refs}")
        lines[-1] += " ."
        return lines

    def _build_experiment(
        self,
        variables: dict[str, str | list[str]],
        prefixes: set[str],
    ) -> list[str]:
        prefixes.update(["expr", "rdfs"])
        item_id = _validate_turtle_local_name(variables.get("id", "EXPR-XXX"))
        title = _escape_turtle_string(variables.get("title", ""))
        paper = variables.get("paper")
        lines = [
            f"<#{item_id}> a expr:Experiment ;",
            f'    rdfs:label "{title}"',
        ]
        if paper:
            prefixes.add("paper")
            _validate_turtle_local_name(str(paper))
            lines[-1] += " ;"
            lines.append(f"    expr:paper paper:PAPER-{paper}")
        hypothesis_id = variables.get("hypothesis_id")
        if hypothesis_id:
            prefixes.add("hyp")
            _validate_turtle_local_name(hypothesis_id)
            lines[-1] += " ;"
            lines.append(f"    expr:hypothesis hyp:{hypothesis_id}")
        measures = variables.get("measures")
        if measures:
            prefixes.add("measure")
            refs = _format_uri_list("measure", measures)
            lines[-1] += " ;"
            lines.append(f"    expr:measure {refs}")
        lines[-1] += " ."
        return lines

    def _build_measure(
        self,
        variables: dict[str, str | list[str]],
        prefixes: set[str],
    ) -> list[str]:
        prefixes.update(["measure", "rdfs"])
        item_id = _validate_turtle_local_name(variables.get("id", "M-XXX"))
        title = _escape_turtle_string(variables.get("title", ""))
        lines = [
            f"<#{item_id}> a measure:Measure ;",
            f'    rdfs:label "{title}"',
        ]
        unit = variables.get("unit")
        if unit:
            lines[-1] += " ;"
            lines.append(f'    measure:unit "{_escape_turtle_string(unit)}"')
        category = variables.get("category")
        if category:
            lines[-1] += " ;"
            lines.append(f'    measure:category "{_escape_turtle_string(category)}"')
        lines[-1] += " ."
        return lines

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_block(self, prefixes: set[str], triples: list[str]) -> str:
        """Wrap prefix declarations and triples in a fenced turtle block."""
        prefix_lines = []
        for name in sorted(prefixes):
            uri = PREFIXES.get(name, "")
            if uri:
                prefix_lines.append(f"@prefix {name}: <{uri}> .")
        parts = ["```turtle"]
        parts.extend(prefix_lines)
        parts.append("")
        parts.extend(triples)
        parts.append("```")
        return "\n".join(parts)


def _format_uri_list(prefix: str, values: str | list[str]) -> str:
    """Format a value or list of values as comma-separated prefixed URIs.

    >>> _format_uri_list("measure", ["M-007", "M-025"])
    'measure:M-007, measure:M-025'
    >>> _format_uri_list("lit", "LIT-001")
    'lit:LIT-001'
    """
    if isinstance(values, str):
        values = [values]
    for v in values:
        _validate_turtle_local_name(v)
    return ", ".join(f"{prefix}:{v}" for v in values)
