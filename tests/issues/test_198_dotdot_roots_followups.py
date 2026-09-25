"""#198: follow-ups to #174 / PR #195 for board roots reached through ``..``.

1. README's config section says that a board root OUTSIDE the repo (absolute or
   ``../``) matches ignore patterns against the ABSOLUTE path, so it needs
   ``**/…`` (or absolute) patterns.
2. ``init`` with no ``git`` on PATH does not crash (it used to run
   ``git rev-parse --show-toplevel`` unguarded); the git toplevel lookup is one
   helper shared with ``KanbanService`` rather than a second copy in ``cli``.
3. A RELATIVE ``repo_root`` (``KanbanService(config, Path("B"))``) still commits:
   ``git add B/work/…`` with ``cwd=B`` doubled the path and git exited 128.
"""

from __future__ import annotations

import inspect
import logging
import re
import subprocess
import traceback
from pathlib import Path

import pytest
from click.testing import CliRunner

import yurtle_kanban.cli as cli_mod
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

README = Path(__file__).resolve().parents[2] / "README.md"
COMMIT_FAILED = "Git commit failed"
LOGGER = "yurtle-kanban"
UNTRACKED = "not be git-tracked"


@pytest.fixture(autouse=True)
def _clear_theme_cache():
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return done.stdout


def _commits(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


def _item(path: Path, item_id: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: \"x\"\ntype: feature\nstatus: backlog\n"
        f"priority: medium\ncreated: 2026-09-24\n---\n\n# {item_id}: x\n"
    )
    return path


def _yaml(root: str) -> str:
    return (
        "kanban:\n  theme: software\n  paths:\n"
        f'    root: "{root}"\n    scan_paths:\n      - "{root}"\n    ignore: []\n'
    )


# --- 1. README documents the absolute ignore key for outside roots -----------


def _section_with(text: str, needle: str) -> str:
    """The `## ` section (headings inside code fences don't count) containing needle."""
    sections: list[list[str]] = [[]]
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            sections.append([])
        sections[-1].append(line)
    hits = ["\n".join(s) for s in sections if needle in "\n".join(s)]
    assert hits, f"no README section contains {needle!r}"
    return hits[0]


def test_readme_config_section_documents_outside_root_ignore():
    section = _section_with(README.read_text(), "ignore:")
    flat = " ".join(section.split())
    match = re.search(r"outside the repo(sitory)?", flat, re.IGNORECASE)
    assert match, f"config section doesn't mention a root outside the repo:\n{section}"
    # `**/` must be explained in that context, not only in the yaml example
    after = flat[max(0, match.start() - 400):match.end() + 400]
    assert "**/" in after, f"no `**/` guidance near 'outside the repo':\n{after}"
    assert re.search(r"absolute", after, re.IGNORECASE), f"no 'absolute' path note:\n{after}"


# --- 2. init without git on PATH ----------------------------------------------


def _no_git_path(tmp_path: Path, monkeypatch) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def _run_init(args: list[str]):
    result = CliRunner().invoke(main, ["init", "--theme", "software", *args])
    tb = ""
    if result.exc_info and not isinstance(result.exception, SystemExit):
        tb = "".join(traceback.format_exception(*result.exc_info))
    return result, tb


@pytest.mark.parametrize("kind", ["outside-path", "plain"])
def test_init_without_git_does_not_crash(tmp_path, monkeypatch, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _no_git_path(tmp_path, monkeypatch)
    args = ["--path", f"{tmp_path / 'outside'}/"] if kind == "outside-path" else []
    result, tb = _run_init(args)
    assert result.exit_code == 0, f"{result.output}\n{tb}"
    assert "Traceback" not in result.output + tb, result.output + tb
    assert (repo / ".kanban" / "config.yaml").exists()


def test_cli_does_not_run_its_own_rev_parse():
    src = inspect.getsource(cli_mod)
    assert "rev-parse" not in src, "cli should use the shared git toplevel helper"


def test_control_init_with_git_outside_path_warns(tmp_path, monkeypatch, caplog):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    monkeypatch.chdir(repo)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result, tb = _run_init(["--path", f"{tmp_path / 'outside'}/"])
    assert result.exit_code == 0, f"{result.output}\n{tb}"
    assert UNTRACKED in " ".join((result.output + caplog.text).split()), result.output


# --- 3. relative repo_root commits --------------------------------------------


@pytest.fixture
def repo_b(tmp_path: Path) -> Path:
    repo = tmp_path / "B"
    _item(repo / "work" / "features" / "FEAT-001-in.md", "FEAT-001")
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(_yaml("work/"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _service(repo: Path, relative: bool, monkeypatch) -> KanbanService:
    config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
    if relative:
        monkeypatch.chdir(repo.parent)
        return KanbanService(config, Path(repo.name))
    return KanbanService(config, repo)


def _op(service: KanbanService, op: str) -> None:
    if op == "move":
        service.move_item("FEAT-001", WorkItemStatus.READY, validate_workflow=False,
                          skip_wip_check=True, skip_gates=True)
    else:
        service.add_comment("FEAT-001", "hello there", "tester")


ROOTS = ["relative", "absolute"]


@pytest.mark.parametrize("op", ["move", "comment"])
@pytest.mark.parametrize("root", ROOTS)
def test_repo_root_op_commits(repo_b, monkeypatch, caplog, root, op):
    service = _service(repo_b, root == "relative", monkeypatch)
    service.scan()
    before = _commits(repo_b)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _op(service, op)
    text = (repo_b / "work" / "features" / "FEAT-001-in.md").read_text()
    assert ("ready" if op == "move" else "hello there") in text, text
    assert COMMIT_FAILED not in caplog.text, caplog.text
    assert _commits(repo_b) == before + 1, f"{root} repo_root: {op} not committed"


@pytest.mark.parametrize("root", ROOTS)
def test_repo_root_create_push_commits(repo_b, monkeypatch, caplog, root):
    service = _service(repo_b, root == "relative", monkeypatch)
    before = _commits(repo_b)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = service.create_item_and_push(WorkItemType.FEATURE, "pushed")
    assert result["success"] is True, result
    assert COMMIT_FAILED not in caplog.text, caplog.text
    assert _commits(repo_b) == before + 1, f"{root} repo_root: create not committed: {result}"
    assert list((repo_b / "work").rglob(f"{result['id']}-*.md")), "item file not written"
