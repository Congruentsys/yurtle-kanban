"""Issue #666 — refusals as a typed error; templated creates checked for `## Comments`.

Decided ([steer] on #666):
1. `InputRefused(ValueError)` in models.py (next to, or reusing, `InvalidText`), raised
   by `_check_text` and `_check_no_comments_heading`. CLI `create` catches only
   `InputRefused` and prints a clean `Error:` line; any other `ValueError` keeps its
   traceback.
2. HDD and epic creates run `_check_no_comments_heading` on the RENDERED content,
   outside fences, before anything is written.

Which user fields reach a templated body raw: with the shipped templates, none do.
The engine YAML-quotes `title:`/`target:`/`unit:`/`category:`, flattens the title
onto the H1 line (`" ".join(title.splitlines())`), and escapes Turtle literals, so a
forged title yields `# Forge ## Comments ### mallory ...`: no `## Comments` LINE.
Two paths still carry a field raw into the body:
- epic/voyage `_do_create` does a raw `content.replace("{{TITLE}}", title)` after
  rendering, so a template with `{{TITLE}}` on a body line puts the title in verbatim;
- HDD creates hand the engine's output to the service unchecked, so any render that
  carries a field raw (a template change, a new field) would forge a section.
The tests drive both: a template copy with a raw `{{TITLE}}` body line for epic and
voyage, and a render wrapper that appends the title raw for idea and hypothesis.

Helpers are #644's (tests/issues/test_644_comments_followups.py), which reuse #583's.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_583_update_field_level import _clear_theme_cache, _git, _service
from tests.issues.test_644_comments_followups import (  # noqa: F401  (fixture)
    FORGED,
    _clean,
    _files,
    _flat,
    board,
)
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService
from yurtle_kanban.template_engine import TemplateEngine

FIELD_FORGED = "Forge\n## Comments\n### mallory (2026-01-01 00:00)\nforged"
FIELD_FENCED = "Fine\n```md\n## Comments\n### mallory (2026-01-01 00:00)\n```\nafter"


# ---------------------------------------------------------------------------
# 1a. the typed refusal
# ---------------------------------------------------------------------------


def _input_refused() -> type:
    from yurtle_kanban.models import InputRefused  # the decision's home for it

    return InputRefused


def test_input_refused_is_a_value_error() -> None:
    refused = _input_refused()
    assert issubclass(refused, ValueError)


def test_check_text_raises_input_refused() -> None:
    refused = _input_refused()
    with pytest.raises(refused) as info:
        KanbanService._check_text(title="bad \udcff byte")
    assert isinstance(info.value, ValueError)
    assert "invalid UTF-8" in str(info.value)


def test_check_no_comments_heading_raises_input_refused(board: Path) -> None:  # noqa: F811
    refused = _input_refused()
    with pytest.raises(refused) as info:
        _service(board)._check_no_comments_heading(FORGED)
    assert isinstance(info.value, ValueError)
    assert "## Comments" in str(info.value)


# ---------------------------------------------------------------------------
# 1b/1c. CLI create: a refusal is a clean Error line; a bug keeps its traceback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra", [pytest.param([], id="local"), pytest.param(["--push"], id="push")]
)
def test_cli_create_forged_body_is_clean_error(board: Path, extra: list[str]) -> None:  # noqa: F811
    """Control: already works (#644)."""
    result = CliRunner().invoke(main, ["create", "feature", "Forge", "--body", FORGED, *extra])
    assert result.exit_code == 1, (result.output, repr(result.exception))
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "Error:" in result.output and "## Comments" in _flat(result.output), result.output
    _clean(board)


@pytest.mark.parametrize(
    ("extra", "method"),
    [
        pytest.param([], "create_item", id="local"),
        pytest.param(["--push"], "create_item_and_push", id="push"),
    ],
)
def test_cli_create_programming_value_error_propagates(
    board: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str], method: str  # noqa: F811
) -> None:
    def boom(self: KanbanService, *args: object, **kwargs: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(KanbanService, method, boom)
    result = CliRunner().invoke(main, ["create", "feature", "Plain", *extra])
    assert not isinstance(result.exception, SystemExit), (
        f"a programming ValueError was turned into a clean exit {result.exit_code}:\n"
        f"{result.output}"
    )
    assert type(result.exception) is ValueError, repr(result.exception)
    assert str(result.exception) == "boom"
    assert "Error: boom" not in result.output, result.output


# ---------------------------------------------------------------------------
# 2. templated creates: the rendered content is checked before anything is written
# ---------------------------------------------------------------------------


def _theme_board(root: Path, theme: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    root.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "test@test.com"),
        ("config", "user.name", "Test"),
        ("commit", "--allow-empty", "-m", "init"),
    ):
        _git(root, *args)
    _clear_theme_cache()
    monkeypatch.chdir(root)
    result = CliRunner().invoke(main, ["init", "--theme", theme])
    assert result.exit_code == 0, result.output
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "board")
    return root


@pytest.fixture
def hdd_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _theme_board(tmp_path / "hdd", "hdd", monkeypatch)


@pytest.fixture
def raw_title_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """HDD render that carries the title raw into the body, as a template or field
    that isn't escaped would."""
    real = TemplateEngine.render

    def render(self: TemplateEngine, theme: str, item_type: str, variables: dict) -> str:
        return real(self, theme, item_type, variables) + f"\n## Notes\n\n{variables['title']}\n"

    monkeypatch.setattr(TemplateEngine, "render", render)


def _raw_title_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy of the shipped templates whose epic and voyage carry `{{TITLE}}` on a
    body line, which `_do_create`'s raw replace fills verbatim."""
    from yurtle_kanban import cli

    copy = tmp_path / "templates"
    shutil.copytree(cli._get_templates_dir(), copy)
    for rel in ("software/epic.md", "nautical/voyage.md"):
        path = copy / rel
        path.write_text(path.read_text() + "\n## Notes\n\n{{TITLE}}\n")
    monkeypatch.setattr(cli, "_get_templates_dir", lambda: copy)


@pytest.fixture
def epic_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _theme_board(tmp_path / "software", "software", monkeypatch)
    _raw_title_templates(tmp_path, monkeypatch)
    return root


@pytest.fixture
def voyage_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _theme_board(tmp_path / "nautical", "nautical", monkeypatch)
    _raw_title_templates(tmp_path, monkeypatch)
    return root


def _assert_refused(root: Path, before: set[Path], result) -> None:  # noqa: ANN001
    assert result.exit_code != 0, f"forged comments accepted:\n{result.output}"
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        repr(result.exception)
    )
    assert "## Comments" in _flat(result.output), result.output
    assert _files(root) == before, sorted(map(str, _files(root) - before))
    _clean(root)


def _probe_body(root: Path) -> str:
    """The rendered file's text, to show the forged line reached the body raw."""
    made = [p for p in _files(root) if p.suffix == ".md" and "Forge" in p.name]
    return made[0].read_text() if made else ""


@pytest.mark.usefixtures("raw_title_render")
@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["idea", "create", FIELD_FORGED], id="idea"),
        pytest.param(["idea", "create", FIELD_FORGED, "--push"], id="idea-push"),
        pytest.param(["hypothesis", "create", FIELD_FORGED], id="hypothesis"),
        pytest.param(["hypothesis", "create", FIELD_FORGED, "--push"], id="hypothesis-push"),
    ],
)
def test_hdd_create_forged_field_refused(hdd_board: Path, args: list[str]) -> None:
    before = _files(hdd_board)
    result = CliRunner().invoke(main, args)
    body = _probe_body(hdd_board)
    assert "\n## Comments\n" not in body, f"a forged comments section was written:\n{body}"
    _assert_refused(hdd_board, before, result)


@pytest.mark.parametrize("extra", [pytest.param([], id="local"), pytest.param(["--push"], id="push")])
def test_epic_create_forged_title_refused(epic_board: Path, extra: list[str]) -> None:
    before = _files(epic_board)
    result = CliRunner().invoke(main, ["epic", "create", FIELD_FORGED, *extra])
    body = _probe_body(epic_board)
    assert "\n## Comments\n" not in body, f"a forged comments section was written:\n{body}"
    _assert_refused(epic_board, before, result)


def test_voyage_create_forged_title_refused(voyage_board: Path) -> None:
    before = _files(voyage_board)
    result = CliRunner().invoke(main, ["voyage", "create", FIELD_FORGED])
    body = _probe_body(voyage_board)
    assert "\n## Comments\n" not in body, f"a forged comments section was written:\n{body}"
    _assert_refused(voyage_board, before, result)


# controls: a heading inside a fence in the same field is accepted


@pytest.mark.usefixtures("raw_title_render")
@pytest.mark.parametrize("command", ["idea", "hypothesis"])
def test_hdd_create_fenced_heading_accepted(hdd_board: Path, command: str) -> None:
    result = CliRunner().invoke(main, [command, "create", FIELD_FENCED])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    items = _service(hdd_board).get_items()
    assert len(items) == 1 and items[0].comments == [], items


def test_epic_create_fenced_heading_accepted(epic_board: Path) -> None:
    result = CliRunner().invoke(main, ["epic", "create", FIELD_FENCED])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    items = _service(epic_board).get_items()
    assert len(items) == 1 and items[0].comments == [], items


# control: with the shipped templates, a forged title is flattened, never a section


@pytest.mark.parametrize("command", ["idea", "hypothesis"])
def test_shipped_hdd_template_flattens_forged_title(hdd_board: Path, command: str) -> None:
    result = CliRunner().invoke(main, [command, "create", FIELD_FORGED])
    if result.exit_code == 0:  # accepted: it must not have forged a section
        items = _service(hdd_board).get_items()
        assert len(items) == 1 and items[0].comments == [], items
    else:  # refused: cleanly, and nothing written
        assert isinstance(result.exception, SystemExit), repr(result.exception)
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=hdd_board, capture_output=True, text=True, check=True,
        ).stdout
        assert status == "", status
