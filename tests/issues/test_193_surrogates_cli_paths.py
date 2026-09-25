"""Issue #193 — the CLI paths #172 left: a lone surrogate in user text must be
refused cleanly, and nothing written or committed.

Python decodes undecodable argv bytes with surrogateescape (b"\\xff" -> "\\udcff").
#172 made `create` refuse such text. The paths below still either show a raw
ValueError traceback (epic/voyage create, the HDD create commands) or write and
commit the surrogate and crash later on display (rank --summary, next-id,
experiment run, paper --authors).

Decided behaviour: every one of them exits non-zero with a message containing
"invalid UTF-8", prints no "Traceback", and leaves the repo byte-for-byte as it
was (item files, config.yaml, _ID_ALLOCATIONS.json, run folders, git log).
Controls: the same commands with valid non-ASCII text succeed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

MSG = "invalid UTF-8"
BAD = b"a\xffb"  # undecodable argv bytes -> "a\udcffb" under surrogateescape
GOOD = "café ☕"
SRC = Path(__file__).resolve().parents[2] / "src"

# Every case runs the real CLI in a subprocess, never CliRunner: today these paths
# leave the unencodable text in cli.py's module-level rich Console buffer, which
# then poisons every later in-process CliRunner run in the whole suite.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _run(repo: Path, *args: str | bytes) -> tuple[int, str]:
    """Run the real CLI in a subprocess, with argv given as raw bytes."""
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONIOENCODING", None)
    argv = [a if isinstance(a, bytes) else a.encode("utf-8") for a in args]
    proc = subprocess.run(
        [
            os.fsencode(sys.executable),
            b"-c",
            b"from yurtle_kanban.cli import main; main()",
            *argv,
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        timeout=120,
    )
    return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")


def _snapshot(repo: Path) -> tuple[dict[str, bytes], str]:
    """Every file outside .git with its bytes, plus the commit count."""
    files = {
        str(p.relative_to(repo)): p.read_bytes()
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }
    return files, _git(repo, "rev-list", "--count", "HEAD").strip()


def _assert_refused(repo: Path, before: tuple[dict[str, bytes], str], code: int, out: str) -> None:
    assert code != 0, out
    assert "Traceback" not in out, out
    assert MSG in out, out
    files, commits = _snapshot(repo)
    assert commits == before[1], f"a commit was made:\n{out}"
    added = sorted(set(files) - set(before[0]))
    changed = sorted(k for k in set(files) & set(before[0]) if files[k] != before[0][k])
    removed = sorted(set(before[0]) - set(files))
    assert (added, changed, removed) == ([], [], []), f"tree changed:\n{out}"


def _init_repo(path: Path, theme: str) -> Path:
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(path, *args)
    code, out = _run(path, "init", "--theme", theme)
    assert code == 0, out
    _commit_all(path, "init")
    return path


@pytest.fixture
def sw(tmp_path: Path) -> Path:
    """A software-theme board with one committed feature, FEAT-001."""
    repo = _init_repo(tmp_path, "software")
    code, out = _run(repo, "create", "feature", "Existing item")
    assert code == 0, out
    _commit_all(repo, "seed")
    return repo


@pytest.fixture
def hdd(tmp_path: Path) -> Path:
    """An HDD-theme board with one committed experiment, EXPR-001."""
    repo = _init_repo(tmp_path, "hdd")
    code, out = _run(repo, "experiment", "create", "--title", "Seed experiment")
    assert code == 0, out
    _commit_all(repo, "seed")
    return repo


# ---------------------------------------------------------------------------
# epic / voyage create — today: raw ValueError traceback
# ---------------------------------------------------------------------------

EPIC_CMDS = [["epic", "create"], ["voyage", "create"]]


class TestEpicCreate:
    @pytest.mark.parametrize("cmd", EPIC_CMDS, ids=["epic", "voyage"])
    @pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
    def test_bad_title_refused(self, sw: Path, cmd: list[str], push: bool) -> None:
        before = _snapshot(sw)
        code, out = _run(sw, *cmd, BAD, *(["--push"] if push else []))
        _assert_refused(sw, before, code, out)

    @pytest.mark.parametrize("cmd", EPIC_CMDS, ids=["epic", "voyage"])
    def test_control_valid_non_ascii(self, sw: Path, cmd: list[str]) -> None:
        code, out = _run(sw, *cmd, GOOD)
        assert code == 0, out
        assert "Traceback" not in out, out
        assert any(GOOD in p.read_text("utf-8") for p in sw.rglob("EPIC-*.md")), out


# ---------------------------------------------------------------------------
# HDD create commands — today: raw ValueError traceback
# ---------------------------------------------------------------------------


def _hdd_create(text: bytes | str, field: str = "title") -> dict[str, list[str | bytes]]:
    """argv per HDD create command, with `text` in the title (or the named option)."""
    t = text if field == "title" else "Plain title"
    return {
        "idea": ["idea", "create", t],
        "literature": ["literature", "create", t],
        "paper": ["paper", "create", "130", t],
        "hypothesis": ["hypothesis", "create", t],
        "experiment": ["experiment", "create", "--title", t],
        "measure": ["measure", "create", t, "--unit", "percent", "--category", "accuracy"],
    }


HDD_KINDS = ["idea", "literature", "paper", "hypothesis", "experiment", "measure"]
HDD_OPTIONS = [
    ("paper", ["paper", "create", "131", "Plain title", "--authors"]),
    ("hypothesis", ["hypothesis", "create", "Plain statement", "--target"]),
    ("measure-unit", ["measure", "create", "Plain", "--category", "accuracy", "--unit"]),
    ("measure-category", ["measure", "create", "Plain", "--unit", "percent", "--category"]),
]


class TestHddCreate:
    @pytest.mark.parametrize("kind", HDD_KINDS)
    @pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
    def test_bad_title_refused(self, hdd: Path, kind: str, push: bool) -> None:
        before = _snapshot(hdd)
        code, out = _run(hdd, *_hdd_create(BAD)[kind], *(["--push"] if push else []))
        _assert_refused(hdd, before, code, out)

    @pytest.mark.parametrize(("name", "argv"), HDD_OPTIONS, ids=[n for n, _ in HDD_OPTIONS])
    def test_bad_option_refused(self, hdd: Path, name: str, argv: list[str]) -> None:
        before = _snapshot(hdd)
        code, out = _run(hdd, *argv, BAD)
        _assert_refused(hdd, before, code, out)

    @pytest.mark.parametrize("kind", HDD_KINDS)
    def test_control_valid_non_ascii_title(self, hdd: Path, kind: str) -> None:
        code, out = _run(hdd, *_hdd_create(GOOD)[kind])
        assert code == 0, out
        assert "Traceback" not in out, out
        assert GOOD in out, out

    @pytest.mark.parametrize(("name", "argv"), HDD_OPTIONS, ids=[n for n, _ in HDD_OPTIONS])
    def test_control_valid_non_ascii_option(self, hdd: Path, name: str, argv: list[str]) -> None:
        code, out = _run(hdd, *argv, GOOD)
        assert code == 0, out
        assert "Traceback" not in out, out


# ---------------------------------------------------------------------------
# rank --summary — today: writes + commits, then crashes printing it
# ---------------------------------------------------------------------------


class TestRankSummary:
    @pytest.mark.parametrize("no_commit", [False, True], ids=["commit", "no-commit"])
    def test_bad_summary_refused(self, sw: Path, no_commit: bool) -> None:
        before = _snapshot(sw)
        extra = ["--no-commit"] if no_commit else []
        code, out = _run(sw, "rank", "FEAT-001", "1", "--summary", BAD, *extra)
        _assert_refused(sw, before, code, out)

    def test_control_valid_non_ascii(self, sw: Path) -> None:
        code, out = _run(sw, "rank", "FEAT-001", "1", "--summary", GOOD)
        assert code == 0, out
        assert "Traceback" not in out, out
        assert any(GOOD in p.read_text("utf-8") for p in sw.rglob("FEAT-001*.md")), out


# ---------------------------------------------------------------------------
# next-id <prefix> — today: writes + commits _ID_ALLOCATIONS.json, then crashes
# ---------------------------------------------------------------------------


class TestNextId:
    @pytest.mark.parametrize(
        "extra",
        [["--no-sync"], ["--no-sync", "--no-commit"], ["--no-sync", "--json"]],
        ids=["commit", "no-commit", "json"],
    )
    def test_bad_prefix_refused(self, sw: Path, extra: list[str]) -> None:
        before = _snapshot(sw)
        code, out = _run(sw, "next-id", BAD, *extra)
        _assert_refused(sw, before, code, out)

    def test_control_valid_non_ascii(self, sw: Path) -> None:
        code, out = _run(sw, "next-id", "CAFÉ", "--no-sync")
        assert code == 0, out
        assert "Traceback" not in out, out
        assert "CAFÉ-" in out, out


# ---------------------------------------------------------------------------
# experiment run --being/--params/--run-by — today: surrogate stored in config.yaml
# ---------------------------------------------------------------------------

RUN_CASES = [
    ("being", lambda v: ["--being", v]),
    ("params-value", lambda v: ["--being", "b1", "--params", b"k=" + v]),
    ("params-key", lambda v: ["--being", "b1", "--params", v + b"=1"]),
    ("run-by", lambda v: ["--being", "b1", "--run-by", v]),
]


class TestExperimentRun:
    @pytest.mark.parametrize(("name", "opts"), RUN_CASES, ids=[n for n, _ in RUN_CASES])
    def test_bad_value_refused(self, hdd: Path, name: str, opts) -> None:
        before = _snapshot(hdd)
        code, out = _run(hdd, "experiment", "run", "EXPR-001", *opts(BAD))
        _assert_refused(hdd, before, code, out)

    @pytest.mark.parametrize(("name", "opts"), RUN_CASES, ids=[n for n, _ in RUN_CASES])
    def test_control_valid_non_ascii(self, hdd: Path, name: str, opts) -> None:
        code, out = _run(hdd, "experiment", "run", "EXPR-001", *opts(GOOD.encode("utf-8")))
        assert code == 0, out
        assert "Traceback" not in out, out
        code, out = _run(hdd, "experiment", "status", "EXPR-001")
        assert code == 0, out
        assert "Traceback" not in out, out
