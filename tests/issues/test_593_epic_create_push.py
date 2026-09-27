"""Issue #593 — `epic create --push` treats `create_item_and_push`'s dict as a WorkItem.

`_do_create` assigns the return value of `service.create_item_and_push(...)` to
`item` and then reads `item.id` / `item.file_path`. `create_item_and_push`
returns a dict (`success`, `item`, `id`, `pushed`, `message`, ...), so the
`--push` path crashes with AttributeError after the epic has been committed.

Route (real git, no monkeypatching): a bare remote plus a clone with a real
`yurtle-kanban init` board (software theme -> EPIC, nautical theme -> VOYAGE),
driven through the CLI in a subprocess:

- `epic create "Title" --push` (and `voyage create ... --push` on nautical)
  exits 0, prints the new epic id, and the epic file is on origin/main.
- Control: without `--push` the same command still exits 0, prints the id and
  writes the file locally (nothing pushed).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
TITLE = "Chart the reef"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-c", "from yurtle_kanban.cli import main; main()", *args],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120,
    )


def _make_repo(tmp_path: Path, theme: str) -> Path:
    """A clone of a bare remote, with an initialised board pushed to origin/main."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)],
        capture_output=True, text=True, check=True,
    )
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(clone)],
        capture_output=True, text=True, check=True,
    )
    for args in (
        ["checkout", "-q", "-B", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(clone, *args)
    proc = _cli(clone, "init", "--theme", theme)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "init board")
    _git(clone, "push", "-q", "-u", "origin", "main")
    return clone


# (theme, command group, expected id prefix)
CASES = {
    "software-epic": ("software", "epic", "EPIC-"),
    "nautical-voyage": ("nautical", "voyage", "VOY-"),
    "nautical-epic-alias": ("nautical", "epic", "VOY-"),
}


def _new_files(repo: Path, prefix: str) -> list[Path]:
    return [
        p for p in repo.rglob(f"{prefix}*.md")
        if ".git" not in p.parts and ".kanban" not in p.parts
    ]


@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
def test_create_push_exits_zero_and_lands_on_origin(tmp_path: Path, case: str) -> None:
    theme, group, prefix = CASES[case]
    repo = _make_repo(tmp_path, theme)

    proc = _cli(repo, group, "create", TITLE, "--push")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"{group} create --push failed:\n{out}"
    assert "Traceback" not in out, out
    assert f"{prefix}001" in proc.stdout, f"new id not printed:\n{out}"

    files = _new_files(repo, prefix)
    assert len(files) == 1, f"expected one {prefix} file, got {files}"
    rel = files[0].relative_to(repo).as_posix()

    _git(repo, "fetch", "-q", "origin")
    on_origin = _git(repo, "ls-tree", "-r", "--name-only", "origin/main")
    assert rel in on_origin.splitlines(), f"{rel} not on origin/main:\n{on_origin}"


@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
def test_create_without_push_control(tmp_path: Path, case: str) -> None:
    theme, group, prefix = CASES[case]
    repo = _make_repo(tmp_path, theme)

    proc = _cli(repo, group, "create", TITLE)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert f"{prefix}001" in proc.stdout, out

    files = _new_files(repo, prefix)
    assert len(files) == 1, f"expected one {prefix} file, got {files}"
    rel = files[0].relative_to(repo).as_posix()
    on_origin = _git(repo, "ls-tree", "-r", "--name-only", "origin/main")
    assert rel not in on_origin.splitlines(), "non-push create must not push"
