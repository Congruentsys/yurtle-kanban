# ruff: noqa: F811  (the borrowed `world` fixture)
"""Issue #967 (part 1): `_files_at`'s two filters, pinned directly. A UTF-8 file
without the suffix (`notes.txt`) and a symlinked `.md` (mode 120000) in origin's
`.kanban/workflows/` are never read; the real workflow is. (test_865's binary
`.DS_Store` is dropped as non-UTF-8 either way, so it can't tell.)"""

from __future__ import annotations

import os

from tests.issues.test_573_states import _workflow_md
from tests.issues.test_585_create_push_loop import git
from tests.issues.test_865_judge_by_origin_rest import (  # noqa: F401 (fixtures)
    WORKFLOW,
    WORKFLOW_READY_NO_CLAIM,
    _env,
    b_change,
    service,
    world,
)


def test_only_regular_suffix_files_are_read(world) -> None:
    b_change(world, {WORKFLOW: _workflow_md(WORKFLOW_READY_NO_CLAIM, applies_to="expedition")})
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    folder = world.b / ".kanban" / "workflows"
    (folder / "notes.txt").write_text("not a workflow, but UTF-8\n")
    target = world.b / "elsewhere.md"
    target.write_text(_workflow_md(WORKFLOW_READY_NO_CLAIM, applies_to="expedition"))
    os.symlink(os.path.relpath(target, folder), folder / "linked.md")
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "a .txt and a symlinked .md in workflows")
    git(world.b, "push", "origin", f"HEAD:{world.default}")
    git(world.a, "fetch", "origin")
    rev = f"origin/{world.default}"

    read = [name for name, _ in service(world.a)._files_at(rev, ".kanban/workflows/")]

    assert read == [f"{rev}:{WORKFLOW}"], read
