"""#479: a leading `~` in a configured path means the user's home.

`Path("~/board")` is relative, so `repo_root / "~/board"` pointed at a literal `~`
directory inside the repo: a `~/board/` board scanned 0 items and `create` wrote into
`<repo>/~/board/...`. Decided behaviour ([steer] bucket 2): a leading `~` in single-board
`paths.root` / `paths.scan_paths` / legacy per-type paths / theme per-type paths, and in
a multi-board board `path`, is expanded to $HOME wherever the path is USED (scan, create
placement, board-for-path lookup, hdd registry default). The config file itself is never
rewritten: load + save keeps the literal `~` spelling.

1. single board: `~` roots/scan paths are scanned; `create` writes under $HOME/board/.
2. multi board: a `path: ~/board/` board scans its items, owns them
   (`get_board_for_path`), and receives created items.
3. round trip: load + save keeps `~/board/` in the YAML.
4. controls: relative and absolute paths behave as before.

HOME (and USERPROFILE) point at a tmp dir, so nothing touches the real home.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tests.issues._snapshot import glob_outside_git
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

SOFTWARE_THEME = Path(__file__).resolve().parents[2] / "themes" / "software.yaml"


# --- helpers ---------------------------------------------------------------------


def _item(path: Path, item_id: str, item_type: str, status: str = "backlog") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: \"{item_id} seeded\"\ntype: {item_type}\n"
        f"status: {status}\npriority: medium\ncreated: 2026-09-26\n---\n\n"
        f"# {item_id}: seeded\n"
    )
    return path


def _init_repo(repo: Path, config_yaml: str) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t.com"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    (repo / ".kanban").mkdir(exist_ok=True)
    (repo / ".kanban" / "config.yaml").write_text(config_yaml)
    return repo


def _no_literal_tilde(repo: Path) -> None:
    tilde = [p for p in glob_outside_git(repo, "~")]
    assert not (repo / "~").exists() and not tilde, (
        f"a literal '~' directory was created inside the repo: {tilde}"
    )


def _cli(repo: Path, monkeypatch, args: list[str]):
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output, result.exception,
    )
    assert result.exit_code == 0, result.output
    return result


def _list_ids(repo: Path, monkeypatch) -> set[str]:
    out = _cli(repo, monkeypatch, ["list", "--json"]).output
    if "No work items found" in out:
        return set()
    return {entry["id"] for entry in json.loads(out)}


@pytest.fixture(autouse=True)
def _clear_theme_cache():
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A fake home: `Path("~").expanduser()` resolves here, never to the real one."""
    fake = (tmp_path / "home").resolve()
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    assert Path("~/board").expanduser() == fake / "board"
    return fake


# --- 1. single board -------------------------------------------------------------

# (id, root, scan_paths, extra `paths:` lines) — every spelling points at $HOME/board/
SINGLE_TILDE = [
    ("root-and-type-scans", "~/board/", ["~/board/features/", "~/board/bugs/"], []),
    ("root-only", "~/board/", None, []),
    ("root-scan", "~/board/", ["~/board/"], []),
    ("no-trailing-slash", "~/board", ["~/board"], []),
]


def _single_yaml(root: str, scans: list[str] | None, extra: list[str], theme="software") -> str:
    lines = ["kanban:", f"  theme: {theme}", "  paths:", f'    root: "{root}"']
    if scans is not None:
        lines.append("    scan_paths:")
        lines += [f'      - "{s}"' for s in scans]
    lines += [f"    {e}" for e in extra]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "root,scans,extra", [c[1:] for c in SINGLE_TILDE], ids=[c[0] for c in SINGLE_TILDE],
)
def test_single_board_tilde_scan_finds_items(tmp_path, home, monkeypatch, root, scans, extra):
    repo = _init_repo(tmp_path / "repo", _single_yaml(root, scans, extra))
    _item(home / "board" / "features" / "FEAT-001-seeded.md", "FEAT-001", "feature")

    config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
    ids = {i.id for i in KanbanService(config, repo).scan()}
    assert "FEAT-001" in ids, f"~ board scanned {sorted(ids)}; FEAT-001 under $HOME/board missed"
    assert "FEAT-001" in _list_ids(repo, monkeypatch)
    _no_literal_tilde(repo)


@pytest.mark.parametrize(
    "root,scans,extra", [c[1:] for c in SINGLE_TILDE], ids=[c[0] for c in SINGLE_TILDE],
)
def test_single_board_tilde_create_writes_under_home(
    tmp_path, home, monkeypatch, root, scans, extra,
):
    repo = _init_repo(tmp_path / "repo", _single_yaml(root, scans, extra))

    _cli(repo, monkeypatch, ["create", "feature", "made here"])

    made = sorted((home / "board").rglob("FEAT-*.md")) if (home / "board").exists() else []
    _no_literal_tilde(repo)
    assert len(made) == 1, f"create did not write under $HOME/board/: {made}"
    assert made[0].parent == home / "board" / "features", made[0]
    assert "FEAT-001" in _list_ids(repo, monkeypatch), "created item not listed"


def test_single_board_legacy_type_path_tilde(tmp_path, home, monkeypatch):
    """`paths.features: ~/board/feats/` places features there, and they are scanned."""
    repo = _init_repo(
        tmp_path / "repo",
        _single_yaml("~/board/", ["~/board/"], ['features: "~/board/feats/"']),
    )

    _cli(repo, monkeypatch, ["create", "feature", "legacy"])

    _no_literal_tilde(repo)
    made = sorted((home / "board").rglob("FEAT-*.md")) if (home / "board").exists() else []
    assert [p.parent for p in made] == [home / "board" / "feats"], made
    assert "FEAT-001" in _list_ids(repo, monkeypatch)


def test_single_board_theme_type_path_tilde(tmp_path, home, monkeypatch):
    """A theme override whose per-type `path` starts with `~` places items under $HOME."""
    repo = _init_repo(tmp_path / "repo", _single_yaml("~/board/", ["~/board/"], []))
    themes = repo / ".kanban" / "themes"
    themes.mkdir(parents=True)
    theme = yaml.safe_load(SOFTWARE_THEME.read_text())
    theme["item_types"]["feature"]["path"] = "~/board/feats/"
    (themes / "software.yaml").write_text(yaml.safe_dump(theme))

    _cli(repo, monkeypatch, ["create", "feature", "themed"])

    _no_literal_tilde(repo)
    made = sorted((home / "board").rglob("FEAT-*.md")) if (home / "board").exists() else []
    assert [p.parent for p in made] == [home / "board" / "feats"], made
    assert "FEAT-001" in _list_ids(repo, monkeypatch)


def test_hdd_registry_tilde_root_indexes_items(tmp_path, home, monkeypatch):
    """`hdd registry` with `root: ~/board/`: written under $HOME/board/ and it lists
    the board's papers (it indexed 0 items before)."""
    repo = _init_repo(
        tmp_path / "repo", _single_yaml("~/board/", ["~/board/papers/"], [], theme="hdd"),
    )
    _item(home / "board" / "papers" / "PAPER-001-reg.md", "PAPER-001", "paper", "draft")

    _cli(repo, monkeypatch, ["hdd", "registry"])

    out = home / "board" / "REGISTRY.md"
    _no_literal_tilde(repo)
    assert out.exists(), "registry not written under $HOME/board/"
    assert "PAPER-001" in out.read_text(), out.read_text()
    assert not (repo / "research" / "REGISTRY.md").exists()


# --- 2. multi board --------------------------------------------------------------


def _multi_yaml(home_path: str, default: str = "home") -> str:
    return (
        "version: \"2.0\"\n"
        f"default_board: {default}\n"
        "boards:\n"
        "  - name: home\n    preset: software\n"
        f"    path: \"{home_path}\"\n"
        "  - name: research\n    preset: hdd\n    path: research/\n"
    )


@pytest.fixture
def multi(tmp_path, home) -> tuple[Path, Path, KanbanService]:
    repo = _init_repo(tmp_path / "repo", _multi_yaml("~/board/"))
    _item(home / "board" / "features" / "FEAT-001-seeded.md", "FEAT-001", "feature")
    _item(repo / "research" / "papers" / "PAPER-001-r.md", "PAPER-001", "paper", "draft")
    config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
    assert config.is_multi_board
    return repo, home, KanbanService(config, repo)


def test_multi_board_tilde_path_scanned(multi):
    repo, _home, svc = multi
    ids = {i.id for i in svc.scan()}
    assert "FEAT-001" in ids, f"~/board/ board scanned 0 items: {sorted(ids)}"
    assert "PAPER-001" in ids  # control: the relative board still scans
    _no_literal_tilde(repo)


def test_multi_board_tilde_items_attributed_to_board(multi):
    repo, home, svc = multi
    svc.scan()
    board = svc.config.get_board_for_path(
        home / "board" / "features" / "FEAT-001-seeded.md", repo,
    )
    assert board is not None and board.name == "home", board
    by_board = {i.id for i in svc.get_items(board="home")}
    assert "FEAT-001" in by_board, by_board


def test_multi_board_tilde_board_view(multi):
    _repo, _home, svc = multi
    board = svc.get_board("home")
    ids = {i.id for i in board.items}
    assert "FEAT-001" in ids, ids


@pytest.mark.parametrize("board_name", ["home", None], ids=["named", "default"])
def test_multi_board_tilde_create_places_under_home(multi, board_name):
    repo, home, svc = multi
    type_dir = svc._get_type_directory(WorkItemType.FEATURE, board_name)
    assert type_dir == home / "board" / "features", type_dir

    item = svc.create_item(WorkItemType.FEATURE, "made here")
    _no_literal_tilde(repo)
    assert item.file_path is not None and item.file_path.parent == home / "board" / "features"
    assert item.file_path.exists()


# --- 3. round trip ---------------------------------------------------------------


def test_single_board_save_keeps_tilde(tmp_path, home):
    repo = _init_repo(
        tmp_path / "repo",
        _single_yaml("~/board/", ["~/board/features/"], ['features: "~/board/feats/"']),
    )
    cfg_path = repo / ".kanban" / "config.yaml"
    KanbanConfig.load(cfg_path).save(cfg_path)

    paths = yaml.safe_load(cfg_path.read_text())["kanban"]["paths"]
    assert paths["root"] == "~/board/", paths
    assert paths["scan_paths"] == ["~/board/features/"], paths
    assert str(home) not in cfg_path.read_text()


def test_multi_board_save_keeps_tilde(tmp_path, home):
    repo = _init_repo(tmp_path / "repo", _multi_yaml("~/board/"))
    cfg_path = repo / ".kanban" / "config.yaml"
    KanbanConfig.load(cfg_path).save(cfg_path)

    boards = {b["name"]: b for b in yaml.safe_load(cfg_path.read_text())["boards"]}
    assert boards["home"]["path"] == "~/board/", boards
    assert str(home) not in cfg_path.read_text()


# --- 4. controls: relative and absolute paths unchanged ---------------------------


@pytest.mark.parametrize("kind", ["relative", "absolute"])
def test_control_single_board_scan_and_create(tmp_path, home, monkeypatch, kind):
    repo = tmp_path / "repo"
    if kind == "relative":
        root, where = "work/", repo / "work"
    else:
        where = (tmp_path / "abs").resolve()
        root = f"{where}/"
    _init_repo(repo, _single_yaml(root, [root], []))
    _item(where / "features" / "FEAT-001-seeded.md", "FEAT-001", "feature")

    assert "FEAT-001" in _list_ids(repo, monkeypatch)
    _cli(repo, monkeypatch, ["create", "feature", "second"])
    made = sorted(p.name for p in (where / "features").glob("FEAT-*.md"))
    assert len(made) == 2 and made[1].startswith("FEAT-002"), made
    assert not (home / "board").exists()
    _no_literal_tilde(repo)


@pytest.mark.parametrize("kind", ["relative", "absolute"])
def test_control_multi_board_scan_and_create(tmp_path, home, kind):
    repo = tmp_path / "repo"
    if kind == "relative":
        path, where = "board/", repo / "board"
    else:
        where = (tmp_path / "abs").resolve()
        path = f"{where}/"
    _init_repo(repo, _multi_yaml(path))
    _item(where / "features" / "FEAT-001-seeded.md", "FEAT-001", "feature")
    svc = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)

    assert "FEAT-001" in {i.id for i in svc.scan()}
    board = svc.config.get_board_for_path(where / "features" / "FEAT-001-seeded.md", repo)
    assert board is not None and board.name == "home", board
    item = svc.create_item(WorkItemType.FEATURE, "second")
    assert item.file_path is not None and item.file_path.parent == where / "features"


def test_control_save_keeps_relative_and_absolute(tmp_path, home):
    abs_root = f"{(tmp_path / 'abs').resolve()}/"
    repo = _init_repo(tmp_path / "repo", _single_yaml("work/", ["work/", abs_root], []))
    cfg_path = repo / ".kanban" / "config.yaml"
    KanbanConfig.load(cfg_path).save(cfg_path)
    paths = yaml.safe_load(cfg_path.read_text())["kanban"]["paths"]
    assert paths["root"] == "work/" and paths["scan_paths"] == ["work/", abs_root], paths
