#!/usr/bin/env python3
"""Check a major release's breaking changes each have an upgrade-guide entry (#1232).

    python scripts/check_upgrade_guide.py X.Y.Z [--changelog PATH] [--guide PATH]

The deprecation policy (CONTRIBUTING.md): a major release removes what a minor release
deprecated, and every removal reaches users through the upgrade guide, UPGRADING.md.
For a major (X.0.0, X >= 1) every top-level entry of the version's `### Removed` section,
and every entry anywhere in it marked `**Breaking`, must have its first `#N` issue
reference in the guide. An entry with no `#N` can't be checked and is a problem too.
A minor or patch has nothing to check. Exit 0 when there are no problems, else 1 with
each problem on stderr.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = "UPGRADING.md"
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
ISSUE = re.compile(r"#(\d+)")


def _release_notes():
    """scripts/release_notes.py, for its CHANGELOG section parsing (#1191)."""
    spec = importlib.util.spec_from_file_location(
        "release_notes", Path(__file__).resolve().parent / "release_notes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_major(version: str) -> bool:
    m = VERSION.fullmatch(version)
    return bool(m) and int(m.group(1)) >= 1 and m.group(2) == m.group(3) == "0"


def check(changelog_text: str, version: str, guide_text: str | None) -> list[str]:
    """The problems with `version`'s upgrade-guide coverage; empty means OK. Raises
    ValueError when `version` isn't in the changelog."""
    notes = _release_notes()
    _, body = notes._section(changelog_text, version)  # ValueError: not in the changelog
    if not is_major(version):
        return []
    sections = notes._subsections(body)
    needed = list(sections.get("Removed", []))
    needed += [
        e for name, items in sections.items() if name != "Removed"
        for e in items if "**Breaking" in e
    ]
    if not needed:
        return []
    if guide_text is None:
        return [f"{version} removes or breaks {len(needed)} thing(s), but there is no {GUIDE}"]
    problems = []
    for entry in needed:
        first = entry.splitlines()[0]
        m = ISSUE.search(entry)
        if m is None:
            problems.append(f"no issue number to check against {GUIDE}; give it one: {first}")
        elif not re.search(rf"(?<![\w#])#{m.group(1)}(?!\d)", guide_text):
            problems.append(f"#{m.group(1)} is not in {GUIDE}: {first}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("version", help="the release version, e.g. 4.0.0")
    parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    parser.add_argument("--guide", type=Path, default=ROOT / GUIDE)
    args = parser.parse_args(argv)
    try:
        changelog = args.changelog.read_text(encoding="utf-8")
        guide = args.guide.read_text(encoding="utf-8") if args.guide.exists() else None
        problems = check(changelog, args.version, guide)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not is_major(args.version):
        print(f"{args.version} is not a major release: the upgrade-guide check is for majors")
        return 0
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
