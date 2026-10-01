#!/usr/bin/env python3
"""Print GitHub release notes for one version of CHANGELOG.md, sized to fit (#1191).

    python scripts/release_notes.py X.Y.Z [--changelog PATH]

A GitHub release body is capped at 125,000 characters, and a refused
`gh release create` publishes nothing: `publish.yml` never runs, so PyPI is skipped
silently. The version's section is printed whole when it fits. Otherwise the notes
are condensed: the entry counts per section, every `**Breaking` entry (from any
section, with its sub-bullets), the Removed and Deprecated sections, and a link to
the full section. Notes that still don't fit, or a version not in the changelog, are
refused: nothing on stdout, exit 1.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LIMIT = 125_000  # GitHub's release-body cap, in characters
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
CHANGELOG_URL = "https://github.com/Congruentsys/yurtle-kanban/blob/v{version}/CHANGELOG.md"
ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, version: str) -> tuple[str, str]:
    """(the `## [version] …` heading, its body up to the next `## [`)."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(f"## [{version}]")]
    if not starts:
        raise ValueError(f"version {version} is not in the changelog")
    start = starts[0]
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines)
    )
    return lines[start], "\n".join(lines[start + 1 : end]).strip("\n")


def _subsections(body: str) -> dict[str, list[str]]:
    """`### Name` -> its top-level entries, each with its indented continuation."""
    found: dict[str, list[str]] = {}
    entries: list[str] | None = None
    for line in body.splitlines():
        if line.startswith("### "):
            entries = found.setdefault(line[4:].strip(), [])
        elif entries is not None and line.startswith("- "):
            entries.append(line)
        elif entries is not None and entries and (line.startswith((" ", "\t")) or not line):
            entries[-1] += "\n" + line
    return {name: [e.rstrip("\n") for e in items] for name, items in found.items()}


def _anchor(heading: str) -> str:
    """GitHub's anchor for a markdown heading: `## [3.0.0] - 2026-09-30` -> `300---2026-09-30`.

    GitHub's rule: lowercase; drop anything but letters (any script), digits, spaces,
    `_` and `-`; spaces become `-`."""
    title = heading.lstrip("#").strip().lower()
    return re.sub(r"[^\w -]", "", title).replace(" ", "-")


def release_notes(changelog_text: str, version: str, *, limit: int = LIMIT) -> str:
    """The notes for `version`: its whole section when it fits in `limit`, else the
    condensed notes. Raises ValueError for an unknown version or notes that can't fit."""
    heading, body = _section(changelog_text, version)
    if len(body) <= limit:
        return body
    sections = _subsections(body)
    counts = ", ".join(f"{len(sections[s])} {s}" for s in SECTIONS if sections.get(s))
    breaking = [e for items in sections.values() for e in items if "**Breaking" in e]
    parts = [
        f"This release has {counts} entries; the full list is too long for a GitHub "
        "release, so these notes keep the breaking changes."
    ]
    if breaking:
        parts.append("## Breaking changes\n\n" + "\n".join(breaking))
    for name in ("Removed", "Deprecated"):
        rest = [e for e in sections.get(name, []) if "**Breaking" not in e]  # listed above
        if rest:
            parts.append(f"## {name}\n\n" + "\n".join(rest))
    link = CHANGELOG_URL.format(version=version) + "#" + _anchor(heading)
    parts.append(f"Full changelog: [CHANGELOG.md, {version}]({link})")
    notes = "\n\n".join(parts)
    if len(notes) > limit:
        raise ValueError(
            f"even the condensed notes for {version} are {len(notes)} characters, over "
            f"the {limit}-character limit: write them by hand"
        )
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("version", help="the release version, e.g. 3.0.0")
    parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    args = parser.parse_args(argv)
    try:
        notes = release_notes(args.changelog.read_text(encoding="utf-8"), args.version)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
