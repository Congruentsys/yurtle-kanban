"""Issue #439 — `list` shows canonical `backlog` for an item the file and board call Draft.

Scanning maps a native `status: draft` to BACKLOG and `render_list` prints
`item.status.value`, so on hdd the human table says `backlog` for an item whose file
says `draft` and whose board column is Draft. Separately, hdd `create` (single-board,
and multi-board for most types) writes `status: backlog`, while multi-board `move`
writes the native name via `_get_reverse_status_mapping`.

Decided ([steer] on #439):
1. The human `list` table shows the status as the theme names it, through the theme's
   reverse status mapping (canonical -> native). A theme with no `status_mappings`
   (software, nautical, spec today) maps nothing, so its names stay canonical.
2. Machine contracts are unchanged: `list --json` / `show --json` `status` is
   canonical, and `list --status <canonical>` still finds the item.
3. `create` on a theme with `status_mappings` writes the native initial status
   (hdd: `status: draft`); the item still scans as BACKLOG and is counted under Draft.

The expected display/native names are derived from each theme file's
`status_mappings`, so the tests follow the theme rather than hardcoding it.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban.cli import main

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"

# one type `create` accepts on each built-in theme (see #87's CREATABLE_TYPES)
FIRST_TYPE: dict[str, str] = {
    "software": "feature",
    "nautical": "expedition",
    "spec": "issue",
    "hdd": "idea",
}

HEADER_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<count>\d+)(?:/\d+)?\)$")

# ---------------------------------------------------------------------------
# Fixtures / helpers (after tests/issues/test_87_board_count_vs_draw.py, PR #438)
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(args, cwd=path, capture_output=True, check=True)


def _clear_theme_cache() -> None:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty git repo, cwd set to it, no kanban config yet."""
    _git_init(tmp_path)
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def wide(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Route cli's console to a wide, colourless buffer so the table never wraps."""
    buf = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=buf, width=300, color_system=None, force_terminal=False)
    )
    return buf


def _ok(result: Result) -> Result:
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    assert result.exit_code == 0, result.output
    return result


def _invoke(runner: CliRunner, args: list[str]) -> Result:
    return _ok(runner.invoke(main, args))


def _text(runner: CliRunner, buf: io.StringIO, args: list[str]) -> str:
    """Run a command and return everything it printed (cli.console or click echo)."""
    buf.seek(0)
    buf.truncate()
    result = _invoke(runner, args)
    return buf.getvalue() + result.output


def _created_id(runner: CliRunner, buf: io.StringIO, args: list[str]) -> str:
    out = _text(runner, buf, args)
    match = re.search(r"Created (\S+):", out)
    assert match, out
    return match.group(1)


def _item_file(repo: Path, item_id: str) -> Path:
    files = [p for p in repo.rglob(f"{item_id}-*.md") if ".git" not in p.parts]
    assert len(files) == 1, files
    return files[0]


def _file_status(repo: Path, item_id: str) -> str:
    text = _item_file(repo, item_id).read_text()
    front = text.split("---", 2)[1]
    return str(yaml.safe_load(front)["status"])


def _list_statuses(runner: CliRunner, buf: io.StringIO, args: list[str]) -> dict[str, str]:
    """Parse the human `list` table into {item id: Status cell}."""
    text = _text(runner, buf, ["list", *args])
    lines = text.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if "Status" in line and "Title" in line), None
    )
    assert header_idx is not None, f"no table header in:\n{text}"
    header = re.split(r"\s{2,}", lines[header_idx].strip())
    status_col = header.index("Status")
    id_col = header.index("ID")
    rows: dict[str, str] = {}
    for line in lines[header_idx + 1 :]:
        cells = re.split(r"\s{2,}", line.strip())
        if len(cells) != len(header):
            continue
        item_id = cells[id_col].split()[-1]  # drop the type marker ("? IDEA-R-001")
        rows[item_id] = cells[status_col]
    return rows


def _json(runner: CliRunner, args: list[str]) -> object:
    result = _invoke(runner, args)
    return json.loads(result.output)


def _reverse_mapping(theme: str) -> dict[str, str]:
    """canonical -> native, from the theme file (what `move` uses on a board)."""
    data = yaml.safe_load((THEMES_DIR / f"{theme}.yaml").read_text())
    return {canonical: native for native, canonical in (data.get("status_mappings") or {}).items()}


def _native(theme: str, canonical: str) -> str:
    return _reverse_mapping(theme).get(canonical, canonical)


def _board_counts(runner: CliRunner, buf: io.StringIO, args: list[str]) -> dict[str, int]:
    """Header counts of the rendered board: {column name: count}."""
    text = _text(runner, buf, ["board", *args])
    lines = text.splitlines()
    sep_idx = next(
        i for i, line in enumerate(lines) if line.lstrip().startswith("├") and "┤" in line
    )
    bounds = [i for i, ch in enumerate(lines[sep_idx]) if ch in "├┼┤"]
    header = lines[sep_idx - 1]
    counts: dict[str, int] = {}
    for left, right in zip(bounds, bounds[1:]):
        match = HEADER_RE.match(header[left + 1 : right].strip())
        assert match, text
        counts[match.group("name")] = int(match.group("count"))
    return counts


def _multiboard(runner: CliRunner) -> None:
    """#98's layout: nautical default board + hdd research board."""
    _invoke(runner, ["init", "--theme", "nautical"])
    _invoke(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])
    _clear_theme_cache()


# ---------------------------------------------------------------------------
# 0. The reverse mappings the tests derive from (pinned so a theme change is seen)
# ---------------------------------------------------------------------------


def test_reverse_mappings_per_theme() -> None:
    assert _reverse_mapping("hdd") == {
        "backlog": "draft",
        "in_progress": "active",
        "done": "complete",
        "blocked": "abandoned",
    }
    # no `status_mappings` today: their names stay canonical everywhere
    assert _reverse_mapping("nautical") == {}
    assert _reverse_mapping("software") == {}
    assert _reverse_mapping("spec") == {}


# ---------------------------------------------------------------------------
# 1. `list` shows the theme's status name
# ---------------------------------------------------------------------------


def test_hdd_single_board_created_idea_listed_as_draft(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "hdd"])
    item_id = _created_id(runner, wide, ["create", "idea", "A real item"])
    assert _list_statuses(runner, wide, [])[item_id] == "draft"


def test_hdd_single_board_file_with_status_draft_listed_as_draft(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "hdd"])
    item_id = _created_id(runner, wide, ["create", "idea", "A real item"])
    path = _item_file(repo, item_id)
    path.write_text(re.sub(r"(?m)^status: .*$", "status: draft", path.read_text()))

    assert _list_statuses(runner, wide, [])[item_id] == "draft"


@pytest.mark.parametrize(
    ("native", "canonical"),
    [("active", "in_progress"), ("complete", "done"), ("abandoned", "blocked")],
)
def test_hdd_single_board_other_statuses_listed_by_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO, native: str, canonical: str
) -> None:
    _invoke(runner, ["init", "--theme", "hdd"])
    item_id = _created_id(runner, wide, ["create", "idea", "A real item"])
    _invoke(runner, ["move", item_id, native, "--force", "--skip-gates", "--no-commit"])
    # single-board `move` writes the theme's name, as multi-board does (#439)
    assert _file_status(repo, item_id) == native

    assert _list_statuses(runner, wide, [])[item_id] == native
    # machine contract: canonical
    (item,) = _json(runner, ["list", "--json"])  # type: ignore[misc]
    assert item["status"] == canonical


def test_multiboard_hdd_hypothesis_listed_as_draft(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard(runner)
    hyp_id = _created_id(
        runner, wide, ["hypothesis", "create", "probe hypothesis", "--target", ">=1"]
    )
    assert _file_status(repo, hyp_id) == "draft"  # multi-board hypothesis create: native

    assert _list_statuses(runner, wide, ["--board", "research"])[hyp_id] == "draft"
    assert _list_statuses(runner, wide, [])[hyp_id] == "draft"


def test_multiboard_hdd_moved_item_listed_by_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard(runner)
    hyp_id = _created_id(
        runner, wide, ["hypothesis", "create", "probe hypothesis", "--target", ">=1"]
    )
    _invoke(runner, ["move", hyp_id, "active", "--force", "--skip-gates", "--no-commit"])
    assert _file_status(repo, hyp_id) == "active"

    assert _list_statuses(runner, wide, ["--board", "research"])[hyp_id] == "active"


def test_multiboard_nautical_item_listed_per_its_reverse_mapping(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    """Control: the nautical board has no status_mappings, so its names stay canonical,
    exactly as `move` writes them there — alongside an hdd item listed as `draft`."""
    _multiboard(runner)
    dev_id = _created_id(runner, wide, ["create", "expedition", "dev item"])
    _invoke(runner, ["move", dev_id, "underway", "--force", "--skip-gates", "--no-commit"])
    assert _file_status(repo, dev_id) == _native("nautical", "in_progress")

    rows = _list_statuses(runner, wide, [])
    assert rows[dev_id] == _native("nautical", "in_progress")


@pytest.mark.parametrize("theme", sorted(FIRST_TYPE))
def test_every_theme_created_item_listed_by_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO, theme: str
) -> None:
    _invoke(runner, ["init", "--theme", theme])
    item_id = _created_id(runner, wide, ["create", FIRST_TYPE[theme], "item one"])
    assert _list_statuses(runner, wide, [])[item_id] == _native(theme, "backlog")


# ---------------------------------------------------------------------------
# 2. Machine contracts are unchanged (controls)
# ---------------------------------------------------------------------------


def test_hdd_list_json_status_is_canonical(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "hdd"])
    item_id = _created_id(runner, wide, ["create", "idea", "A real item"])
    items = _json(runner, ["list", "--json"])
    assert [(i["id"], i["status"]) for i in items] == [(item_id, "backlog")]  # type: ignore[union-attr,index]


def test_hdd_show_json_status_is_canonical(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "hdd"])
    item_id = _created_id(runner, wide, ["create", "idea", "A real item"])
    shown = _json(runner, ["show", item_id, "--json"])
    assert shown["status"] == "backlog"  # type: ignore[index]


def test_multiboard_hdd_json_status_is_canonical(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard(runner)
    hyp_id = _created_id(
        runner, wide, ["hypothesis", "create", "probe hypothesis", "--target", ">=1"]
    )
    items = {i["id"]: i["status"] for i in _json(runner, ["list", "--json"])}  # type: ignore[union-attr,index]
    assert items[hyp_id] == "backlog"
    assert _json(runner, ["show", hyp_id, "--json"])["status"] == "backlog"  # type: ignore[index]


def test_hdd_list_status_backlog_filter_still_finds_item(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    # `list --status draft` is refused today ("Unknown status: draft"), so it is not
    # pinned either way; the canonical filter is the contract.
    _invoke(runner, ["init", "--theme", "hdd"])
    item_id = _created_id(runner, wide, ["create", "idea", "A real item"])
    assert item_id in _list_statuses(runner, wide, ["--status", "backlog"])
    items = _json(runner, ["list", "--status", "backlog", "--json"])
    assert [i["id"] for i in items] == [item_id]  # type: ignore[union-attr,index]


def test_multiboard_list_status_backlog_filter_still_finds_hdd_item(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard(runner)
    hyp_id = _created_id(
        runner, wide, ["hypothesis", "create", "probe hypothesis", "--target", ">=1"]
    )
    rows = _list_statuses(runner, wide, ["--board", "research", "--status", "backlog"])
    assert hyp_id in rows


# ---------------------------------------------------------------------------
# 3. `create` writes the theme's native initial status
# ---------------------------------------------------------------------------


def test_hdd_single_board_create_writes_draft(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "hdd"])
    item_id = _created_id(runner, wide, ["create", "idea", "A real item"])

    assert _file_status(repo, item_id) == "draft"
    # still scans as canonical BACKLOG, counted under Draft
    (item,) = _json(runner, ["list", "--json"])  # type: ignore[misc]
    assert item["status"] == "backlog"
    assert _board_counts(runner, wide, [])["Draft"] == 1


@pytest.mark.parametrize("item_type", ["idea", "experiment", "literature"])
def test_multiboard_hdd_create_writes_draft(
    repo: Path, runner: CliRunner, wide: io.StringIO, item_type: str
) -> None:
    _multiboard(runner)
    item_id = _created_id(runner, wide, ["create", item_type, f"an {item_type}"])
    assert "research" in _item_file(repo, item_id).relative_to(repo).parts

    assert _file_status(repo, item_id) == "draft"
    items = {i["id"]: i["status"] for i in _json(runner, ["list", "--json"])}  # type: ignore[union-attr,index]
    assert items[item_id] == "backlog"
    assert _board_counts(runner, wide, ["research"])["Draft"] == 1


@pytest.mark.parametrize("theme", sorted(FIRST_TYPE))
def test_every_theme_create_writes_native_initial_status(
    repo: Path, runner: CliRunner, wide: io.StringIO, theme: str
) -> None:
    """hdd: `draft`; software/nautical/spec (no status_mappings): `backlog`."""
    _invoke(runner, ["init", "--theme", theme])
    item_id = _created_id(runner, wide, ["create", FIRST_TYPE[theme], "item one"])
    assert _file_status(repo, item_id) == _native(theme, "backlog")


def test_multiboard_nautical_create_unchanged(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard(runner)
    dev_id = _created_id(runner, wide, ["create", "expedition", "dev item"])
    assert _file_status(repo, dev_id) == _native("nautical", "backlog") == "backlog"


def test_software_create_and_list_unchanged(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "software"])
    item_id = _created_id(runner, wide, ["create", "feature", "A real item"])
    assert _file_status(repo, item_id) == "backlog"
    assert _list_statuses(runner, wide, [])[item_id] == "backlog"
    (item,) = _json(runner, ["list", "--json"])  # type: ignore[misc]
    assert item["status"] == "backlog"


# ---------------------------------------------------------------------------
# 4. Round trip: create -> move -> list agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["single", "multi"])
def test_hdd_round_trip_create_move_list(
    repo: Path, runner: CliRunner, wide: io.StringIO, layout: str
) -> None:
    if layout == "single":
        _invoke(runner, ["init", "--theme", "hdd"])
        list_args: list[str] = []
    else:
        _multiboard(runner)
        list_args = ["--board", "research"]
    item_id = _created_id(runner, wide, ["create", "idea", "round trip"])

    steps = [("draft", "backlog")]
    for native, canonical in [("active", "in_progress"), ("complete", "done")]:
        steps.append((native, canonical))
    for i, (native, canonical) in enumerate(steps):
        if i:
            _invoke(runner, ["move", item_id, native, "--force", "--skip-gates", "--no-commit"])
        # create and move write the theme's name on both layouts; it scans back canonical
        assert _file_status(repo, item_id) == native
        assert _list_statuses(runner, wide, list_args)[item_id] == native
        shown = _json(runner, ["show", item_id, "--json"])
        assert shown["status"] == canonical  # type: ignore[index]
