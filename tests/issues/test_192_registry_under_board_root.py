"""#192: `hdd registry` writes REGISTRY.md under the HDD board root when that root
is outside the repo ([steer] decision B, building on #174 / #156).

1. Board root outside the repo, no `--output`: the registry is written to
   `<board root>/REGISTRY.md`; `<repo>/research/REGISTRY.md` is NOT created.
2. `--push` with an outside root: pushing is skipped with the #174
   "outside the git repository" warning, and nothing is committed in the repo.
3. Controls: an in-repo HDD board still writes `research/REGISTRY.md` (and `--push`
   still commits it); an explicit `--output` is honoured for both kinds of board.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

OUTSIDE_NOTE = "outside the git repository"  # the #174 warning
LOGGER = "yurtle-kanban"
PAPER_ID = "PAPER-001"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return done.stdout


def _hdd_yaml(root: str) -> str:
    return (
        "kanban:\n  theme: hdd\n  paths:\n"
        f'    root: "{root}"\n    scan_paths:\n      - "{root}papers/"\n'
    )


def _paper(root: Path) -> None:
    path = root / "papers" / f"{PAPER_ID}-registry.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {PAPER_ID}\ntitle: \"Registry paper\"\ntype: paper\nstatus: draft\n"
        f"priority: medium\ncreated: 2026-09-26\n---\n\n# {PAPER_ID}: Registry paper\n"
    )


def _init_repo(repo: Path, root: str) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir(exist_ok=True)
    (repo / ".kanban" / "config.yaml").write_text(_hdd_yaml(root))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _commits(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(autouse=True)
def _clear_theme_cache():
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def outside_board(tmp_path: Path) -> tuple[Path, Path]:
    """repo at tmp/repo; HDD board root in a sibling dir tmp/board (not in the repo)."""
    repo, board = tmp_path / "repo", (tmp_path / "board").resolve()
    _paper(board)
    _init_repo(repo, f"{board}/")
    return repo, board


@pytest.fixture
def inside_board(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _paper(repo / "research")
    return _init_repo(repo, "research/")


def _registry(repo: Path, args: list[str], monkeypatch, caplog) -> tuple[object, str]:
    monkeypatch.chdir(repo)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = CliRunner().invoke(main, ["hdd", "registry", *args])
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output, result.exception,
    )
    assert result.exit_code == 0, result.output
    return result, _flat(result.output + caplog.text)


# --- 1. outside board root, no --output ------------------------------------------


def test_outside_root_registry_written_under_board_root(outside_board, monkeypatch, caplog):
    repo, board = outside_board
    _registry(repo, [], monkeypatch, caplog)
    out = board / "REGISTRY.md"
    assert out.exists(), f"registry not written under the board root: {out}"
    assert PAPER_ID in out.read_text(), out.read_text()


def test_outside_root_registry_not_written_in_repo(outside_board, monkeypatch, caplog):
    repo, _board = outside_board
    _registry(repo, [], monkeypatch, caplog)
    assert not (repo / "research" / "REGISTRY.md").exists(), "repo registry created"
    assert not (repo / "research").exists(), "research/ dir created in the repo"


# --- 2. outside board root, --push ----------------------------------------------


def test_outside_root_push_skipped_with_174_warning(outside_board, monkeypatch, caplog):
    repo, board = outside_board
    before = _commits(repo)
    _result, seen = _registry(repo, ["--push"], monkeypatch, caplog)
    assert _commits(repo) == before, "--push committed in the repo for an outside root"
    assert OUTSIDE_NOTE in seen, f"no '{OUTSIDE_NOTE}' warning:\n{seen}"
    assert "git push failed" not in seen, seen
    assert (board / "REGISTRY.md").exists(), "registry not written under the board root"
    assert not (repo / "research" / "REGISTRY.md").exists(), "repo registry created"
    assert _git(repo, "status", "--porcelain").strip() == "", "repo left dirty"


# --- 3. controls -----------------------------------------------------------------


def test_control_in_repo_registry_in_research(inside_board, monkeypatch, caplog):
    repo = inside_board
    _result, seen = _registry(repo, [], monkeypatch, caplog)
    out = repo / "research" / "REGISTRY.md"
    assert out.exists(), "in-repo board: research/REGISTRY.md not written"
    assert PAPER_ID in out.read_text(), out.read_text()
    assert OUTSIDE_NOTE not in seen, seen


def test_control_in_repo_push_commits(inside_board, monkeypatch, caplog):
    repo = inside_board
    before = _commits(repo)
    _result, seen = _registry(repo, ["--push"], monkeypatch, caplog)
    assert _commits(repo) == before + 1, f"in-repo --push did not commit:\n{seen}"
    assert "research/REGISTRY.md" in _git(repo, "show", "--name-only", "HEAD")
    assert OUTSIDE_NOTE not in seen, seen


@pytest.mark.parametrize("kind", ["outside", "inside"])
def test_control_explicit_output_honoured(tmp_path, monkeypatch, caplog, kind):
    if kind == "outside":
        repo, board = tmp_path / "repo", (tmp_path / "board").resolve()
        _paper(board)
        _init_repo(repo, f"{board}/")
    else:
        repo = tmp_path / "repo"
        board = repo / "research"
        _paper(board)
        _init_repo(repo, "research/")
    target = tmp_path / "custom" / "MY-REGISTRY.md"
    _registry(repo, ["--output", str(target)], monkeypatch, caplog)
    assert target.exists(), f"--output ignored ({kind})"
    assert PAPER_ID in target.read_text(), target.read_text()
    assert not (board / "REGISTRY.md").exists(), f"default written despite --output ({kind})"
    assert not (repo / "research" / "REGISTRY.md").exists(), (
        f"repo default written despite --output ({kind})"
    )
