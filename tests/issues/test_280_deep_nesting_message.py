"""Issue #280 — frontmatter nested too deeply for PyYAML's recursive composer.

Decided behaviour ([steer] on #280):
- An item whose frontmatter nests so deeply that parsing hits Python's recursion
  limit (e.g. `extra:` a flow list nested 600 deep) is still skipped — not listed.
- `list` prints exactly ONE stderr warning line for that file, naming it and saying
  "nested too deeply"; the line does not leak "RecursionError" or
  "maximum recursion depth".
- `list` exits 0 with no traceback, and the other items are still listed.
- Controls: 400-deep nesting still lists the item with no warning (as #262 pins); a
  plain broken-YAML file keeps its usual "YAML error: …" reason.

Every CLI call runs in a subprocess: the CLI's real stack depth is what matters.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"
TIMEOUT = 60
TOO_DEEP = 600  # PyYAML's composer overflows near 486
KEPT_DEEP = 400


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        return subprocess.run(
            [sys.executable, "-c", "from yurtle_kanban.cli import main; main()", *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"`{' '.join(args)}` hung for {TIMEOUT}s")


def _ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stdout + proc.stderr, proc.stdout + proc.stderr


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


@pytest.fixture
def sw(tmp_path: Path) -> Path:
    """A software-theme board with two committed features, FEAT-001 and FEAT-002."""
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(tmp_path, *args)
    _ok(_cli(tmp_path, "init", "--theme", "software"))
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    svc = _service(tmp_path)
    svc.create_item(WorkItemType.FEATURE, "Deep item", description="Body")
    svc.create_item(WorkItemType.FEATURE, "Plain item", description="Body")
    _commit_all(tmp_path, "seed")
    return tmp_path


def _item_file(repo: Path, item_id: str = "FEAT-001") -> Path:
    found = [p for p in repo.rglob(f"{item_id}*.md") if ".git" not in p.parts]
    assert len(found) == 1, found
    return found[0]


def _add_frontmatter(repo: Path, lines: str) -> Path:
    """Insert `lines` right after FEAT-001's opening `---`."""
    path = _item_file(repo)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text[:80]
    path.write_text("---\n" + lines + text[4:], encoding="utf-8")
    _commit_all(repo, "edit")
    return path


def _nested(depth: int) -> str:
    return "extra: " + "[" * depth + "x" + "]" * depth + "\n"


def _lines_naming(stderr: str, path: Path) -> list[str]:
    return [ln for ln in stderr.splitlines() if path.name in ln or "FEAT-001" in ln]


class TestTooDeep:
    def test_skipped_with_clear_message(self, sw: Path) -> None:
        path = _add_frontmatter(sw, _nested(TOO_DEEP))
        proc = _cli(sw, "list")
        _ok(proc)
        assert "FEAT-001" not in proc.stdout, proc.stdout
        assert "FEAT-002" in proc.stdout, proc.stdout + proc.stderr
        named = _lines_naming(proc.stderr, path)
        assert len(named) == 1, proc.stderr
        assert "nested too deeply" in named[0], named[0]

    def test_no_recursion_error_text(self, sw: Path) -> None:
        _add_frontmatter(sw, _nested(TOO_DEEP))
        proc = _cli(sw, "list")
        _ok(proc)
        both = proc.stdout + proc.stderr
        assert "RecursionError" not in both, both
        assert "maximum recursion depth" not in both, both

    def test_list_json_still_lists_others(self, sw: Path) -> None:
        _add_frontmatter(sw, _nested(TOO_DEEP))
        proc = _cli(sw, "list", "--json")
        _ok(proc)
        ids = [d["id"] for d in json.loads(proc.stdout)]
        assert ids == ["FEAT-002"], ids
        assert "nested too deeply" in proc.stderr, proc.stderr
        assert "RecursionError" not in proc.stderr, proc.stderr


class TestControls:
    def test_400_deep_kept_without_warning(self, sw: Path) -> None:
        path = _add_frontmatter(sw, _nested(KEPT_DEEP))
        proc = _cli(sw, "list")
        _ok(proc)
        assert "FEAT-001" in proc.stdout, proc.stdout + proc.stderr
        assert "FEAT-002" in proc.stdout, proc.stdout + proc.stderr
        assert not _lines_naming(proc.stderr, path), proc.stderr
        assert "nested too deeply" not in proc.stderr, proc.stderr
        assert "skipped" not in proc.stderr, proc.stderr

    def test_broken_yaml_keeps_yaml_error(self, sw: Path) -> None:
        path = _add_frontmatter(sw, "extra: [unclosed\n")
        proc = _cli(sw, "list")
        _ok(proc)
        assert "FEAT-002" in proc.stdout, proc.stdout + proc.stderr
        named = _lines_naming(proc.stderr, path)
        assert len(named) == 1, proc.stderr
        assert "YAML error: " in named[0], named[0]
        assert "nested too deeply" not in proc.stderr, proc.stderr
