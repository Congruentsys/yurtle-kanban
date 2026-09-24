"""#174: board roots outside the repo, or reached through a symlink.

1. An in-repo board whose files are reached through a symlinked absolute path
   still matches ANCHORED ignore patterns (``work/hidden/*``), single- and
   multi-board. ``repo_root`` is the resolved path; the board path in config is
   the symlinked one, so scanned files are not ``relative_to(repo_root)``.
2. Git operations on an item outside the git repository (#156) skip git with a
   clear "outside the git repository" warning instead of "Git commit failed";
   ``create --push`` succeeds; ``init --path <outside>`` warns "not be git-tracked".
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

OUTSIDE_NOTE = "outside the git repository"
COMMIT_FAILED = "Git commit failed"
LOGGER = "yurtle-kanban"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return done.stdout


def _init_repo(repo: Path, config_yaml: str) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir(exist_ok=True)
    (repo / ".kanban" / "config.yaml").write_text(config_yaml)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _commits(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


def _item(path: Path, item_id: str, title: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: \"{title}\"\ntype: feature\nstatus: backlog\n"
        f"priority: medium\ncreated: 2026-09-24\n---\n\n# {item_id}: {title}\n"
    )
    return path


def _single_yaml(root: str, ignore: list[str]) -> str:
    lines = ["kanban:", "  theme: software", "  paths:", f'    root: "{root}"',
             "    scan_paths:", f'      - "{root}"', "    ignore:" + ("" if ignore else " []")]
    lines += [f'      - "{p}"' for p in ignore]
    return "\n".join(lines) + "\n"


def _multi_yaml(path: str, ignore: list[str]) -> str:
    lines = ['version: "2.0"', "boards:", "  - name: dev", "    preset: software",
             f'    path: "{path}"', "    ignore:" + ("" if ignore else " []")]
    lines += [f'      - "{p}"' for p in ignore]
    return "\n".join(lines) + "\ndefault_board: dev\n"


def _service(repo: Path) -> KanbanService:
    config_mod._theme_cache.clear()
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _ids(service: KanbanService) -> set[str]:
    return {i.id for i in service.scan()}


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(autouse=True)
def _clear_theme_cache():
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


# --- Part 1: symlinked in-repo board and anchored ignore patterns -------------


@pytest.fixture
def linked(tmp_path: Path) -> tuple[Path, Path]:
    """repo at tmp/real (resolved), symlink tmp/link -> real."""
    real = (tmp_path / "real").resolve()
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    _item(real / "work" / "features" / "FEAT-001-live.md", "FEAT-001")
    _item(real / "work" / "hidden" / "FEAT-002-hid.md", "FEAT-002")
    return real, link


@pytest.mark.parametrize("make_yaml", [_single_yaml, _multi_yaml], ids=["single", "multi"])
def test_symlinked_in_repo_board_anchored_ignore(linked, make_yaml):
    real, link = linked
    _init_repo(real, make_yaml(f"{link / 'work'}/", ["work/hidden/*"]))
    ids = _ids(_service(real))
    assert "FEAT-001" in ids, f"control: live item missing: {ids}"
    assert "FEAT-002" not in ids, f"work/hidden/* not applied via symlinked path: {ids}"


@pytest.mark.parametrize("make_yaml", [_single_yaml, _multi_yaml], ids=["single", "multi"])
def test_control_outside_board_absolute_ignore_still_matches(tmp_path, make_yaml):
    repo, outside = tmp_path / "repo", (tmp_path / "outside").resolve()
    _item(outside / "features" / "FEAT-001-live.md", "FEAT-001")
    _item(outside / "hidden" / "FEAT-002-hid.md", "FEAT-002")
    _init_repo(repo, make_yaml(f"{outside}/", [f"{outside}/hidden/*"]))
    ids = _ids(_service(repo))
    assert "FEAT-001" in ids and "FEAT-002" not in ids, ids


# --- Part 2: git operations on items outside the repository -------------------


@pytest.fixture
def outside_board(tmp_path: Path) -> tuple[Path, Path]:
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    _item(outside / "features" / "FEAT-001-out.md", "FEAT-001")
    _init_repo(repo, _single_yaml(f"{outside}/", []))
    return repo, outside


@pytest.fixture
def inside_board(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _item(repo / "work" / "features" / "FEAT-001-in.md", "FEAT-001")
    return _init_repo(repo, _single_yaml("work/", []))


def _do(service: KanbanService, op: str) -> None:
    if op == "move":
        service.move_item("FEAT-001", WorkItemStatus.READY, validate_workflow=False,
                          skip_wip_check=True, skip_gates=True)
    elif op == "comment":
        service.add_comment("FEAT-001", "hello there", "tester")
    else:
        service.rank_item("FEAT-001", 3)


def _check_file(path: Path, op: str) -> None:
    text = path.read_text()
    expected = {"move": "ready", "comment": "hello there", "rank": "priority_rank"}[op]
    assert expected in text, f"{op} change not applied to {path}:\n{text}"


OPS = ["move", "comment", "rank"]
CLI_ARGS = {
    "move": ["move", "FEAT-001", "ready", "--force", "--skip-gates"],
    "comment": ["comment", "FEAT-001", "hello there"],
    "rank": ["rank", "FEAT-001", "3"],
}


@pytest.mark.parametrize("op", OPS)
def test_service_op_outside_repo_skips_git_with_note(outside_board, caplog, op):
    repo, outside = outside_board
    service = _service(repo)
    service.scan()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _do(service, op)
    _check_file(outside / "features" / "FEAT-001-out.md", op)
    assert COMMIT_FAILED not in caplog.text, caplog.text
    assert OUTSIDE_NOTE in caplog.text, f"no '{OUTSIDE_NOTE}' warning:\n{caplog.text}"


@pytest.mark.parametrize("op", OPS)
def test_cli_op_outside_repo_skips_git_with_note(outside_board, caplog, monkeypatch, op):
    repo, outside = outside_board
    monkeypatch.chdir(repo)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = CliRunner().invoke(main, CLI_ARGS[op])
    assert result.exit_code == 0, result.output
    _check_file(outside / "features" / "FEAT-001-out.md", op)
    seen = result.output + caplog.text
    assert COMMIT_FAILED not in seen, seen
    assert OUTSIDE_NOTE in _flat(seen), f"no '{OUTSIDE_NOTE}' note:\n{seen}"


@pytest.mark.parametrize("op", OPS)
def test_control_in_repo_op_commits(inside_board, caplog, op):
    repo = inside_board
    service = _service(repo)
    service.scan()
    before = _commits(repo)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _do(service, op)
    assert _commits(repo) == before + 1, f"{op} did not commit"
    assert OUTSIDE_NOTE not in caplog.text, caplog.text


def test_create_push_outside_repo_succeeds(outside_board, caplog):
    repo, outside = outside_board
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = _service(repo).create_item_and_push(WorkItemType.FEATURE, "pushed")
    assert result["success"] is True, result
    assert result["pushed"] is False, result
    assert OUTSIDE_NOTE in result["message"], result
    assert list(outside.rglob(f"{result['id']}-*.md")), "item file not written"


def test_cli_create_push_outside_repo_exits_zero(outside_board, monkeypatch):
    repo, outside = outside_board
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, ["create", "feature", "pushed", "--push"])
    assert result.exit_code == 0, result.output
    assert COMMIT_FAILED not in result.output, result.output
    assert list(outside.rglob("FEAT-002-*.md")), "item file not written"


def test_control_create_push_in_repo_commits(inside_board):
    repo = inside_board
    before = _commits(repo)
    result = _service(repo).create_item_and_push(WorkItemType.FEATURE, "pushed")
    assert result["success"] is True, result
    assert OUTSIDE_NOTE not in (result.get("message") or ""), result
    assert _commits(repo) == before + 1


def _init_cli(repo: Path, path: str, monkeypatch, caplog):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    monkeypatch.chdir(repo)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = CliRunner().invoke(main, ["init", "--theme", "software", "--path", path])
    assert result.exit_code == 0, result.output
    return _flat(result.output + caplog.text)


def test_init_path_outside_repo_warns(tmp_path, monkeypatch, caplog):
    outside = tmp_path / "outside"
    seen = _init_cli(tmp_path / "repo", f"{outside}/", monkeypatch, caplog)
    assert (tmp_path / "repo" / ".kanban" / "config.yaml").exists()
    assert "not be git-tracked" in seen, seen


def test_control_init_path_in_repo_no_warning(tmp_path, monkeypatch, caplog):
    seen = _init_cli(tmp_path / "repo", "work/", monkeypatch, caplog)
    assert "not be git-tracked" not in seen, seen


# --- Round 2 (PR #195 review) -------------------------------------------------

# 1. A relative root with `..` that leads outside the repo is outside it too.


@pytest.fixture
def dotdot_board(tmp_path: Path) -> tuple[Path, Path]:
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    _item(outside / "features" / "FEAT-001-out.md", "FEAT-001")
    _init_repo(repo, _single_yaml("../outside/", []))
    return repo, outside


@pytest.mark.parametrize("op", ["move", "comment"])
def test_dotdot_root_op_skips_git_with_note(dotdot_board, caplog, op):
    repo, outside = dotdot_board
    service = _service(repo)
    service.scan()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _do(service, op)
    _check_file(outside / "features" / "FEAT-001-out.md", op)
    assert COMMIT_FAILED not in caplog.text, caplog.text
    assert OUTSIDE_NOTE in caplog.text, f"no '{OUTSIDE_NOTE}' warning:\n{caplog.text}"


def test_dotdot_root_create_push_succeeds(dotdot_board, caplog):
    repo, outside = dotdot_board
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = _service(repo).create_item_and_push(WorkItemType.FEATURE, "pushed")
    assert result["success"] is True, result
    assert result["pushed"] is False, result
    assert OUTSIDE_NOTE in result["message"], result
    assert COMMIT_FAILED not in caplog.text, caplog.text
    assert list(outside.rglob(f"{result['id']}-*.md")), "item file not written"


def test_dotdot_init_path_warns(tmp_path, monkeypatch, caplog):
    seen = _init_cli(tmp_path / "repo", "../outside/", monkeypatch, caplog)
    assert "not be git-tracked" in seen, seen


# 2. `.kanban/` in a subdirectory of the git repo: a board elsewhere in that repo
#    is git-tracked (git toplevel, not repo_root, decides).


@pytest.fixture
def subdir_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    top = tmp_path / "A"
    proj, shared = top / "proj", top / "shared"
    _item(shared / "features" / "FEAT-001-sh.md", "FEAT-001")
    proj.mkdir(parents=True)
    return top, proj, shared


def _commit_subdir(top: Path, proj: Path, root: str) -> None:
    (proj / ".kanban").mkdir()
    (proj / ".kanban" / "config.yaml").write_text(_single_yaml(root, []))
    _init_repo(top, "")  # git init + initial commit of everything at the top
    (top / ".kanban" / "config.yaml").unlink()
    (top / ".kanban").rmdir()


SUBDIR_ROOTS = ["absolute", "dotdot"]


def _subdir_root(shared: Path, kind: str) -> str:
    return f"{shared}/" if kind == "absolute" else "../shared/"


@pytest.mark.parametrize("kind", SUBDIR_ROOTS)
def test_control_subdir_kanban_comment_commits(subdir_repo, caplog, kind):
    top, proj, shared = subdir_repo
    _commit_subdir(top, proj, _subdir_root(shared, kind))
    service = _service(proj)
    service.scan()
    before = _commits(top)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _do(service, "comment")
    assert OUTSIDE_NOTE not in caplog.text, caplog.text
    assert _commits(top) == before + 1, f"comment not committed:\n{caplog.text}"


@pytest.mark.parametrize("kind", SUBDIR_ROOTS)
def test_control_subdir_kanban_create_push_commits(subdir_repo, caplog, kind):
    top, proj, shared = subdir_repo
    _commit_subdir(top, proj, _subdir_root(shared, kind))
    before = _commits(top)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = _service(proj).create_item_and_push(WorkItemType.FEATURE, "pushed")
    assert result["success"] is True, result
    assert OUTSIDE_NOTE not in (result.get("message") or ""), result
    assert OUTSIDE_NOTE not in caplog.text, caplog.text
    assert _commits(top) == before + 1, f"create --push not committed: {result}"


def test_control_subdir_init_path_in_git_repo_no_warning(tmp_path, monkeypatch, caplog):
    top = tmp_path / "A"
    proj = top / "proj"
    proj.mkdir(parents=True)
    _git(top, "init", "-b", "main")
    monkeypatch.chdir(proj)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = CliRunner().invoke(
            main, ["init", "--theme", "software", "--path", f"{top / 'shared'}/"],
        )
    assert result.exit_code == 0, result.output
    seen = _flat(result.output + caplog.text)
    assert "not be git-tracked" not in seen, seen


# 3. Symlinked board path whose item file is itself a symlink to a file outside
#    the repo: it is keyed by its in-repo path (only the parent is resolved).


@pytest.fixture
def linked_item(linked, tmp_path: Path) -> tuple[Path, Path]:
    real, link = linked
    target = _item(tmp_path / "elsewhere" / "FEAT-009.md", "FEAT-009", "ext")
    (real / "work" / "features" / "FEAT-009.md").symlink_to(target)
    return real, link


def test_symlinked_item_file_matches_anchored_ignore(linked_item):
    real, link = linked_item
    _init_repo(real, _single_yaml(f"{link / 'work'}/", ["work/features/*"]))
    ids = _ids(_service(real))
    assert "FEAT-001" not in ids, f"control: sibling not ignored: {ids}"
    assert "FEAT-009" not in ids, f"symlinked item file missed work/features/*: {ids}"


def test_symlinked_item_file_comment_not_outside(linked_item, caplog):
    real, link = linked_item
    _init_repo(real, _single_yaml(f"{link / 'work'}/", ["work/hidden/*"]))
    service = _service(real)
    service.scan()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        service.add_comment("FEAT-009", "hello there", "tester")
    assert OUTSIDE_NOTE not in caplog.text, caplog.text


# 4. `_commit_and_push_file` (HDD parent/link push) on an outside file.


@pytest.mark.parametrize("kind", ["absolute", "dotdot"])
def test_commit_and_push_file_outside_repo(outside_board, caplog, kind):
    repo, outside = outside_board
    target = outside / "features" / "FEAT-001-out.md"
    path = target if kind == "absolute" else repo / ".." / "outside" / target.relative_to(outside)
    target.write_text(target.read_text() + "\nedit\n")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        ok = _service(repo)._commit_and_push_file(path, "link parent")
    assert ok is False
    assert COMMIT_FAILED not in caplog.text, caplog.text
    assert OUTSIDE_NOTE in caplog.text, f"no '{OUTSIDE_NOTE}' warning:\n{caplog.text}"
