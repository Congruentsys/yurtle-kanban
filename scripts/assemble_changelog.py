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
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# fences may be indented (a code block inside a list item): wider than CommonMark's
# 3 spaces, on purpose. A closing fence is the run alone, same character, as long.
FENCE = re.compile(r"[ \t]*(```+|~~~+)(.*)")
FENCE_CLOSE = re.compile(r"[ \t]*(```+|~~~+)[ \t]*\n?")


class FragmentError(ValueError):
    """A fragment without a known `<!-- section: … -->` first line, or empty."""


def read_fragments(directory: Path) -> list[tuple[int, str, str, Path]]:
    """(issue number, section, text, path) for each fragment, in release order."""
    found = []
    for path in directory.iterdir() if directory.is_dir() else []:
        m = FRAGMENT_NAME.fullmatch(path.name)
        if not m or not path.is_file():
            continue
        first, _, rest = path.read_text().partition("\n")  # universal newlines
        head = SECTION_LINE.fullmatch(first.strip())
        if not head or head.group(1) not in SECTIONS:
            raise FragmentError(
                f"{path}: first line must be <!-- section: X -->, X one of {', '.join(SECTIONS)}"
            )
        if not rest.strip():
            raise FragmentError(f"{path}: no entry below the section line")
        try:
            headings(rest, "\0")  # only its fence check: one left open breaks every release
        except ValueError as e:
            raise FragmentError(f"{path}: {e}") from None
        found.append((int(m.group(1)), head.group(1), rest.rstrip(), path))
    # `12.md` before `12-b.md`: the plain fragment first, then the rest by name
    return sorted(found, key=lambda f: (f[0], f[3].name != f"{f[0]}.md", f[3].name))


def headings(text: str, prefix: str) -> list[tuple[int, int]]:
    """(start, end) of each line starting with `prefix`, outside fenced code
    blocks: a ``` or ~~~ block may quote a `### ` or `## [` line (#197)."""
    found, pos, fence = [], 0, ""
    for line in text.splitlines(keepends=True):
        if fence:
            m = FENCE_CLOSE.fullmatch(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = ""
        elif (m := FENCE.match(line)) and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            # a backtick fence's info string can't hold a backtick (CommonMark):
            # "```x``` inline" is text, not an opener (#223)
            fence = m.group(1)
        elif line.startswith(prefix):
            found.append((pos, pos + len(line.rstrip("\n"))))
        pos += len(line)
    if fence:
        # an unclosed fence would hide every heading after it: refuse, don't guess
        raise ValueError(f"unclosed code fence ({fence})")
    return found


def split_sections(body: str) -> tuple[str, dict[str, str]]:
    """Text before the first `### ` heading, and each `### Section`'s entries."""
    heads = headings(body, "### ")
    lead = body[: heads[0][0]] if heads else body
    sections: dict[str, str] = {}
    for n, (start, end) in enumerate(heads):
        stop = heads[n + 1][0] if n + 1 < len(heads) else len(body)
        text = body[end:stop].strip("\n").rstrip()
        name = body[start + 4 : end].strip()
        sections[name] = f"{sections[name]}\n{text}" if name in sections and text else (
            sections.get(name) or text
        )
    return lead.strip("\n"), sections


def assemble(changelog: str, fragments: list[tuple[int, str, str, Path]], version: str,
             date: str) -> str | None:
    """The new CHANGELOG text (LF), or None when there is nothing to release."""
    try:
        releases = headings(changelog, "## [")
    except ValueError as e:
        raise ValueError(f"{e} in the CHANGELOG") from None
    if any(changelog[s:e].startswith(f"## [{version}]") for s, e in releases):
        raise ValueError(f"CHANGELOG already has a ## [{version}] section")
    unreleased = next((h for h in releases if changelog[h[0]:h[1]].startswith(UNRELEASED)), None)
    if unreleased is not None:
        start, body_start = unreleased
        # the heading line stays as written (`## [Unreleased] - TBD`); its text
        # never leaks into the release (#197)
        heading = changelog[start:body_start]
        nxt = next((s for s, _ in releases if s > start), None)
        body_end = nxt if nxt is not None else len(changelog)
        body = changelog[body_start:body_end]
        before, after = changelog[:start], changelog[body_end:]
    else:
        cut = releases[0][0] if releases else len(changelog)
        heading, body, before, after = UNRELEASED, "", changelog[:cut], changelog[cut:]

    lead, sections = split_sections(body)
    for _, name, text, _ in fragments:
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
    text = f"{before}{heading}\n\n{release}"
    return f"{text}\n{after}" if after else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("version", help="the release version, e.g. 2.3.0")
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--fragments", type=Path, default=Path("changelog.d"))
    args = parser.parse_args(argv)

    try:
        if not DATE.fullmatch(args.date):
            raise ValueError(f"--date must be YYYY-MM-DD, got {args.date!r}")
        datetime.date.fromisoformat(args.date)  # a real day: not 2026-13-01
        fragments = read_fragments(args.fragments)
        raw = args.changelog.read_bytes().decode("utf-8")
        crlf = "\r\n" in raw  # keep the file's endings, new lines too (#197)
        new = assemble(raw.replace("\r\n", "\n"), fragments, args.version, args.date)
        if new is None:
            print("nothing to release: no fragments and nothing under ## [Unreleased]")
            return 0
        if crlf:
            new = new.replace("\n", "\r\n")
        # the CHANGELOG first: a failed write leaves every fragment in place
        args.changelog.write_bytes(new.encode("utf-8"))
        left = []
        for *_, path in fragments:
            try:
                path.unlink()
            except OSError:
                left.append(str(path))
        if left:
            print(
                f"error: the CHANGELOG was already written for {args.version}, but these "
                f"fragments could not be deleted; delete them by hand: {', '.join(left)}",
                file=sys.stderr,
            )
            return 1
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"assembled {len(fragments)} fragment(s) into ## [{args.version}] - {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
