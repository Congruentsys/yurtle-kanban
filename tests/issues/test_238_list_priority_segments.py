"""#238: `list --priority` drops empty comma segments before validating, and
reports each invalid value on its own (with `unknown_priority_message`'s rule),
never the joined list. Before the fix `x,` printed `Unknown priority: 'x, '`,
`,` printed `', '`, and `high,,low` was refused with `''`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

VALID = "valid: critical, high, medium, low"

ITEMS = {
    "EXP-001": "critical",
    "EXP-002": "high",
    "EXP-003": "medium",
    "EXP-004": "low",
}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init", "-b", "main")
    git("config", "user.email", "test@test.com")
    git("config", "user.name", "Test")
    (tmp_path / ".kanban").mkdir()
    exp_dir = tmp_path / "kanban-work" / "expeditions"
    exp_dir.mkdir(parents=True)
    KanbanConfig(
        theme="nautical",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/expeditions/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    for item_id, prio in ITEMS.items():
        (exp_dir / f"{item_id}-{prio}.md").write_text(
            f"---\nid: {item_id}\ntitle: {prio} item\nstatus: backlog\npriority: {prio}\n---\n"
        )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run_list(value: str, *extra: str) -> Result:
    return CliRunner().invoke(main, ["list", "--priority", value, *extra])


def collapsed(text: str) -> str:
    return " ".join(text.split())


def listed_ids(result: Result) -> set[str]:
    assert result.exit_code == 0, result.output
    return {d["id"] for d in json.loads(result.output)}


class TestEmptySegmentsDropped:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("high,,low", {"EXP-002", "EXP-004"}),
            ("high,", {"EXP-002"}),
            (",high", {"EXP-002"}),
            ("high, ,low", {"EXP-002", "EXP-004"}),
        ],
        ids=["double-comma", "trailing-comma", "leading-comma", "blank-middle"],
    )
    def test_filters_on_remaining_values(
        self, repo: Path, value: str, expected: set[str]
    ) -> None:
        assert listed_ids(run_list(value, "--json")) == expected


class TestOnlyEmptySegmentsRefused:
    @pytest.mark.parametrize("value", [",", ",,", " , "], ids=["comma", "commas", "blank"])
    def test_no_valid_priority_given(self, repo: Path, value: str) -> None:
        result = run_list(value)
        assert result.exit_code == 1, result.output
        assert isinstance(result.exception, SystemExit)
        assert VALID in result.output
        # no quoted joined list or quoted empty value: nothing was typed to quote
        assert "'" not in result.output
        assert "No work items found" not in result.output


class TestEachInvalidValueReportedSeparately:
    def test_trailing_comma_names_only_the_value(self, repo: Path) -> None:
        result = run_list("x,")
        assert result.exit_code == 1, result.output
        assert f"Unknown priority: x; {VALID}" in result.output
        assert "'x, '" not in result.output
        assert "x, " not in result.output

    def test_two_invalid_values_both_named(self, repo: Path) -> None:
        result = run_list("urgent,bogus")
        assert result.exit_code == 1, result.output
        out = collapsed(result.output)
        assert f"Unknown priority: urgent; {VALID}" in out
        assert f"Unknown priority: bogus; {VALID}" in out
        assert "urgent, bogus" not in out

    def test_control_char_value_gets_its_own_repr(self, repo: Path) -> None:
        result = run_list("a\x1bb,urgent")
        assert result.exit_code == 1, result.output
        lines = [collapsed(line) for line in result.output.splitlines()]
        assert f"Unknown priority: 'a\\x1bb'; {VALID}" in lines
        assert f"Unknown priority: urgent; {VALID}" in lines
        assert "\x1b" not in result.output

    def test_invalid_with_empty_segment_names_only_the_value(self, repo: Path) -> None:
        result = run_list("high,,urgent")
        assert result.exit_code == 1, result.output
        assert f"Unknown priority: urgent; {VALID}" in result.output
        assert "''" not in result.output


class TestControls:
    def test_single_priority(self, repo: Path) -> None:
        assert listed_ids(run_list("high", "--json")) == {"EXP-002"}

    def test_case_insensitive(self, repo: Path) -> None:
        assert listed_ids(run_list("HIGH", "--json")) == {"EXP-002"}

    def test_comma_list(self, repo: Path) -> None:
        assert listed_ids(run_list("critical,high", "--json")) == {"EXP-001", "EXP-002"}

    def test_single_invalid_raw(self, repo: Path) -> None:
        result = run_list("urgent")
        assert result.exit_code == 1, result.output
        assert f"Unknown priority: urgent; {VALID}" in result.output
