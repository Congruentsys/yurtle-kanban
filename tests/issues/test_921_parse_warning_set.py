# ruff: noqa: F811  (the borrowed `sw` fixture)
"""Issue #921: `_warn_parse` de-duplicates parse warnings with a set kept beside
`parse_warnings`. The set must never suppress a warning the list no longer holds:
after a rescan (`_scan` resets the list), in a fetched-rev judge (`copy.copy` of
the service, list reset), or after any reset of the list alone."""

from __future__ import annotations

import copy
from pathlib import Path

from tests.issues._snapshot import glob_outside_git
from tests.issues.test_262_cyclic_frontmatter import _service, sw  # noqa: F401 (fixture)

BROKEN = "---\nid: FEAT-009\ntitle: [unclosed\n---\n\nBody\n"


def _broken(repo: Path) -> Path:
    path = next(glob_outside_git(repo, "FEAT-001*.md")).parent / "FEAT-009-broken.md"
    path.write_text(BROKEN)
    return path


def _warned(svc, path: Path) -> list[str]:
    return [reason for p, reason in svc.parse_warnings if p == path]


def test_rescan_reports_the_broken_file_again(sw: Path) -> None:
    broken = _broken(sw)
    svc = _service(sw)
    svc.get_items()
    assert len(_warned(svc, broken)) == 1, svc.parse_warnings
    svc._scan()
    assert len(_warned(svc, broken)) == 1, svc.parse_warnings


def test_list_reset_alone_does_not_suppress_the_warning(sw: Path) -> None:
    broken = _broken(sw)
    svc = _service(sw)
    svc.get_items()
    reason = _warned(svc, broken)[0]
    svc.parse_warnings = []  # a reset site that forgets the set
    svc._warn_parse(broken, reason)
    assert _warned(svc, broken) == [reason], svc.parse_warnings


def test_a_judge_copy_and_the_service_each_record_their_own(sw: Path) -> None:
    broken = _broken(sw)
    svc = _service(sw)
    judge = copy.copy(svc)  # as the fetched-rev judge is made
    judge.parse_warnings = []
    judge._warn_parse(broken, "judge saw it")
    svc._warn_parse(broken, "judge saw it")
    assert _warned(svc, broken) == ["judge saw it"], svc.parse_warnings
    assert _warned(judge, broken) == ["judge saw it"], judge.parse_warnings


def test_a_repeated_warning_is_recorded_once(sw: Path) -> None:
    broken = _broken(sw)
    svc = _service(sw)
    svc._warn_parse(broken, "x")
    svc._warn_parse(broken, "x")
    assert _warned(svc, broken) == ["x"], svc.parse_warnings
