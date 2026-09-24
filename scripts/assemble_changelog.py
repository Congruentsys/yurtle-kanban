#!/usr/bin/env python3
"""Assemble `changelog.d/` fragments into a release section of CHANGELOG.md (#178).

Each PR adds `changelog.d/<N>.md` (or `<N>-<slug>.md`): a first line
`<!-- section: Fixed -->`, then the bullet exactly as it should read. At release time

    python scripts/assemble_changelog.py X.Y.Z [--date YYYY-MM-DD]

adds `## [X.Y.Z] - <date>` under `## [Unreleased]`, holding whatever was under
Unreleased plus every fragment, grouped by section (Keep a Changelog order) and ordered
by issue number, then deletes the fragments. Nothing is written if a fragment is bad or
the version already exists; with nothing to release it changes nothing.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
FRAGMENT_NAME = re.compile(r"(\d+)(?:-.*)?\.md")
SECTION_LINE = re.compile(r"<!-- section: (\w+) -->")
UNRELEASED = "## [Unreleased]"
RELEASE_HEADING = re.compile(r"^## \[", re.MULTILINE)
SUBHEADING = re.compile(r"^### (.+?)[ \t]*$", re.MULTILINE)


class FragmentError(ValueError):
    """A fragment without a known `<!-- section: … -->` first line."""


def read_fragments(directory: Path) -> list[tuple[int, str, str, Path]]:
    """(issue number, section, text, path) for each fragment, in release order."""
    found = []
    for path in directory.iterdir() if directory.is_dir() else []:
        m = FRAGMENT_NAME.fullmatch(path.name)
        if not m or not path.is_file():
            continue
        first, _, rest = path.read_text().partition("\n")
        head = SECTION_LINE.fullmatch(first.strip())
        if not head or head.group(1) not in SECTIONS:
            raise FragmentError(
                f"{path}: first line must be <!-- section: X -->, X one of {', '.join(SECTIONS)}"
            )
        found.append((int(m.group(1)), head.group(1), rest.rstrip(), path))
    # `12.md` before `12-b.md`: the plain fragment first, then the rest by name
    return sorted(found, key=lambda f: (f[0], f[3].name != f"{f[0]}.md", f[3].name))


def split_sections(body: str) -> tuple[str, dict[str, str]]:
    """Text before the first `### ` heading, and each `### Section`'s entries."""
    heads = list(SUBHEADING.finditer(body))
    lead = body[: heads[0].start()] if heads else body
    sections: dict[str, str] = {}
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        text = body[h.end() : end].strip("\n").rstrip()
        name = h.group(1)
        sections[name] = f"{sections[name]}\n{text}" if name in sections and text else (
            sections.get(name) or text
        )
    return lead.strip("\n"), sections


def assemble(changelog: str, fragments: list[tuple[int, str, str, Path]], version: str,
             date: str) -> str | None:
    """The new CHANGELOG text, or None when there is nothing to release."""
    if re.search(rf"^## \[{re.escape(version)}\]", changelog, re.MULTILINE):
        raise ValueError(f"CHANGELOG already has a ## [{version}] section")
    start = changelog.find(UNRELEASED)
    if start != -1:
        body_start = start + len(UNRELEASED)
        nxt = RELEASE_HEADING.search(changelog, body_start)
        body_end = nxt.start() if nxt else len(changelog)
        body = changelog[body_start:body_end]
        before, after = changelog[:start], changelog[body_end:]
    else:
        nxt = RELEASE_HEADING.search(changelog)
        cut = nxt.start() if nxt else len(changelog)
        body, before, after = "", changelog[:cut], changelog[cut:]

    lead, sections = split_sections(body)
    for _, name, text, _ in fragments:
        if text:
            sections[name] = f"{sections[name]}\n{text}" if sections.get(name) else text
    order = [s for s in SECTIONS if sections.get(s)] + [
        s for s in sections if s not in SECTIONS and sections[s]
    ]
    if not lead and not order:
        return None

    parts = [lead] if lead else []
    parts += [f"### {name}\n\n{sections[name]}" for name in order]
    release = f"## [{version}] - {date}\n\n" + "\n\n".join(parts) + "\n"
    if before and not before.endswith("\n\n"):
        before = before.rstrip("\n") + "\n\n"
    text = f"{before}{UNRELEASED}\n\n{release}"
    return f"{text}\n{after}" if after else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("version", help="the release version, e.g. 2.3.0")
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--fragments", type=Path, default=Path("changelog.d"))
    args = parser.parse_args(argv)

    try:
        fragments = read_fragments(args.fragments)
        new = assemble(args.changelog.read_text(), fragments, args.version, args.date)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if new is None:
        print("nothing to release: no fragments and nothing under ## [Unreleased]")
        return 0
    args.changelog.write_text(new)
    for *_, path in fragments:
        path.unlink()
    print(f"assembled {len(fragments)} fragment(s) into ## [{args.version}] - {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
