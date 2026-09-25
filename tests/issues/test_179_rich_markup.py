"""Issue #179 — user text that looks like Rich markup must print literally.

`console.print(f"[green]Created {id}: {title}[/green]")` hands the title to Rich's
markup parser. A title like `a [/b] c` raises MarkupError (after the file is
written); `[bold]x[/bold]` or `[link=…]z[/link]` is silently swallowed. The same
holds for item IDs in error paths (`show '[/zz]'`) and for comments and summaries.

Decided behaviour: every command prints user- or file-controlled text verbatim,
never crashes on it, and error paths still exit non-zero with the text shown.

Also from the #176 review:
- a broken `_TEMPLATE*.md` stays silent in the except-path warning (#158);
- an empty `status:` or a numeric `type:` falls back to the default instead of
  being dropped with an `AttributeError` warning.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

MARKUP_TITLES = [
    "a [/b] c",
    "[bold]x[/bold]",
    "x [ y",
    "[red]",
    "[link=http://e]z[/link]",
]
BAD_ID = "[/zz]"

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=path, capture_output=True, check=True)


def _clear_theme_cache() -> None:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with the software theme, cwd set to it."""
    _git_init(tmp_path)
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    (tmp_path / "kanban-work" / "bugs").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/features/", "kanban-work/bugs/"],
        ),
    ).save(tmp_path / ".kanban" / "config.yaml")
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _flat(text: str) -> str:
    """Collapse whitespace: Rich wraps long lines."""
    return " ".join(text.split())


def _assert_clean(result: Result) -> None:
    """No crash of any kind, in the output or as the exception."""
    assert "Traceback" not in result.output, result.output
    assert "MarkupError" not in result.output, result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception


def _assert_ok(result: Result) -> None:
    _assert_clean(result)
    assert result.exit_code == 0, result.output


def _create(runner: CliRunner, title: str) -> tuple[Result, str]:
    """Create a feature and return (result, its ID)."""
    result = runner.invoke(main, ["create", "feature", title])
    files = sorted((Path.cwd() / "kanban-work" / "features").glob("FEAT-*.md"))
    assert files, f"no item file written: {result.output}"
    item_id = files[-1].stem.split("-")[0] + "-" + files[-1].stem.split("-")[1]
    return result, item_id


# ---------------------------------------------------------------------------
# 1. Markup-like titles through the commands that print them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", MARKUP_TITLES)
def test_create_prints_title_literally(repo: Path, runner: CliRunner, title: str) -> None:
    result, _ = _create(runner, title)
    _assert_ok(result)
    assert title in _flat(result.output)


def test_create_plain_title_control(repo: Path, runner: CliRunner) -> None:
    result, item_id = _create(runner, "Plain title")
    _assert_ok(result)
    assert f"Created {item_id}: Plain title" in _flat(result.output)


@pytest.mark.parametrize("title", MARKUP_TITLES)
def test_list_prints_title_literally(repo: Path, runner: CliRunner, title: str) -> None:
    _create(runner, title)
    result = runner.invoke(main, ["list"])
    _assert_ok(result)
    assert title in _flat(result.output)


@pytest.mark.parametrize("title", MARKUP_TITLES)
def test_show_prints_title_literally(repo: Path, runner: CliRunner, title: str) -> None:
    _, item_id = _create(runner, title)
    result = runner.invoke(main, ["show", item_id])
    _assert_ok(result)
    assert title in _flat(result.output)


def _dense(text: str) -> str:
    """Drop whitespace and box-drawing chars: board cards wrap inside narrow columns."""
    return "".join(c for c in text if not c.isspace() and not "\u2500" <= c <= "\u257f")


def _card_title(title: str) -> str:
    """The board card truncates titles longer than 20 characters."""
    return title if len(title) <= 20 else title[:17] + "..."


@pytest.mark.parametrize("title", [*MARKUP_TITLES, "Plain title"])
def test_board_prints_title_literally(repo: Path, runner: CliRunner, title: str) -> None:
    _create(runner, title)
    result = runner.invoke(main, ["board"])
    _assert_ok(result)
    assert _dense(_card_title(title)) in _dense(result.output), result.output


@pytest.mark.parametrize("title", MARKUP_TITLES)
def test_move_item_with_markup_title(repo: Path, runner: CliRunner, title: str) -> None:
    _, item_id = _create(runner, title)
    result = runner.invoke(main, ["move", item_id, "ready", "--force"])
    _assert_ok(result)
    assert f"Moved {item_id} to ready" in _flat(result.output)


@pytest.mark.parametrize("text", MARKUP_TITLES)
def test_comment_shown_literally(repo: Path, runner: CliRunner, text: str) -> None:
    _, item_id = _create(runner, "Plain title")
    result = runner.invoke(main, ["comment", item_id, text])
    _assert_ok(result)
    shown = runner.invoke(main, ["show", item_id])
    _assert_ok(shown)
    assert text in _flat(shown.output)


@pytest.mark.parametrize("summary", MARKUP_TITLES)
def test_rank_summary_literally(repo: Path, runner: CliRunner, summary: str) -> None:
    _, item_id = _create(runner, "Plain title")
    result = runner.invoke(main, ["rank", item_id, "1", "--summary", summary])
    _assert_ok(result)
    assert f"Value: {summary}" in _flat(result.output)


# ---------------------------------------------------------------------------
# 2. Error paths with a markup-like ID
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["show", BAD_ID],
        ["move", BAD_ID, "done"],
        ["comment", BAD_ID, "hi"],
        ["rank", BAD_ID, "1"],
    ],
    ids=["show", "move", "comment", "rank"],
)
def test_error_path_prints_markup_id_literally(
    repo: Path, runner: CliRunner, args: list[str]
) -> None:
    result = runner.invoke(main, args)
    _assert_clean(result)
    assert result.exit_code != 0, result.output
    # commands may upper-case the ID; either way it appears verbatim
    assert BAD_ID.upper() in _flat(result.output).upper()


def test_error_path_plain_id_control(repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["show", "NOPE-1"])
    _assert_clean(result)
    assert result.exit_code == 1
    assert "Item not found: NOPE-1" in _flat(result.output)


# ---------------------------------------------------------------------------
# 3. Epic / HDD create commands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", ["[/e] epic", "[bold]e[/bold]"])
def test_epic_create_prints_title_literally(
    repo: Path, runner: CliRunner, title: str
) -> None:
    (repo / "kanban-work" / "epics").mkdir()
    result = runner.invoke(main, ["epic", "create", title])
    _assert_ok(result)
    assert title in _flat(result.output)


@pytest.fixture
def hdd_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git_init(tmp_path)
    (tmp_path / ".kanban").mkdir()
    scan = []
    for sub in ("ideas", "literature", "papers", "hypotheses", "experiments", "measures"):
        (tmp_path / "research" / sub).mkdir(parents=True)
        scan.append(f"research/{sub}/")
    KanbanConfig(
        theme="hdd", paths=PathConfig(root="research/", scan_paths=scan)
    ).save(tmp_path / ".kanban" / "config.yaml")
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("title", ["[/i] idea", "[bold]i[/bold]"])
def test_idea_create_prints_title_literally(
    hdd_repo: Path, runner: CliRunner, title: str
) -> None:
    result = runner.invoke(main, ["idea", "create", title])
    _assert_ok(result)
    assert title in _flat(result.output)


def test_idea_create_plain_control(hdd_repo: Path, runner: CliRunner) -> None:
    result = runner.invoke(main, ["idea", "create", "Plain idea"])
    _assert_ok(result)
    assert "Plain idea" in _flat(result.output)


# ---------------------------------------------------------------------------
# 4a. `_TEMPLATE*` exemption of #158's except-path warning
# ---------------------------------------------------------------------------

# Frontmatter parses as a mapping, then crashes later in _parse_file (#158's case)
CRASHING = "---\nid: FEAT-{n}\ntitle: T\ntype: feature\nstatus: [backlog]\n---\n\nBody\n"


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def test_broken_template_is_silent_but_broken_item_warns(repo: Path) -> None:
    features = repo / "kanban-work" / "features"
    (features / "_TEMPLATE.md").write_text(CRASHING.format(n="000"))
    (features / "_TEMPLATE-feature.md").write_text(CRASHING.format(n="000"))
    (features / "FEAT-007-broken.md").write_text(CRASHING.format(n="007"))
    svc = _service(repo)
    svc.get_items()
    warned = {path.name for path, _ in svc.parse_warnings}
    assert "FEAT-007-broken.md" in warned
    assert not any(name.startswith("_TEMPLATE") for name in warned), warned


def test_broken_template_silent_in_cli_list(repo: Path, runner: CliRunner) -> None:
    features = repo / "kanban-work" / "features"
    (features / "_TEMPLATE.md").write_text(CRASHING.format(n="000"))
    (features / "FEAT-007-broken.md").write_text(CRASHING.format(n="007"))
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "FEAT-007-broken.md" in result.output
    assert "_TEMPLATE" not in result.output


# ---------------------------------------------------------------------------
# 4b. Empty `status:` / numeric `type:` fall back instead of AttributeError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frontmatter",
    [
        "id: FEAT-011\ntitle: Empty status\ntype: feature\nstatus:\n",
        "id: FEAT-012\ntitle: Numeric type\ntype: 5\nstatus: backlog\n",
        "id: FEAT-013\ntitle: Empty type\ntype:\nstatus: backlog\n",
    ],
    ids=["empty-status", "numeric-type", "empty-type"],
)
def test_bad_scalar_field_falls_back(repo: Path, runner: CliRunner, frontmatter: str) -> None:
    item_id = frontmatter.split("\n")[0].split(": ")[1]
    path = repo / "kanban-work" / "features" / f"{item_id}-x.md"
    path.write_text(f"---\n{frontmatter}---\n\nBody\n")
    svc = _service(repo)
    items = {i.id: i for i in svc.get_items()}
    reasons = [reason for _, reason in svc.parse_warnings]
    assert not any("AttributeError" in r for r in reasons), reasons
    assert item_id in items, reasons
    assert items[item_id].status is WorkItemStatus.BACKLOG

    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "AttributeError" not in result.output
    assert item_id in _flat(result.output)


def test_valid_scalar_fields_control(repo: Path) -> None:
    path = repo / "kanban-work" / "features" / "FEAT-014-ok.md"
    path.write_text("---\nid: FEAT-014\ntitle: Fine\ntype: feature\nstatus: ready\n---\n")
    svc = _service(repo)
    items = {i.id: i for i in svc.get_items()}
    assert items["FEAT-014"].status is WorkItemStatus.READY
    assert svc.parse_warnings == []


# ---------------------------------------------------------------------------
# Round 2 (review of PR #201): numeric `id:` / `title:` must not crash output
# ---------------------------------------------------------------------------

NUMERIC_ID = "---\nid: 42\ntitle: Numeric id\ntype: feature\nstatus: backlog\n---\n\nBody\n"
NUMERIC_TITLE = "---\nid: FEAT-021\ntitle: 2024\ntype: feature\nstatus: backlog\n---\n\nBody\n"


@pytest.fixture
def numeric_id(repo: Path) -> Path:
    (repo / "kanban-work" / "features" / "FEAT-042-numeric-id.md").write_text(NUMERIC_ID)
    return repo


@pytest.fixture
def numeric_title(repo: Path) -> Path:
    (repo / "kanban-work" / "features" / "FEAT-021-numeric-title.md").write_text(NUMERIC_TITLE)
    return repo


def _assert_no_type_error(result: Result) -> None:
    _assert_ok(result)
    assert "TypeError" not in result.output, result.output


@pytest.mark.parametrize("args", [["board"], ["show", "42"], ["list"]], ids=lambda a: a[0])
def test_numeric_id_prints(numeric_id: Path, runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(main, args)
    _assert_no_type_error(result)
    assert "42" in _dense(result.output)


@pytest.mark.parametrize(
    "args", [["show", "FEAT-021"], ["list"], ["board"]], ids=lambda a: a[0]
)
def test_numeric_title_prints(numeric_title: Path, runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(main, args)
    _assert_no_type_error(result)
    assert "2024" in _dense(result.output)


def test_numeric_id_and_title_scan_as_strings(repo: Path) -> None:
    features = repo / "kanban-work" / "features"
    (features / "FEAT-042-numeric-id.md").write_text(NUMERIC_ID)
    (features / "FEAT-021-numeric-title.md").write_text(NUMERIC_TITLE)
    svc = _service(repo)
    items = svc.get_items()
    by_title = {i.title: i for i in items}
    by_id = {i.id: i for i in items}
    assert "Numeric id" in by_title, [(i.id, i.title) for i in items]
    assert by_title["Numeric id"].id == "42"
    assert isinstance(by_title["Numeric id"].id, str)
    assert "FEAT-021" in by_id, [(i.id, i.title) for i in items]
    assert by_id["FEAT-021"].title == "2024"
    assert isinstance(by_id["FEAT-021"].title, str)
