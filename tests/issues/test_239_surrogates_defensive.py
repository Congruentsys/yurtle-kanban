"""Issue #239 — lone surrogates, defensive follow-ups to #219.

The CLI root argv guard (#193) covers these paths today, so they are defence in
depth for future API callers.

Decided behaviour:
- move_item(assignee, message, closed_by), update_run_status(status, outcome) and
  update_parent_turtle_block(child_id) refuse a lone surrogate with a ValueError
  naming the field and containing "invalid UTF-8", before anything is written or
  committed (tree byte-identical, git log does not grow).
- check_encodable recurses into nested containers (dict values, dict keys, nested
  lists); non-str leaves are fine.
- check_encodable raises a dedicated InvalidText(ValueError), importable from
  yurtle_kanban.models. epic create and the HDD create commands turn exactly that
  type into a clean error (no traceback, non-zero exit) when render refuses a
  template variable. A plain ValueError from render still propagates (#183).

The root guard blocks every argv path through `main`, so the command-level tests
invoke the `epic` / HDD subgroups directly with CliRunner, which skips the guard
and hands the lone surrogate straight to the command.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
import pytest
import yaml
from click.testing import CliRunner

from tests.issues._snapshot import files_outside_git
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus, WorkItemType, check_encodable
from yurtle_kanban.service import KanbanService

LONE = "a\udcffb"  # what surrogateescape yields for argv bytes b"a\xffb"
MSG = "invalid UTF-8"
GOOD = "café ☕"
SRC = Path(__file__).resolve().parents[2] / "src"

# ---------------------------------------------------------------------------
# Helpers (same shape as tests/issues/test_219_surrogates_service_api.py)
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
    return files_outside_git(repo), _git(repo, "rev-list", "--count", "HEAD").strip()


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


@pytest.fixture
def sw(tmp_path: Path) -> Path:
    """A software-theme board with one committed feature, FEAT-001."""
    repo = _init_repo(tmp_path, "software")
    _service(repo).create_item(WorkItemType.FEATURE, "Existing item", description="Body")
    _commit_all(repo, "seed")
    return repo


@pytest.fixture
def hdd(tmp_path: Path) -> Path:
    """An HDD-theme board with a committed experiment (EXPR-001) and idea (IDEA-R-001)."""
    repo = _init_repo(tmp_path, "hdd")
    _cli(repo, "experiment", "create", "--title", "Seed experiment")
    _cli(repo, "idea", "create", "Seed idea")
    _commit_all(repo, "seed")
    return repo


def _refused(repo: Path, fn: Any, field: str) -> None:
    """fn() raises ValueError with MSG naming `field`; the repo is untouched."""
    before = _snapshot(repo)
    with pytest.raises(ValueError) as exc:
        fn()
    assert MSG in str(exc.value), str(exc.value)
    assert field in str(exc.value), str(exc.value)
    _assert_unchanged(repo, before)


# ---------------------------------------------------------------------------
# move_item(assignee, message, closed_by)
# ---------------------------------------------------------------------------

MOVE_FIELDS = ["assignee", "message", "closed_by"]


def _move(repo: Path, commit: bool, **kwargs: Any) -> Any:
    return _service(repo).move_item(
        "FEAT-001",
        WorkItemStatus.IN_PROGRESS,
        commit=commit,
        validate_workflow=False,
        skip_wip_check=True,
        skip_gates=True,
        **kwargs,
    )


class TestMoveItem:
    @pytest.mark.parametrize("commit", [False, True], ids=["no-commit", "commit"])
    @pytest.mark.parametrize("field", MOVE_FIELDS)
    def test_bad_value_refused(self, sw: Path, field: str, commit: bool) -> None:
        _refused(sw, lambda: _move(sw, commit, **{field: LONE}), field)

    def test_good_values_accepted(self, sw: Path) -> None:
        pr = "https://example.com/pr/café"  # closed_by is a URI: no spaces (see move_item)
        item = _move(sw, True, assignee=GOOD, message=f"move {GOOD}", closed_by=pr)
        assert item.status == WorkItemStatus.IN_PROGRESS
        reread = _service(sw).get_item("FEAT-001")
        assert reread is not None
        assert reread.assignee == GOOD
        assert _git(sw, "log", "-1", "--format=%B").strip() == f"move {GOOD}"
        assert f"<{pr}>" in reread.file_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# update_run_status(status, outcome)
# ---------------------------------------------------------------------------


class TestUpdateRunStatus:
    @pytest.mark.parametrize("field", ["status", "outcome"])
    def test_bad_value_refused(self, hdd: Path, field: str) -> None:
        svc = _service(hdd)
        run = svc.create_experiment_run("EXPR-001", being="b", run_by="r")
        kwargs = {"status": "complete", "outcome": "VALIDATED", field: LONE}
        _refused(hdd, lambda: svc.update_run_status(run, **kwargs), field)

    def test_good_values_accepted(self, hdd: Path) -> None:
        svc = _service(hdd)
        run = svc.create_experiment_run("EXPR-001", being="b", run_by="r")
        svc.update_run_status(run, status=GOOD, outcome=GOOD)
        data = yaml.safe_load((run / "config.yaml").read_text(encoding="utf-8"))
        assert data["status"] == GOOD
        assert data["outcome"] == GOOD


# ---------------------------------------------------------------------------
# update_parent_turtle_block(child_id)
# ---------------------------------------------------------------------------


class TestUpdateParentTurtleBlock:
    @pytest.mark.parametrize("push", [False, True], ids=["no-push", "push"])
    def test_bad_child_id_refused(self, hdd: Path, push: bool) -> None:
        svc = _service(hdd)
        _refused(
            hdd,
            lambda: svc.update_parent_turtle_block("IDEA-R-001", "literature", LONE, push=push),
            "child_id",
        )

    def test_good_child_id_accepted(self, hdd: Path) -> None:
        svc = _service(hdd)
        assert svc.update_parent_turtle_block("IDEA-R-001", "literature", "LIT-001")
        idea = next((hdd / "research").rglob("IDEA-R-001*.md"))
        assert "LIT-001" in idea.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# check_encodable: nested containers + InvalidText
# ---------------------------------------------------------------------------

NESTED_BAD = {
    "dict-in-dict": {"k": {"x": LONE}},
    "list-in-list": ["ok", ["a", LONE]],
    "dict-key": {LONE: 1},
    "dict-value": {"k": LONE},
    "list-in-dict": {"k": ["ok", LONE]},
    "dict-in-list": ["ok", {"k": LONE}],
    "tuple-in-list": [("ok", LONE)],
}

NESTED_GOOD = {
    "dict": {"k": {"x": GOOD}, GOOD: [GOOD, 1, None]},
    "list": ["ok", [GOOD, 2, None, 3.5, True]],
    "leaves": [1, None, {"n": 0}],
}


class TestCheckEncodableNested:
    @pytest.mark.parametrize("case", list(NESTED_BAD), ids=list(NESTED_BAD))
    def test_nested_bad_refused(self, case: str) -> None:
        with pytest.raises(ValueError) as exc:
            check_encodable("field_x", NESTED_BAD[case])
        assert MSG in str(exc.value)
        assert "field_x" in str(exc.value)

    @pytest.mark.parametrize("case", list(NESTED_GOOD), ids=list(NESTED_GOOD))
    def test_nested_good_accepted(self, case: str) -> None:
        check_encodable("f", NESTED_GOOD[case])

    @pytest.mark.parametrize("value", [1, None, 2.5, True])
    def test_non_str_scalar_accepted(self, value: object) -> None:
        check_encodable("f", value)


class TestInvalidText:
    def test_exists_and_is_value_error(self) -> None:
        from yurtle_kanban import models

        assert hasattr(models, "InvalidText"), "models.InvalidText missing"
        assert issubclass(models.InvalidText, ValueError)

    @pytest.mark.parametrize(
        "value", [LONE, ["ok", LONE], {"k": {"x": LONE}}], ids=["str", "list", "nested"]
    )
    def test_check_encodable_raises_it(self, value: object) -> None:
        from yurtle_kanban import models

        assert hasattr(models, "InvalidText"), "models.InvalidText missing"
        with pytest.raises(models.InvalidText, match=MSG):
            check_encodable("f", value)


# ---------------------------------------------------------------------------
# Render refusals reach the CLI as a clean error
# ---------------------------------------------------------------------------


def _assert_clean_error(result: Any) -> None:
    crashed = result.exception is not None and not isinstance(result.exception, SystemExit)
    assert not crashed, (result.output, repr(result.exception))
    assert result.exit_code != 0, result.output
    assert "Traceback" not in result.output
    assert MSG in result.output, result.output


def _invoke(repo: Path, monkeypatch: pytest.MonkeyPatch, group: Any, args: list[str]) -> Any:
    monkeypatch.chdir(repo)
    return CliRunner().invoke(group, args)


class TestRenderRefusalIsCleanCliError:
    def test_epic_create(self, sw: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from yurtle_kanban.epic_commands import epic

        before = _snapshot(sw)
        result = _invoke(sw, monkeypatch, epic, ["create", LONE])
        _assert_clean_error(result)
        _assert_unchanged(sw, before)

    HDD_CASES = {
        "idea-title": ("idea", ["create", LONE]),
        "paper-authors": ("paper", ["create", "900", "P", "--authors", LONE]),
        "experiment-title": ("experiment", ["create", "--title", LONE]),
    }

    @pytest.mark.parametrize("case", list(HDD_CASES), ids=list(HDD_CASES))
    def test_hdd_create(self, hdd: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
        from yurtle_kanban import hdd_commands

        group_name, args = self.HDD_CASES[case]
        before = _snapshot(hdd)
        result = _invoke(hdd, monkeypatch, getattr(hdd_commands, group_name), args)
        _assert_clean_error(result)
        _assert_unchanged(hdd, before)

    def test_control_good_epic_created(self, sw: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from yurtle_kanban.epic_commands import epic

        result = _invoke(sw, monkeypatch, epic, ["create", GOOD])
        assert result.exit_code == 0, (result.output, repr(result.exception))

    def test_control_plain_value_error_still_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#183: only the refusal types are softened; a real bug keeps its traceback."""
        from yurtle_kanban import hdd_commands
        from yurtle_kanban.cli import _get_templates_dir
        from yurtle_kanban.template_engine import TemplateEngine

        def boom(self: Any, *args: Any, **kwargs: Any) -> str:
            raise ValueError("boom")

        monkeypatch.setattr(TemplateEngine, "render", boom)
        engine = TemplateEngine(_get_templates_dir())
        with pytest.raises(ValueError, match="boom") as excinfo:
            hdd_commands._render(engine, "hdd", "idea", {})
        assert not isinstance(excinfo.value, click.ClickException)

    def test_control_plain_value_error_keeps_traceback_through_group(
        self, hdd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#183 through Group.invoke: only InvalidText is softened, so a plain
        ValueError from render reaches the caller as itself (its traceback kept)."""
        from yurtle_kanban.hdd_commands import idea
        from yurtle_kanban.template_engine import TemplateEngine

        def boom(self: Any, *args: Any, **kwargs: Any) -> str:
            raise ValueError("boom")

        monkeypatch.setattr(TemplateEngine, "render", boom)
        result = _invoke(hdd, monkeypatch, idea, ["create", "t"])
        assert type(result.exception) is ValueError, repr(result.exception)
        assert str(result.exception) == "boom"


# ---------------------------------------------------------------------------
# Self-referential containers never hang check_encodable (review of PR #253)
# ---------------------------------------------------------------------------

# Each case builds `value` in a child process; the child arms a 1 s alarm after
# its imports, so a hang kills it (and the parent's timeout is a backstop).
CYCLE_CASES = {
    "list-self": ('value = ["x"]; value.append(value)', "ok"),
    "list-self-bad": ("value = [LONE]; value.append(value)", "InvalidText"),
    "dict-self": ('value = {"k": "x"}; value["me"] = value', "ok"),
    "dict-self-bad": ('value = {"k": LONE}; value["me"] = value', "InvalidText"),
    "mutual": ('a = ["x"]; b = [a]; a.append(b); value = a', "ok"),
    "yaml-anchor": ('value = yaml.safe_load("&a [x, *a]")', "ok"),
    "yaml-anchor-bad": (
        'value = yaml.safe_load("&a [x, *a]"); value.insert(0, LONE)',
        "InvalidText",
    ),
    "yaml-anchor-only": ('value = yaml.safe_load("&a [*a]")', "ok"),
}

_CYCLE_CHILD = """\
import signal, sys, yaml
from yurtle_kanban.models import InvalidText, check_encodable
LONE = "a\\udcffb"
{build}
if hasattr(signal, "alarm"):
    signal.alarm(1)
try:
    check_encodable("f", value)
except InvalidText:
    print("InvalidText")
else:
    print("ok")
"""


def _run_child(code: str, *args: str, cwd: Path | None = None, timeout: float = 60) -> Any:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        return subprocess.run(
            [sys.executable, "-c", code, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"hung for {timeout}s")


class TestCyclicContainers:
    @pytest.mark.parametrize("case", list(CYCLE_CASES), ids=list(CYCLE_CASES))
    def test_cycle_terminates(self, case: str) -> None:
        build, expected = CYCLE_CASES[case]
        proc = _run_child(_CYCLE_CHILD.format(build=build))
        assert proc.returncode == 0, f"exit {proc.returncode} (alarm = hang): {proc.stderr}"
        assert proc.stdout.strip() == expected, proc.stdout + proc.stderr

    def test_hooks_anchor_cycle_does_not_hang_create(self, sw: Path) -> None:
        """The reviewer's repro: a hooks file whose create_item tags are a YAML cycle."""
        hooks = sw / ".kanban" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "kanban-hooks.yurtle.md").write_text(
            "---\n"
            "type: kanban-hooks\n"
            "version: 1\n"
            "hooks:\n"
            "  on_create:\n"
            "    - item_types: [feature]\n"
            "      actions:\n"
            "        - type: create_item\n"
            "          item_type: bug\n"
            '          title: "Follow-up"\n'
            "          tags: &a [x, *a]\n"
            "---\n"
            "# hooks\n",
            encoding="utf-8",
        )
        _commit_all(sw, "hooks")
        proc = _run_child(
            "from yurtle_kanban.cli import main; main()",
            "create",
            "feature",
            "Hello",
            cwd=sw,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert list(sw.rglob("FEAT-002*.md")), proc.stdout + proc.stderr
