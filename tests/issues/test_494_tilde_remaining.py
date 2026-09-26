"""#494: the `~` sites #479 left unexpanded.

Decided behaviour ([steer] bucket 2, the same rule as #479): a leading `~` means $HOME
wherever a path is used or compared.

1. `WorkItemIndexer.scan` with a `~` root / scan path finds the items under $HOME.
2. `init --path "~/x"` (a quoted, unexpanded `~`) puts the board under $HOME/x, never a
   literal `<repo>/~/x`; `create` and `list` then work there.
3. Mixed spellings of one place (`root: ~/b/` with an absolute `$HOME/b/p/` scan path,
   and the mirror) give the same answer as the all-absolute spelling in
   `service._board_root()`, `config._single_board_path()` and the `board-add`
   coverage check (the single -> multi-board upgrade).
4. `**/archive/**` excludes `archive/` items from a `~` board, single-board
   (`paths.ignore`) and multi-board (`BoardConfig.ignore`).
Controls: relative and absolute spellings behave as before.

HOME (and USERPROFILE) point at a tmp dir, so nothing touches the real home.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_479_tilde_paths import (
    _cli,
    _init_repo,
    _item,
    _list_ids,
    _multi_yaml,
    _no_literal_tilde,
    _single_yaml,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.indexer import WorkItemIndexer
from yurtle_kanban.service import KanbanService


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
    assert Path("~/b").expanduser() == fake / "b"
    return fake


def _load(repo: Path) -> KanbanConfig:
    return KanbanConfig.load(repo / ".kanban" / "config.yaml")


# --- 1. WorkItemIndexer ----------------------------------------------------------


@pytest.mark.parametrize(
    "root,scans",
    [("~/b/", ["~/b/"]), ("~/b/", None), ("~/b", ["~/b/features/"])],
    ids=["root-scan", "root-only", "type-scan"],
)
def test_indexer_tilde_scan_finds_items(tmp_path, home, root, scans):
    repo = _init_repo(tmp_path / "repo", _single_yaml(root, scans, []))
    _item(home / "b" / "features" / "FEAT-001-seeded.md", "FEAT-001", "feature")

    ids = {i.id for i in WorkItemIndexer(_load(repo), repo).scan()}
    assert "FEAT-001" in ids, f"indexer scanned {sorted(ids)}; FEAT-001 under $HOME/b missed"
    _no_literal_tilde(repo)


def test_indexer_tilde_ignores_archive(tmp_path, home):
    repo = _init_repo(
        tmp_path / "repo",
        _single_yaml("~/b/", ["~/b/"], ["ignore:", '  - "**/archive/**"']),
    )
    _item(home / "b" / "features" / "FEAT-001-live.md", "FEAT-001", "feature")
    _item(home / "b" / "archive" / "FEAT-002-old.md", "FEAT-002", "feature")

    ids = {i.id for i in WorkItemIndexer(_load(repo), repo).scan()}
    assert "FEAT-001" in ids and "FEAT-002" not in ids, ids


@pytest.mark.parametrize("kind", ["relative", "absolute"])
def test_control_indexer_relative_and_absolute(tmp_path, home, kind):
    repo = tmp_path / "repo"
    if kind == "relative":
        root, where = "work/", repo / "work"
    else:
        where = (tmp_path / "abs").resolve()
        root = f"{where}/"
    _init_repo(repo, _single_yaml(root, [root], []))
    _item(where / "features" / "FEAT-001-seeded.md", "FEAT-001", "feature")

    assert "FEAT-001" in {i.id for i in WorkItemIndexer(_load(repo), repo).scan()}


# --- 2. init --path "~/x" --------------------------------------------------------


def _git_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t.com"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    return repo


@pytest.mark.parametrize("spelling", ["~/x", "~/x/"])
def test_init_tilde_path_board_under_home(tmp_path, home, monkeypatch, spelling):
    repo = _git_repo(tmp_path / "repo")

    _cli(repo, monkeypatch, ["init", "--path", spelling])

    _no_literal_tilde(repo)
    assert (home / "x").is_dir(), "init did not scaffold the board under $HOME/x"
    # a seeded item under $HOME/x is scanned, and create writes under $HOME/x
    _item(home / "x" / "bugs" / "BUG-001-seeded.md", "BUG-001", "bug")
    assert "BUG-001" in _list_ids(repo, monkeypatch)
    _cli(repo, monkeypatch, ["create", "feature", "made here"])
    _no_literal_tilde(repo)
    made = sorted((home / "x").rglob("FEAT-*.md"))
    assert len(made) == 1, f"create did not write under $HOME/x: {made}"
    assert "FEAT-001" in _list_ids(repo, monkeypatch)
    # whatever init wrote as the root, it names $HOME/x
    root = _load(repo).paths.root
    assert Path(root).expanduser() == home / "x", root


@pytest.mark.parametrize("kind", ["relative", "absolute"])
def test_control_init_path_relative_and_absolute(tmp_path, home, monkeypatch, kind):
    repo = _git_repo(tmp_path / "repo")
    if kind == "relative":
        arg, where = "x/", repo / "x"
    else:
        where = (tmp_path / "abs").resolve()
        arg = f"{where}/"

    _cli(repo, monkeypatch, ["init", "--path", arg])
    _cli(repo, monkeypatch, ["create", "feature", "made here"])

    assert len(sorted(where.rglob("FEAT-*.md"))) == 1
    assert "FEAT-001" in _list_ids(repo, monkeypatch)
    assert not (home / "x").exists()
    _no_literal_tilde(repo)


# --- 3. mixed spellings ----------------------------------------------------------

# (id, root, scan_paths) with "H" standing for the absolute $HOME. Each case is compared
# with the same config spelled all-absolute (every "~" replaced by $HOME).
MIXED = [
    ("root-tilde-scan-abs", "~/b/", ["H/b/p/"]),
    ("root-abs-scan-tilde", "H/b/", ["~/b/p/"]),
    ("root-tilde-scans-abs-contain-root", "~/b/", ["H/b/", "H/c/"]),
    ("root-abs-scans-tilde-contain-root", "H/b/", ["~/b/", "~/c/"]),
    ("root-tilde-scans-mixed", "~/b/", ["~/b/p/", "H/b/q/"]),
    ("root-abs-scans-mixed", "H/b/", ["H/b/p/", "~/b/q/"]),
]


def _spell(value: str, home: Path, absolute: bool) -> str:
    if value.startswith("H/"):
        return f"{home}/{value[2:]}"
    if absolute and value.startswith("~/"):
        return f"{home}/{value[2:]}"
    return value


def _mixed_repo(tmp_path: Path, home: Path, root: str, scans: list[str], absolute: bool):
    repo = _init_repo(
        tmp_path / ("repo-abs" if absolute else "repo"),
        _single_yaml(
            _spell(root, home, absolute), [_spell(s, home, absolute) for s in scans], [],
        ),
    )
    return repo


@pytest.mark.parametrize("root,scans", [c[1:] for c in MIXED], ids=[c[0] for c in MIXED])
def test_mixed_spellings_board_root(tmp_path, home, root, scans):
    repo = _mixed_repo(tmp_path, home, root, scans, absolute=False)
    ref = _mixed_repo(tmp_path, home, root, scans, absolute=True)

    got = KanbanService(_load(repo), repo)._board_root()
    want = KanbanService(_load(ref), ref)._board_root()
    assert Path(got).expanduser() == Path(want), (got, want)


def test_mixed_spellings_board_root_returns_root(tmp_path, home):
    """The #494 repro: `root: ~/b/` contains the absolute `$HOME/b/p/` scan path."""
    repo = _init_repo(tmp_path / "repo", _single_yaml("~/b/", [f"{home}/b/p/"], []))
    got = KanbanService(_load(repo), repo)._board_root()
    assert Path(got).expanduser() == home / "b", got


@pytest.mark.parametrize("root,scans", [c[1:] for c in MIXED], ids=[c[0] for c in MIXED])
def test_mixed_spellings_single_board_path(tmp_path, home, root, scans):
    repo = _mixed_repo(tmp_path, home, root, scans, absolute=False)
    ref = _mixed_repo(tmp_path, home, root, scans, absolute=True)

    got = _load(repo)._single_board_path()
    want = _load(ref)._single_board_path()
    assert Path(got).expanduser() == Path(want), (got, want)


@pytest.mark.parametrize(
    "root,scans",
    [("~/b/", ["H/b/p/"]), ("H/b/", ["~/b/p/"]),
     ("~/b/", ["~/b/p/", "H/b/q/"]), ("H/b/", ["H/b/p/", "~/b/q/"])],
    ids=["root-tilde-scan-abs", "root-abs-scan-tilde",
         "root-tilde-scans-mixed", "root-abs-scans-mixed"],
)
def test_mixed_spellings_board_add_upgrades(tmp_path, home, monkeypatch, root, scans):
    """Every scan path lies under $HOME/b, so the upgrade to multi-board covers them
    all: board-add succeeds and the old board still sees its items."""
    repo = _mixed_repo(tmp_path, home, root, scans, absolute=False)
    for n, scan in enumerate(scans, start=1):
        where = Path(_spell(scan, home, absolute=True))
        _item(where / "features" / f"FEAT-00{n}-seeded.md", f"FEAT-00{n}", "feature")

    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        main, ["board-add", "research", "--preset", "hdd", "--path", "research/"],
    )
    assert "Can't upgrade" not in result.output, result.output
    assert result.exit_code == 0, result.output

    config = _load(repo)
    assert config.is_multi_board
    ids = {i.id for i in KanbanService(config, repo).scan()}
    want = {f"FEAT-00{n}" for n in range(1, len(scans) + 1)}
    assert want <= ids, f"after the upgrade the board scanned {sorted(ids)}"
    _no_literal_tilde(repo)


@pytest.mark.parametrize("kind", ["relative", "absolute", "tilde"])
def test_control_consistent_spellings(tmp_path, home, kind):
    """One spelling throughout: `_board_root` is the root that contains the scan path,
    and `_single_board_path` is that scan path (unchanged behaviour)."""
    repo = tmp_path / "repo"
    base = {"relative": "b", "absolute": f"{home}/b", "tilde": "~/b"}[kind]
    _init_repo(repo, _single_yaml(f"{base}/", [f"{base}/p/"], []))
    config = _load(repo)
    assert KanbanService(config, repo)._board_root() == f"{base}/"
    assert config._single_board_path() == f"{base}/p/"


# --- 4. archive ignore on a `~` board --------------------------------------------


def test_single_board_tilde_ignores_archive(tmp_path, home, monkeypatch):
    repo = _init_repo(
        tmp_path / "repo",
        _single_yaml("~/b/", ["~/b/"], ["ignore:", '  - "**/archive/**"']),
    )
    _item(home / "b" / "features" / "FEAT-001-live.md", "FEAT-001", "feature")
    _item(home / "b" / "archive" / "FEAT-002-old.md", "FEAT-002", "feature")

    config = _load(repo)
    assert config.paths.ignore == ["**/archive/**"]
    for ids in (
        {i.id for i in KanbanService(config, repo).scan()},
        _list_ids(repo, monkeypatch),
    ):
        assert "FEAT-001" in ids and "FEAT-002" not in ids, ids


def test_multi_board_tilde_ignores_archive(tmp_path, home):
    yaml_text = _multi_yaml("~/b/").replace(
        '    path: "~/b/"\n', '    path: "~/b/"\n    ignore:\n      - "**/archive/**"\n',
    )
    repo = _init_repo(tmp_path / "repo", yaml_text)
    _item(home / "b" / "features" / "FEAT-001-live.md", "FEAT-001", "feature")
    _item(home / "b" / "archive" / "FEAT-002-old.md", "FEAT-002", "feature")

    config = _load(repo)
    assert config.is_multi_board and config.get_board("home").ignore == ["**/archive/**"]
    svc = KanbanService(config, repo)
    ids = {i.id for i in svc.scan()}
    assert "FEAT-001" in ids and "FEAT-002" not in ids, ids
    board_ids = {i.id for i in svc.get_board("home").items}
    assert "FEAT-001" in board_ids and "FEAT-002" not in board_ids, board_ids
