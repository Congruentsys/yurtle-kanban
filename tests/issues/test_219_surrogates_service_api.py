"""Issue #219 — lone surrogates through the service API and MCP.

#193 made the CLI refuse undecodable argv at the root group, but the service still
accepts a lone surrogate (what surrogateescape makes of undecodable bytes) on the
paths #193 listed: rank_item(value_summary), allocate_next_id(prefix) and
create_experiment_run(being, params keys/values, run_by). check_encodable also
ignores tuples.

Decided behaviour: each of those service calls raises a ValueError naming the
field and containing "invalid UTF-8" before anything is written or committed (the
tree stays byte-identical and the git log does not grow). MCP tools that reach them
return {"error": ...} with that message. Valid non-ASCII text is still accepted.

The HDD-specific fields (authors, target, unit, category) have no service
parameters: they are template variables rendered into `content=`, so the service
must refuse them on the render + create path the HDD commands use (real templates):
the #172 content check catches target/unit/category, but the paper template
JSON-escapes authors into a literal `\\udcff`, which slips past it.
MCP exposes no rank or experiment-run tool, so only kanban_next_id is tested there.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType, check_encodable
from yurtle_kanban.service import KanbanService

LONE = "a\udcffb"  # what surrogateescape yields for argv bytes b"a\xffb"
MSG = "invalid UTF-8"
GOOD = "café ☕"
SRC = Path(__file__).resolve().parents[2] / "src"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

Snapshot = tuple[dict[str, bytes], str]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _cli(repo: Path, *args: str) -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", "from yurtle_kanban.cli import main; main()", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _snapshot(repo: Path) -> Snapshot:
    """Every file outside .git with its bytes, plus the commit count."""
    files = {
        str(p.relative_to(repo)): p.read_bytes()
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }
    return files, _git(repo, "rev-list", "--count", "HEAD").strip()


def _assert_unchanged(repo: Path, before: Snapshot) -> None:
    files, commits = _snapshot(repo)
    assert commits == before[1], "a commit was made"
    added = sorted(set(files) - set(before[0]))
    changed = sorted(k for k in set(files) & set(before[0]) if files[k] != before[0][k])
    removed = sorted(set(before[0]) - set(files))
    assert (added, changed, removed) == ([], [], []), "tree changed"


def _init_repo(path: Path, theme: str) -> Path:
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(path, *args)
    _cli(path, "init", "--theme", theme)
    _commit_all(path, "init")
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    return path


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _mcp(repo: Path) -> Any:
    mcp_server = pytest.importorskip("yurtle_kanban.mcp.server")
    return mcp_server.KanbanMCPServer(repo_root=repo)


@pytest.fixture
def sw(tmp_path: Path) -> Path:
    """A software-theme board with one committed feature, FEAT-001."""
    repo = _init_repo(tmp_path, "software")
    _service(repo).create_item(WorkItemType.FEATURE, "Existing item", description="Body")
    _commit_all(repo, "seed")
    return repo


@pytest.fixture
def hdd(tmp_path: Path) -> Path:
    """An HDD-theme board with one committed experiment, EXPR-001."""
    repo = _init_repo(tmp_path, "hdd")
    _cli(repo, "experiment", "create", "--title", "Seed experiment")
    _commit_all(repo, "seed")
    return repo


def _refused(repo: Path, fn: Any, *fields: str) -> None:
    """fn() raises ValueError with MSG naming one of `fields`; the repo is untouched."""
    before = _snapshot(repo)
    with pytest.raises(ValueError) as exc:
        fn()
    assert MSG in str(exc.value)
    assert any(f in str(exc.value) for f in fields), str(exc.value)
    _assert_unchanged(repo, before)


# ---------------------------------------------------------------------------
# check_encodable looks inside tuples
# ---------------------------------------------------------------------------


class TestCheckEncodable:
    def test_tuple_checked(self) -> None:
        with pytest.raises(ValueError, match=MSG):
            check_encodable("f", ("ok", LONE))  # type: ignore[arg-type]

    def test_tuple_names_field(self) -> None:
        with pytest.raises(ValueError, match="f"):
            check_encodable("f", ("ok", LONE))  # type: ignore[arg-type]

    def test_list_still_checked(self) -> None:
        with pytest.raises(ValueError, match=MSG):
            check_encodable("f", ["ok", LONE])

    def test_good_tuple_accepted(self) -> None:
        check_encodable("f", ("ok", GOOD))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# rank_item(value_summary)
# ---------------------------------------------------------------------------


class TestRankItem:
    @pytest.mark.parametrize("commit", [False, True], ids=["no-commit", "commit"])
    def test_bad_summary_refused(self, sw: Path, commit: bool) -> None:
        svc = _service(sw)
        _refused(
            sw,
            lambda: svc.rank_item("FEAT-001", 1, value_summary=LONE, commit=commit),
            "value_summary",
        )

    def test_good_summary_accepted(self, sw: Path) -> None:
        svc = _service(sw)
        svc.rank_item("FEAT-001", 1, value_summary=GOOD, commit=True)
        item = _service(sw).get_item("FEAT-001")
        assert item is not None
        assert item.value_summary == GOOD


# ---------------------------------------------------------------------------
# allocate_next_id(prefix)
# ---------------------------------------------------------------------------


class TestAllocateNextId:
    @pytest.mark.parametrize("commit", [False, True], ids=["no-commit", "commit"])
    def test_bad_prefix_refused(self, sw: Path, commit: bool) -> None:
        svc = _service(sw)
        _refused(
            sw,
            lambda: svc.allocate_next_id(LONE, sync_remote=False, commit_allocation=commit),
            "prefix",
        )

    def test_good_prefix_accepted(self, sw: Path) -> None:
        result = _service(sw).allocate_next_id(GOOD, sync_remote=False, commit_allocation=True)
        assert result["success"], result
        assert result["id"].startswith(GOOD.upper())


# ---------------------------------------------------------------------------
# create_experiment_run(being, params, run_by)
# ---------------------------------------------------------------------------

RUN_CASES = {
    "being": ({"being": LONE}, "being"),
    "param-key": ({"being": "b", "params": {LONE: "x"}}, "params"),
    "param-value": ({"being": "b", "params": {"k": LONE}}, "params"),
    "run_by": ({"being": "b", "run_by": LONE}, "run_by"),
}


class TestExperimentRun:
    @pytest.mark.parametrize("case", list(RUN_CASES), ids=list(RUN_CASES))
    def test_bad_value_refused(self, hdd: Path, case: str) -> None:
        kwargs, field = RUN_CASES[case]
        svc = _service(hdd)
        _refused(hdd, lambda: svc.create_experiment_run("EXPR-001", **kwargs), field)

    def test_good_values_accepted(self, hdd: Path) -> None:
        run = _service(hdd).create_experiment_run(
            "EXPR-001", being=GOOD, params={GOOD: GOOD}, run_by=GOOD
        )
        assert (run / "config.yaml").is_file()
        runs = _service(hdd).get_experiment_runs("EXPR-001")
        assert runs[0]["being"] == GOOD
        assert runs[0]["run_by"] == GOOD
        assert runs[0]["params"] == {GOOD: GOOD}


# ---------------------------------------------------------------------------
# HDD fields (authors, target, unit, category): rendered into content=
# ---------------------------------------------------------------------------

HDD_CASES = {
    "paper-authors": (
        WorkItemType.PAPER,
        "paper",
        "PAPER-900",
        {"id": "PAPER-900", "title": "P", "number": 900, "authors": LONE},
    ),
    "hypothesis-target": (
        WorkItemType.HYPOTHESIS,
        "hypothesis",
        "H900.1",
        {"id": "H900.1", "title": "H", "paper": "PAPER-900", "target": LONE},
    ),
    "measure-unit": (
        WorkItemType.MEASURE,
        "measure",
        "M-900",
        {"id": "M-900", "title": "M", "unit": LONE, "category": "accuracy"},
    ),
    "measure-category": (
        WorkItemType.MEASURE,
        "measure",
        "M-900",
        {"id": "M-900", "title": "M", "unit": "percent", "category": LONE},
    ),
}


def _render(item_type: str, variables: dict[str, Any]) -> str:
    from yurtle_kanban.cli import _get_templates_dir
    from yurtle_kanban.template_engine import TemplateEngine

    return TemplateEngine(_get_templates_dir()).render("hdd", item_type, variables)


class TestHddFields:
    @pytest.mark.parametrize("case", list(HDD_CASES), ids=list(HDD_CASES))
    @pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
    def test_bad_field_refused(self, hdd: Path, case: str, push: bool) -> None:
        """Render + create, as the HDD commands do: refused at either step. The paper
        template JSON-escapes authors to a literal `\\udcff` (valid UTF-8 text that
        YAML reads back as the surrogate), so the content check alone misses it."""
        wtype, tname, item_id, variables = HDD_CASES[case]
        field = case.split("-")[1]
        svc = _service(hdd)
        create = svc.create_item_and_push if push else svc.create_item

        def run() -> None:
            content = _render(tname, variables)
            create(wtype, "Title", content=content, item_id=item_id)

        _refused(hdd, run, field, "content")


# ---------------------------------------------------------------------------
# MCP: kanban_next_id (no rank or experiment-run tool exists)
# ---------------------------------------------------------------------------


class TestMcp:
    def test_next_id_bad_prefix(self, sw: Path) -> None:
        before = _snapshot(sw)
        result = _mcp(sw).handle_tool_call("kanban_next_id", {"prefix": LONE, "sync_remote": False})
        assert "error" in result, result
        assert MSG in result["error"]
        _assert_unchanged(sw, before)

    def test_next_id_good_prefix(self, sw: Path) -> None:
        result = _mcp(sw).handle_tool_call("kanban_next_id", {"prefix": GOOD, "sync_remote": False})
        assert "error" not in result, result
        assert result["success"], result

    def test_no_rank_or_run_tool(self, sw: Path) -> None:
        """If MCP grows these tools, they need #219 tests too."""
        names = {t["name"] for t in _mcp(sw).get_tools()}
        assert not {n for n in names if "rank" in n or "run" in n}, names
