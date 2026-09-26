"""#478: `hdd registry` default path — follow-ups from the review of PR #475 (#192),
applying the #174 / #192 ruling ([steer] bucket 2).

1. Multi-board: with no `--output`, the registry goes to `<HDD board path>/REGISTRY.md`
   when that HDD board lies outside the git repository, else `research/REGISTRY.md`.
   "The HDD board" is the one hdd items are created on: default_board first, then the
   boards in config order, first whose preset is hdd. `--push` then commits nothing
   (the #174 warning, exit 0).
2. One definition of "outside" (the git toplevel): `.kanban/` in `sub/` of a git repo
   with root `../sibling/` is inside git, so the registry stays with the repo
   (`sub/research/REGISTRY.md`) and `--push` commits it.
3. "Registry written to ..." prints a normalised path (no `..` segments).
Controls: an in-repo multi-board HDD board keeps `research/REGISTRY.md`; an explicit
`--output` always wins; a plain outside single-board root still uses the board root.
"""

from __future__ import annotations

import logging
import os
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


def _paper(root: Path) -> None:
    path = root / "papers" / f"{PAPER_ID}-registry.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {PAPER_ID}\ntitle: \"Registry paper\"\ntype: paper\nstatus: draft\n"
        f"priority: medium\ncreated: 2026-09-26\n---\n\n# {PAPER_ID}: Registry paper\n"
    )


def _single_yaml(root: str) -> str:
    return (
        "kanban:\n  theme: hdd\n  paths:\n"
        f'    root: "{root}"\n    scan_paths:\n      - "{root}papers/"\n'
    )


def _multi_yaml(boards: list[tuple[str, str, str]], default: str) -> str:
    """boards: (name, preset, path)."""
    lines = ['version: "2.0"', "boards:"]
    for name, preset, path in boards:
        lines += [f"  - name: {name}", f"    preset: {preset}", f'    path: "{path}"']
    return "\n".join(lines) + f"\ndefault_board: {default}\n"


def _init_git(top: Path) -> None:
    top.mkdir(parents=True, exist_ok=True)
    _git(top, "init", "-b", "main")
    _git(top, "config", "user.email", "t@t.com")
    _git(top, "config", "user.name", "T")


def _init_repo(repo: Path, yaml: str, git_top: Path | None = None) -> Path:
    """`.kanban/config.yaml` in `repo`; git initialised at `git_top` (default: repo)."""
    top = git_top or repo
    _init_git(top)
    (repo / ".kanban").mkdir(parents=True, exist_ok=True)
    (repo / ".kanban" / "config.yaml").write_text(yaml)
    (repo / ".keep").write_text("")
    _git(top, "add", "-A")
    _git(top, "commit", "-m", "init")
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


def _registry(repo: Path, args: list[str], monkeypatch, caplog) -> tuple[str, str]:
    """Run `hdd registry` in `repo`; return (flattened output+log, output unwrapped)."""
    monkeypatch.chdir(repo)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = CliRunner().invoke(main, ["hdd", "registry", *args])
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output, result.exception,
    )
    assert result.exit_code == 0, result.output
    # rich may fold a long path across lines: rejoin without inserting spaces
    unwrapped = "".join(line.strip() for line in result.output.splitlines())
    return _flat(result.output + caplog.text), unwrapped


# --- 1. multi-board, HDD board outside the repo ----------------------------------


@pytest.fixture
def multi_outside(tmp_path: Path) -> tuple[Path, Path]:
    """repo with an in-repo software board (default) and an HDD board at tmp/board."""
    repo, board = tmp_path / "repo", (tmp_path / "board").resolve()
    _paper(board)
    (repo / "work").mkdir(parents=True)
    yaml = _multi_yaml(
        [("dev", "software", "work/"), ("research", "hdd", f"{board}/")], default="dev",
    )
    _init_repo(repo, yaml)
    return repo, board


def test_multi_outside_hdd_board_registry_under_board(multi_outside, monkeypatch, caplog):
    repo, board = multi_outside
    _registry(repo, [], monkeypatch, caplog)
    out = board / "REGISTRY.md"
    assert out.exists(), f"registry not written under the outside HDD board: {out}"
    assert PAPER_ID in out.read_text(), out.read_text()
    assert not (repo / "research" / "REGISTRY.md").exists(), "repo registry created"


def test_multi_outside_hdd_board_push_skipped(multi_outside, monkeypatch, caplog):
    repo, board = multi_outside
    before = _commits(repo)
    seen, _ = _registry(repo, ["--push"], monkeypatch, caplog)
    assert _commits(repo) == before, "--push committed an index of untracked items"
    assert OUTSIDE_NOTE in seen, f"no '{OUTSIDE_NOTE}' warning:\n{seen}"
    assert "git push failed" not in seen, seen
    assert (board / "REGISTRY.md").exists(), "registry not written under the HDD board"
    assert _git(repo, "status", "--porcelain").strip() == "", "repo left dirty"


def test_multi_hdd_board_chosen_in_config_order(tmp_path, monkeypatch, caplog):
    """default_board is software; the FIRST hdd board in config order (outside) wins
    over a later in-repo hdd board."""
    repo, board = tmp_path / "repo", (tmp_path / "board").resolve()
    _paper(board)
    (repo / "research2").mkdir(parents=True)
    yaml = _multi_yaml(
        [("dev", "software", "work/"), ("research", "hdd", f"{board}/"),
         ("later", "hdd", "research2/")],
        default="dev",
    )
    _init_repo(repo, yaml)
    _registry(repo, [], monkeypatch, caplog)
    assert (board / "REGISTRY.md").exists(), "first hdd board in config order not used"
    assert not (repo / "research" / "REGISTRY.md").exists(), "repo registry created"


def test_multi_default_hdd_board_in_repo_wins(tmp_path, monkeypatch, caplog):
    """default_board is an in-repo hdd board: it is the HDD board even though an
    outside hdd board is listed before it → research/REGISTRY.md."""
    repo, board = tmp_path / "repo", (tmp_path / "board").resolve()
    _paper(repo / "lab")
    board.mkdir(parents=True)
    yaml = _multi_yaml(
        [("elsewhere", "hdd", f"{board}/"), ("lab", "hdd", "lab/")], default="lab",
    )
    _init_repo(repo, yaml)
    _registry(repo, [], monkeypatch, caplog)
    assert (repo / "research" / "REGISTRY.md").exists(), "default in-repo HDD board ignored"
    assert not (board / "REGISTRY.md").exists(), "registry written under a non-default board"


# --- controls: multi-board in-repo HDD board; explicit --output -------------------


@pytest.fixture
def multi_inside(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _paper(repo / "lab")
    (repo / "work").mkdir(parents=True)
    yaml = _multi_yaml(
        [("dev", "software", "work/"), ("research", "hdd", "lab/")], default="dev",
    )
    return _init_repo(repo, yaml)


def test_control_multi_inside_registry_in_research(multi_inside, monkeypatch, caplog):
    repo = multi_inside
    seen, _ = _registry(repo, [], monkeypatch, caplog)
    out = repo / "research" / "REGISTRY.md"
    assert out.exists(), "in-repo HDD board: research/REGISTRY.md not written"
    assert PAPER_ID in out.read_text(), out.read_text()
    assert not (repo / "lab" / "REGISTRY.md").exists()
    assert OUTSIDE_NOTE not in seen, seen


def test_control_multi_inside_push_commits(multi_inside, monkeypatch, caplog):
    repo = multi_inside
    before = _commits(repo)
    seen, _ = _registry(repo, ["--push"], monkeypatch, caplog)
    assert _commits(repo) == before + 1, f"in-repo --push did not commit:\n{seen}"
    assert "research/REGISTRY.md" in _git(repo, "show", "--name-only", "HEAD")
    assert OUTSIDE_NOTE not in seen, seen


def test_control_multi_outside_explicit_output(multi_outside, tmp_path, monkeypatch, caplog):
    repo, board = multi_outside
    target = tmp_path / "custom" / "MY-REGISTRY.md"
    _registry(repo, ["--output", str(target)], monkeypatch, caplog)
    assert target.exists(), "--output ignored"
    assert PAPER_ID in target.read_text(), target.read_text()
    assert not (board / "REGISTRY.md").exists(), "default written despite --output"
    assert not (repo / "research" / "REGISTRY.md").exists(), "repo default written"


# --- 2. one definition of "outside": the git toplevel ----------------------------


@pytest.fixture
def subdir_sibling(tmp_path: Path) -> tuple[Path, Path, Path]:
    """git at tmp/top; `.kanban/` in top/sub; board root `../sibling/` = top/sibling
    (outside repo_root, inside the git toplevel)."""
    top = tmp_path / "top"
    repo, sibling = top / "sub", top / "sibling"
    _paper(sibling)
    _init_repo(repo, _single_yaml("../sibling/"), git_top=top)
    return top, repo, sibling


def test_subdir_sibling_inside_git_registry_stays_with_repo(
    subdir_sibling, monkeypatch, caplog,
):
    top, repo, sibling = subdir_sibling
    seen, _ = _registry(repo, [], monkeypatch, caplog)
    out = repo / "research" / "REGISTRY.md"
    assert out.exists(), (
        "root inside the git toplevel: registry must default to repo_root/research/"
    )
    assert PAPER_ID in out.read_text(), out.read_text()
    assert not (sibling / "REGISTRY.md").exists(), "registry written under the sibling root"
    assert OUTSIDE_NOTE not in seen, seen


def test_subdir_sibling_push_commits(subdir_sibling, monkeypatch, caplog):
    top, repo, _sibling = subdir_sibling
    before = _commits(top)
    seen, _ = _registry(repo, ["--push"], monkeypatch, caplog)
    assert _commits(top) == before + 1, f"--push did not commit an in-git registry:\n{seen}"
    assert "sub/research/REGISTRY.md" in _git(top, "show", "--name-only", "HEAD")
    assert OUTSIDE_NOTE not in seen, seen


def test_control_plain_outside_still_under_board_root(tmp_path, monkeypatch, caplog):
    """#192 control: a root outside the git toplevel still keeps the registry."""
    repo, board = tmp_path / "repo", (tmp_path / "board").resolve()
    _paper(board)
    _init_repo(repo, _single_yaml(f"{board}/"))
    _registry(repo, [], monkeypatch, caplog)
    assert (board / "REGISTRY.md").exists()
    assert not (repo / "research" / "REGISTRY.md").exists()


# --- 3. normalised path in the message --------------------------------------------


def test_message_path_normalised(tmp_path, monkeypatch, caplog):
    repo, board = tmp_path / "repo", tmp_path / "board"
    _paper(board)
    _init_repo(repo, _single_yaml("../board/"))
    _seen, unwrapped = _registry(repo, [], monkeypatch, caplog)
    out = board / "REGISTRY.md"
    assert out.exists(), "registry not written under the ../board/ root"
    assert "Registry written to" in unwrapped, unwrapped
    assert f"{os.sep}..{os.sep}" not in unwrapped, f"un-normalised path printed:\n{unwrapped}"
    expected = {os.path.normpath(out), str(out.resolve())}
    assert any(p in unwrapped for p in expected), (
        f"normalised path {expected} not printed:\n{unwrapped}"
    )
