"""#501: follow-ups from the review of PR #487 (#478), per the [steer] ruling.

A. `KanbanConfig.get_board_for_path` returns the DEEPEST board containing the path,
   whatever the config order: with `outer` at `o/` listed before `lab` at `o/lab/`,
   a file under `o/lab/` belongs to `lab`. Consequences: scan attributes such items
   to `lab`; `hdd registry` with `outer` at an outside `/o/` and the hdd board `lab`
   at `/o/lab/` writes `/o/lab/REGISTRY.md`.
B. `_repo_relative` normalises `..` before its symlink fallback: `repo/..` (a board
   `path: "../"`) is OUTSIDE the repo, also when the repo sits under a symlinked dir.
C. No behaviour change (#174's lexical definition kept; docstring only).
Controls: siblings `a/` and `ab/` (a string prefix, not a parent); `repo/x/../y` is
inside; a path reached through a symlinked directory to a file inside is inside (#174).
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
from yurtle_kanban.service import KanbanService

LOGGER = "yurtle-kanban"
OUTSIDE_NOTE = "outside the git repository"  # the #174 warning


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


def _multi_yaml(boards: list[tuple[str, str, str]], default: str) -> str:
    """boards: (name, preset, path)."""
    lines = ['version: "2.0"', "boards:"]
    for name, preset, path in boards:
        lines += [f"  - name: {name}", f"    preset: {preset}", f'    path: "{path}"']
    return "\n".join(lines) + f"\ndefault_board: {default}\n"


def _single_yaml(root: str) -> str:
    return (
        "kanban:\n  theme: hdd\n  paths:\n"
        f'    root: "{root}"\n    scan_paths:\n      - "{root}papers/"\n'
    )


def _init_repo(repo: Path, yaml: str) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir(parents=True, exist_ok=True)
    (repo / ".kanban" / "config.yaml").write_text(yaml)
    (repo / ".keep").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _commits(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


def _paper(root: Path, item_id: str = "PAPER-001") -> Path:
    path = root / "papers" / f"{item_id}-p.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: \"Paper\"\ntype: paper\nstatus: draft\n"
        f"priority: medium\ncreated: 2026-09-26\n---\n\n# {item_id}: Paper\n"
    )
    return path


def _config(where: Path, boards: list[tuple[str, str]]) -> KanbanConfig:
    """A multi-board config from (name, path) pairs, in that order."""
    yaml = _multi_yaml([(n, "software", p) for n, p in boards], default=boards[0][0])
    path = where / "cfg" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml)
    return KanbanConfig.load(path)


def _load(repo: Path) -> KanbanConfig:
    return KanbanConfig.load(repo / ".kanban" / "config.yaml")


def _name(board) -> str | None:
    return board.name if board is not None else None


# --- A. deepest board, regardless of config order ---------------------------------

ORDERS = {
    "outer_first": [("outer", "o/"), ("lab", "o/lab/")],
    "lab_first": [("lab", "o/lab/"), ("outer", "o/")],
}


@pytest.mark.parametrize("order", list(ORDERS))
def test_deepest_board_relative(tmp_path, order):
    cfg = _config(tmp_path, ORDERS[order])
    got = cfg.get_board_for_path(Path("o/lab/x/item.md"), tmp_path)
    assert _name(got) == "lab", f"{order}: o/lab/x/item.md attributed to {_name(got)}"
    got = cfg.get_board_for_path(Path("o/y.md"), tmp_path)
    assert _name(got) == "outer", f"{order}: o/y.md attributed to {_name(got)}"


@pytest.mark.parametrize("order", list(ORDERS))
def test_deepest_board_absolute_outside_repo(tmp_path, order):
    repo, o = tmp_path / "repo", (tmp_path / "o").resolve()
    repo.mkdir()
    boards = [(n, f"{o}/" if p == "o/" else f"{o}/lab/") for n, p in ORDERS[order]]
    cfg = _config(tmp_path, boards)
    for root in (repo, None):
        got = cfg.get_board_for_path(o / "lab" / "x" / "item.md", root)
        assert _name(got) == "lab", f"{order}, root={root}: got {_name(got)}"
        got = cfg.get_board_for_path(o / "y.md", root)
        assert _name(got) == "outer", f"{order}, root={root}: got {_name(got)}"


@pytest.mark.parametrize("order", ["a_first", "ab_first"])
def test_control_sibling_prefix_not_parent(tmp_path, order):
    pairs = [("a", "a/"), ("ab", "ab/")]
    cfg = _config(tmp_path, pairs if order == "a_first" else pairs[::-1])
    assert _name(cfg.get_board_for_path(Path("ab/x.md"), tmp_path)) == "ab"
    assert _name(cfg.get_board_for_path(Path("a/x.md"), tmp_path)) == "a"
    assert cfg.get_board_for_path(Path("abc/x.md"), tmp_path) is None


@pytest.mark.parametrize("order", list(ORDERS))
def test_scan_attributes_nested_item_to_deepest_board(tmp_path, order):
    repo = tmp_path / "repo"
    _paper(repo / "o" / "lab", "PAPER-001")
    _paper(repo / "o", "PAPER-002")
    boards = [(n, "hdd", p) for n, p in ORDERS[order]]
    _init_repo(repo, _multi_yaml(boards, default=boards[0][0]))
    service = KanbanService(_load(repo), repo)
    items = {i.id: i for i in service.scan()}
    assert {"PAPER-001", "PAPER-002"} <= set(items), sorted(items)
    lab_item = service._get_board_for_item(items["PAPER-001"])
    assert _name(lab_item) == "lab", f"{order}: nested item on {_name(lab_item)}"
    outer_item = service._get_board_for_item(items["PAPER-002"])
    assert _name(outer_item) == "outer", f"{order}: outer item on {_name(outer_item)}"


def test_hdd_registry_nested_outside_goes_to_deepest_board(tmp_path, monkeypatch, caplog):
    """`outer` (software, default) at an outside `/o/` listed before the hdd board
    `lab` at `/o/lab/`: hypotheses go under lab, so must the registry."""
    repo, o = tmp_path / "repo", (tmp_path / "o").resolve()
    _paper(o / "lab")
    (o / "lab").mkdir(parents=True, exist_ok=True)
    yaml = _multi_yaml(
        [("outer", "software", f"{o}/"), ("lab", "hdd", f"{o}/lab/")], default="outer",
    )
    _init_repo(repo, yaml)
    monkeypatch.chdir(repo)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = CliRunner().invoke(main, ["hdd", "registry"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert (o / "lab" / "REGISTRY.md").exists(), (
        f"registry not under the hdd board /o/lab/:\n{result.output}"
    )
    assert not (o / "REGISTRY.md").exists(), "registry written under the outer board"


# --- B. `..` is outside, even under a symlinked parent ----------------------------


@pytest.fixture
def linked_repo(tmp_path: Path) -> tuple[Path, Path]:
    """(repo reached through a symlinked parent, the same repo resolved)."""
    real = (tmp_path / "real").resolve()
    (real / "repo" / "x").mkdir(parents=True)
    (real / "repo" / "y").mkdir()
    (real / "repo" / "y" / "f.md").write_text("")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    return link / "repo", real / "repo"


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig(), repo)


def test_dotdot_outside_plain(tmp_path):
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    svc = _service(repo)
    assert svc._repo_relative(repo / "..", repo) is None


@pytest.mark.parametrize("via", ["link", "real"])
def test_dotdot_outside_under_symlinked_parent(linked_repo, via):
    link_repo, real_repo = linked_repo
    root = link_repo
    path_repo = link_repo if via == "link" else real_repo
    svc = _service(root)
    assert svc._repo_relative(path_repo / "..", root) is None, f"{via}/repo/.. inside"
    assert svc._repo_relative(real_repo / "..", link_repo) is None
    assert svc._repo_relative(link_repo / "..", real_repo) is None


def test_board_path_dotdot_is_outside_git(tmp_path):
    repo = (tmp_path / "repo").resolve()
    _init_repo(repo, _single_yaml("research/"))
    svc = KanbanService(_load(repo), repo)
    assert svc._outside_git(repo / Path("../")) is True
    assert svc._outside_git(config_mod._under(repo, "../")) is True


def test_registry_root_dotdot_is_outside(tmp_path, monkeypatch, caplog):
    """Single-board root `../`: outside git, so the registry goes to the root and
    `--push` commits nothing (#174/#192)."""
    top = (tmp_path / "top").resolve()
    repo = top / "repo"
    _paper(top)
    _init_repo(repo, _single_yaml("../"))
    before = _commits(repo)
    monkeypatch.chdir(repo)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = CliRunner().invoke(main, ["hdd", "registry", "--push"])
    assert result.exit_code == 0, (result.output, result.exception)
    seen = " ".join((result.output + caplog.text).split())
    assert (top / "REGISTRY.md").exists(), f"registry not under root ../:\n{seen}"
    assert not (repo / "research" / "REGISTRY.md").exists(), "repo registry created"
    assert _commits(repo) == before, "--push committed a registry of outside items"
    assert OUTSIDE_NOTE in seen, seen


# --- B controls -------------------------------------------------------------------


def test_control_dotdot_back_inside(linked_repo):
    link_repo, real_repo = linked_repo
    for repo in (link_repo, real_repo):
        svc = _service(repo)
        assert svc._repo_relative(repo / "x" / ".." / "y", repo) == Path("y")
        assert svc._repo_relative(repo / "x" / "..", repo) == Path(".")


def test_control_symlinked_dir_to_file_inside(linked_repo):
    """#174: a file reached through a symlinked directory is still inside."""
    link_repo, real_repo = linked_repo
    svc = _service(link_repo)
    assert svc._repo_relative(real_repo / "y" / "f.md", link_repo) == Path("y/f.md")
    assert svc._repo_relative(link_repo / "y" / "f.md", real_repo) == Path("y/f.md")
