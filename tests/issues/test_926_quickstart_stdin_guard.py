"""Issue #926: the README Quick Start runner (test_861) must never let a command
read the script itself as its stdin. Run as `bash -s` with the script on stdin, a
stdin-reading line would swallow the rest of the block and the harness would
report FEWER failures. A planted `cat >/dev/null` followed by a bogus command:
the bogus command must still be reported."""

from __future__ import annotations

from pathlib import Path

from tests.issues.test_861_readme_quick_start_runs import (
    README,
    failures,
    quick_start,
    run_quick_start,
)


def test_a_stdin_reading_line_does_not_swallow_the_block(tmp_path: Path) -> None:
    readme = README.read_text(encoding="utf-8")
    _, body = quick_start(readme)
    board = body.index("yurtle-kanban board")
    planted = (
        body[:board]
        + ["cat >/dev/null", "yurtle-kanban move FEAT-999 done"]
        + body[board + 1 :]
    )
    result, first = run_quick_start(tmp_path, readme.replace("\n".join(body), "\n".join(planted)))
    found = failures(result, first)
    assert found == [f"README.md:{first + board + 1} exited 1: yurtle-kanban move FEAT-999 done"], (
        result.stderr
    )
