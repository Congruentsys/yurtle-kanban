"""
Template engine for rendering HDD (and other theme) item templates.

Loads markdown templates from the templates/ directory and substitutes
variables to produce ready-to-write file content.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from yurtle_kanban.turtle_builder import TurtleBlockBuilder

# HDD item types that get Turtle knowledge blocks generated.
_HDD_TURTLE_TYPES = {"idea", "literature", "paper", "hypothesis", "experiment", "measure"}


class TemplateEngine:
    """Load and render themed item templates with variable substitution."""

    def __init__(self, templates_dir: Path):
        self.templates_dir = templates_dir
        self._turtle_builder = TurtleBlockBuilder()

    def render(self, theme: str, item_type: str, variables: dict[str, str | list[str]]) -> str:
        """Load template and substitute variables.

        Args:
            theme: Theme name (e.g., "hdd", "software")
            item_type: Item type (e.g., "hypothesis", "paper")
            variables: Dict of placeholder → value substitutions.
                       Common keys: id, title, paper, n, source_idea, unit, category

        Returns:
            Full markdown file content with variables substituted.

        Raises:
            FileNotFoundError: If no template exists for this theme/type.
        """
        template_path = self._get_template_path(theme, item_type)
        if template_path is None:
            raise FileNotFoundError(
                f"No template found for theme='{theme}', type='{item_type}' "
                f"(searched {self.templates_dir})"
            )

        content = template_path.read_text()

        # Always substitute today's date
        variables.setdefault("date", date.today().isoformat())

        # Substitute YYYY-MM-DD with actual date
        content = content.replace("YYYY-MM-DD", variables["date"])

        # Substitute frontmatter id field
        if "id" in variables:
            # Replace the id line in frontmatter
            # (handles patterns like IDEA-R-XXX, H{paper}.{n}, etc.)
            content = re.sub(
                r"^(id:\s*).+$",
                rf"\g<1>{variables['id']}",
                content,
                count=1,
                flags=re.MULTILINE,
            )

        # Substitute title in frontmatter and heading
        if "title" in variables:
            title = variables["title"]
            content = re.sub(
                r'^(title:\s*)".*"',
                rf'\g<1>"{title}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )
            # Also replace the first H1 heading with the title
            content = re.sub(
                r"^(# ).+$",
                rf"\g<1>{title}",
                content,
                count=1,
                flags=re.MULTILINE,
            )

        # Substitute paper reference in frontmatter
        if "paper" in variables:
            paper_val = variables["paper"]
            content = re.sub(
                r"^(paper:\s*).+$",
                rf"\g<1>PAPER-{paper_val}",
                content,
                count=1,
                flags=re.MULTILINE,
            )
            # Replace {paper} placeholders in body
            content = content.replace("{paper}", paper_val)
        elif item_type == "hypothesis" and "id" in variables:
            # NO PAPER (issue #77). The hypothesis template is written around
            # `H{paper}.{n}`, so leaving these unsubstituted ships a file with
            # literal braces in its frontmatter and its turtle block — which is
            # worse than the error we just removed.
            #
            # Substitute the id for the whole `H{paper}.{n}` token so the turtle
            # subject matches the frontmatter id, and blank the paper field
            # rather than leaving the `PAPER-XXX` scaffold — an unparented
            # hypothesis has no paper, and a placeholder reads like an unfilled
            # field rather than an absent one.
            #
            # ⚠ SCOPED ON item_type, NOT on the presence of an id. render() is
            # the SHARED path for every HDD type and `H{paper}.{n}` also appears
            # in measure.md (an example row) and experiment.md. An id-only guard
            # fires for `measure create`, which passes an id and no paper, and
            # rewrote the hypothesis placeholder in the measure's example table
            # to the MEASURE's own id — so the row claimed the measure was its
            # own hypothesis. Found by checking which templates carry the token
            # rather than assuming only this one did.
            content = content.replace("H{paper}.{n}", str(variables["id"]))
            content = content.replace("EXPR-{paper}", "EXPR-XXX")
            content = re.sub(
                r"^(paper:\s*).*$", r"\g<1>", content, count=1, flags=re.MULTILINE,
            )
        elif item_type == "experiment" and "id" in variables:
            # Same story one type over: an experiment need not belong to a paper
            # either, and its template is written around `EXPR-{paper}` for its
            # own id and `H{paper}.{n}` for the hypothesis it tests.
            #
            # The experiment's own id is known, so substitute it. The HYPOTHESIS
            # placeholder is a DIFFERENT thing and must not become the
            # experiment's id — with no hypothesis it falls back to a scaffold,
            # so the file reads as "not attached" rather than "attached to
            # itself".
            content = content.replace("EXPR-{paper}", str(variables["id"]))
            content = content.replace(
                "H{paper}.{n}", str(variables.get("hypothesis_id", "H-XXX")),
            )
            content = re.sub(
                r"^(paper:\s*).*$", r"\g<1>", content, count=1, flags=re.MULTILINE,
            )
            if "hypothesis_id" not in variables:
                content = re.sub(
                    r"^(hypothesis:\s*).*$", r"\g<1>", content, count=1, flags=re.MULTILINE,
                )

        # Substitute hypothesis number
        if "n" in variables:
            content = content.replace("{n}", variables["n"])

        # Substitute paper number for paper template
        if "paper_num" in variables:
            content = content.replace("{N}", variables["paper_num"])

        # Substitute hypothesis reference in experiment template
        if "hypothesis_id" in variables:
            content = re.sub(
                r"^(hypothesis:\s*).+$",
                rf"\g<1>{variables['hypothesis_id']}",
                content,
                count=1,
                flags=re.MULTILINE,
            )

        # Substitute unit and category for measures
        if "unit" in variables:
            content = re.sub(
                r'^(unit:\s*)".*"',
                rf'\g<1>"{variables["unit"]}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )
            # Also try without quotes
            content = re.sub(
                r"^(unit:\s*)$",
                rf'\g<1>"{variables["unit"]}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )

        if "category" in variables:
            content = re.sub(
                r'^(category:\s*)".*"',
                rf'\g<1>"{variables["category"]}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )
            content = re.sub(
                r"^(category:\s*)$",
                rf'\g<1>"{variables["category"]}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )

        # Substitute target for hypothesis
        if "target" in variables:
            content = re.sub(
                r'^(target:\s*)".*"',
                rf'\g<1>"{variables["target"]}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )

        # Substitute authors for paper
        if "authors" in variables:
            content = re.sub(
                r"^(authors:\s*)\[\]",
                rf"\g<1>[{variables['authors']}]",
                content,
                count=1,
                flags=re.MULTILINE,
            )

        # Generate and substitute Turtle knowledge block for HDD items
        if item_type in _HDD_TURTLE_TYPES:
            turtle_block = self._turtle_builder.build(item_type, variables)
            if turtle_block:
                content = re.sub(
                    r"```turtle\n.*?```",
                    turtle_block,
                    content,
                    count=1,
                    flags=re.DOTALL,
                )

        return content

    def _get_template_path(self, theme: str, item_type: str) -> Path | None:
        """Resolve template file path.

        Looks for templates/{theme}/{item_type}.md
        """
        path = self.templates_dir / theme / f"{item_type}.md"
        if path.exists():
            return path
        return None
