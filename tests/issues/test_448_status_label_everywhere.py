# ruff: noqa: F811  -- pytest fixtures imported from the #439 test module are re-bound as args
"""Issue #448 — the human renderers #439 missed still print canonical status names, and a
multi-board scan reads a native status through whichever board comes first.

Decided ([steer] on #448, extending #439):
1. Every human-facing output that prints an item's status names it as the item's theme
   does, through `KanbanService.status_label`:
   - `show <id>` detail (board.render_item_detail),
   - the `move` confirmation ("Moved X to active", not "to in_progress"),
   - the `roadmap` table (board._render_roadmap_table, incl. --ranked / --by-type),
   - `roadmap --export md` (cli.py `(status)`), and the `rank` confirmation
     ("Status: ...", cli.py).
   JSON stays canonical; software (no status_mappings) stays canonical everywhere.
2. A scan maps a native status through the item's OWN board's theme: two custom themes
   mapping `doing` to different canonicals give each item its own board's reading,
   whatever the board order in config.yaml.
3. (Loose sanity) a scan doesn't reload the theme once per item.

Expected names derive from each theme file's `status_mappings`, as in #439's tests.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_439_list_theme_status import (  # noqa: F401  (fixtures)
    THEMES_DIR,
    _clear_theme_cache,
    _created_id,
    _file_status,
    _invoke,
    _json,
    _multiboard,
    _native,
    _text,
    repo,
    runner,
    wide,
)

MOVE = ["--force", "--skip-gates", "--no-commit"]

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _layout(runner: CliRunner, wide: io.StringIO, layout: str) -> str:
    """Set up an hdd single board or a nautical+hdd multi-board repo; return an hdd
    item id (a research-board item on multi-board)."""
    if layout == "single":
        _invoke(runner, ["init", "--theme", "hdd"])
    else:
        _multiboard(runner)
    return _created_id(runner, wide, ["create", "idea", "an idea"])


def _show_status(runner: CliRunner, wide: io.StringIO, item_id: str) -> str:
    text = _text(runner, wide, ["show", item_id])
    match = re.search(r"^\s*Status\s+(\S+)\s*$", text, re.MULTILINE)
    assert match, text
    return match.group(1)


def _table_status(text: str, item_id: str) -> str:
    """The Status cell of `item_id`'s row in a rich table with a Status header."""
    lines = text.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if "Status" in line and "ID" in line), None
    )
    assert header_idx is not None, f"no table header in:\n{text}"
    header = re.split(r"\s{2,}", lines[header_idx].strip())
    status_col = header.index("Status")
    for line in lines[header_idx + 1 :]:
        cells = re.split(r"\s{2,}", line.strip())
        # a trailing empty cell (--ranked's Value Summary) drops off the split
        if status_col < len(cells) <= len(header) and item_id in cells:
            return cells[status_col]
    raise AssertionError(f"no row for {item_id} in:\n{text}")


def _md_status(runner: CliRunner, wide: io.StringIO, item_id: str) -> str:
    text = _text(runner, wide, ["roadmap", "--export", "md"])
    match = re.search(rf"\*\*{re.escape(item_id)}\*\*.*\((\S+)\) @", text)
    assert match, text
    return match.group(1)


def _rank_status(runner: CliRunner, wide: io.StringIO, item_id: str) -> str:
    text = _text(runner, wide, ["rank", item_id, "1", "--no-commit"])
    match = re.search(r"Status: (\S+)", text)
    assert match, text
    return match.group(1)


def _moved_to(runner: CliRunner, wide: io.StringIO, item_id: str, target: str) -> str:
    text = _text(runner, wide, ["move", item_id, target, *MOVE])
    match = re.search(rf"Moved {re.escape(item_id)} to (\S+)", text)
    assert match, text
    return match.group(1)


LAYOUTS = ["single", "multi"]

# ---------------------------------------------------------------------------
# 1a. `show` detail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", LAYOUTS)
def test_hdd_show_detail_fresh_item_is_draft(
    repo: Path, runner: CliRunner, wide: io.StringIO, layout: str
) -> None:
    item_id = _layout(runner, wide, layout)
    assert _show_status(runner, wide, item_id) == _native("hdd", "backlog") == "draft"


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("canonical", ["in_progress", "done", "blocked"])
def test_hdd_show_detail_uses_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO, layout: str, canonical: str
) -> None:
    item_id = _layout(runner, wide, layout)
    native = _native("hdd", canonical)
    _invoke(runner, ["move", item_id, native, *MOVE])
    assert _show_status(runner, wide, item_id) == native
    assert _json(runner, ["show", item_id, "--json"])["status"] == canonical  # type: ignore[index]


def test_multiboard_hypothesis_show_detail_is_draft(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard(runner)
    hyp_id = _created_id(
        runner, wide, ["hypothesis", "create", "probe hypothesis", "--target", ">=1"]
    )
    assert _show_status(runner, wide, hyp_id) == "draft"


# ---------------------------------------------------------------------------
# 1b. `move` confirmation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("canonical", ["in_progress", "done", "blocked", "backlog"])
@pytest.mark.parametrize("spelling", ["native", "canonical"])
def test_hdd_move_confirmation_uses_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO, layout: str, canonical: str,
    spelling: str,
) -> None:
    """Whether the target is typed as `active` or `in_progress`, the file says `active`
    and so does the confirmation."""
    item_id = _layout(runner, wide, layout)
    if canonical == "backlog":  # move away first so backlog is a real move
        _invoke(runner, ["move", item_id, "active", *MOVE])
    native = _native("hdd", canonical)
    target = native if spelling == "native" else canonical
    assert _moved_to(runner, wide, item_id, target) == native
    assert _file_status(repo, item_id) == native


# ---------------------------------------------------------------------------
# 1c. `roadmap` table (board._render_roadmap_table) and `roadmap --export md`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("flags", [[], ["--ranked"], ["--by-type"]])
def test_hdd_roadmap_table_uses_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO, layout: str, flags: list[str]
) -> None:
    item_id = _layout(runner, wide, layout)
    if flags == ["--ranked"]:
        _invoke(runner, ["rank", item_id, "1", "--no-commit"])
    assert _table_status(_text(runner, wide, ["roadmap", *flags]), item_id) == "draft"

    _invoke(runner, ["move", item_id, "active", *MOVE])
    assert _table_status(_text(runner, wide, ["roadmap", *flags]), item_id) == "active"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_hdd_roadmap_export_md_uses_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO, layout: str
) -> None:
    item_id = _layout(runner, wide, layout)
    assert _md_status(runner, wide, item_id) == "draft"
    _invoke(runner, ["move", item_id, "abandoned", *MOVE])
    assert _md_status(runner, wide, item_id) == "abandoned"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_hdd_roadmap_json_stays_canonical(
    repo: Path, runner: CliRunner, wide: io.StringIO, layout: str
) -> None:
    item_id = _layout(runner, wide, layout)
    _invoke(runner, ["move", item_id, "active", *MOVE])
    items = {i["id"]: i["status"] for i in _json(runner, ["roadmap", "--json"])}  # type: ignore[union-attr,index]
    assert items[item_id] == "in_progress"


# ---------------------------------------------------------------------------
# 1d. `rank` confirmation ("Status: ...")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", LAYOUTS)
def test_hdd_rank_confirmation_uses_theme_name(
    repo: Path, runner: CliRunner, wide: io.StringIO, layout: str
) -> None:
    item_id = _layout(runner, wide, layout)
    assert _rank_status(runner, wide, item_id) == "draft"
    _invoke(runner, ["move", item_id, "active", *MOVE])
    assert _rank_status(runner, wide, item_id) == "active"


# ---------------------------------------------------------------------------
# 1e. Controls: software and the nautical board stay canonical everywhere
# ---------------------------------------------------------------------------


def test_software_every_renderer_stays_canonical(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "software"])
    item_id = _created_id(runner, wide, ["create", "feature", "a feature"])
    assert _show_status(runner, wide, item_id) == "backlog"
    assert _table_status(_text(runner, wide, ["roadmap"]), item_id) == "backlog"
    assert _md_status(runner, wide, item_id) == "backlog"
    assert _rank_status(runner, wide, item_id) == "backlog"
    assert _moved_to(runner, wide, item_id, "in_progress") == "in_progress"
    assert _show_status(runner, wide, item_id) == "in_progress"
    assert _table_status(_text(runner, wide, ["roadmap"]), item_id) == "in_progress"


def test_multiboard_nautical_item_stays_canonical_beside_hdd(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard(runner)
    dev_id = _created_id(runner, wide, ["create", "expedition", "dev item"])
    expected = _native("nautical", "in_progress")
    assert _moved_to(runner, wide, dev_id, "in_progress") == expected
    assert _show_status(runner, wide, dev_id) == expected
    assert _table_status(_text(runner, wide, ["roadmap"]), dev_id) == expected


# ---------------------------------------------------------------------------
# 2. A scan maps a native status per item's board
# ---------------------------------------------------------------------------


def _custom_theme(repo: Path, name: str, doing_means: str) -> None:
    """A software-shaped theme whose own name `doing` maps to `doing_means`."""
    data = yaml.safe_load((THEMES_DIR / "software.yaml").read_text())
    data["theme"] = {"name": name, "description": f"{name}: doing is {doing_means}"}
    data["status_mappings"] = {"doing": doing_means}
    themes = repo / ".kanban" / "themes"
    themes.mkdir(parents=True, exist_ok=True)
    (themes / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def _write_item(repo: Path, board_dir: str, item_id: str, status: str) -> None:
    folder = repo / board_dir / "features"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item_id}-item.md").write_text(
        "---\n"
        f"id: {item_id}\n"
        f'title: "item {item_id}"\n'
        "type: feature\n"
        f"status: {status}\n"
        "priority: medium\n"
        "assignee: null\n"
        "created: 2026-09-26\n"
        "depends_on: []\n"
        "---\n\n"
        f"# item {item_id}\n"
    )


def _two_board_repo(repo: Path, order: list[str]) -> None:
    _custom_theme(repo, "alpha", "in_progress")
    _custom_theme(repo, "beta", "review")
    boards = {
        "alpha": {"name": "alpha", "preset": "alpha", "path": "alpha/"},
        "beta": {"name": "beta", "preset": "beta", "path": "beta/"},
    }
    config = {"version": "2.0", "boards": [boards[name] for name in order]}
    (repo / ".kanban" / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    _write_item(repo, "alpha", "FEAT-001", "doing")
    _write_item(repo, "beta", "FEAT-002", "doing")
    _clear_theme_cache()


@pytest.mark.parametrize("order", [["alpha", "beta"], ["beta", "alpha"]])
def test_multiboard_native_status_read_through_own_board(
    repo: Path, runner: CliRunner, wide: io.StringIO, order: list[str]
) -> None:
    _two_board_repo(repo, order)
    items = {i["id"]: i["status"] for i in _json(runner, ["list", "--json"])}  # type: ignore[union-attr,index]
    assert items == {"FEAT-001": "in_progress", "FEAT-002": "review"}
    assert _json(runner, ["show", "FEAT-001", "--json"])["status"] == "in_progress"  # type: ignore[index]
    assert _json(runner, ["show", "FEAT-002", "--json"])["status"] == "review"  # type: ignore[index]


@pytest.mark.parametrize("order", [["alpha", "beta"], ["beta", "alpha"]])
def test_multiboard_native_status_labelled_back_as_doing(
    repo: Path, runner: CliRunner, wide: io.StringIO, order: list[str]
) -> None:
    """Both read back as each board's own `doing` in the human renderers."""
    _two_board_repo(repo, order)
    for item_id in ("FEAT-001", "FEAT-002"):
        assert _show_status(runner, wide, item_id) == "doing"


# ---------------------------------------------------------------------------
# 3. Loose sanity: a large hdd scan doesn't reload the theme per item
# ---------------------------------------------------------------------------


def test_hdd_scan_does_not_load_theme_per_item(
    repo: Path, runner: CliRunner, wide: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yurtle_kanban import cli
    from yurtle_kanban import config as config_mod

    _invoke(runner, ["init", "--theme", "hdd"])
    first = _created_id(runner, wide, ["create", "idea", "template"])
    template = next(p for p in repo.rglob(f"{first}-*.md") if ".git" not in p.parts)
    body = re.sub(r"(?m)^status: .*$", "status: active", template.read_text())
    n_items = 60
    for n in range(2, n_items + 1):
        new_id = first.replace("001", f"{n:03d}")
        (template.parent / f"{new_id}-item.md").write_text(body.replace(first, new_id))

    service = cli.get_service()
    calls = 0
    real = config_mod._load_builtin_theme

    def counting(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(config_mod, "_load_builtin_theme", counting)
    items = service.scan()
    assert len(items) == n_items
    # loose: well under once per item, whatever the exact memoisation
    assert calls < n_items // 4, f"{calls} theme loads for {n_items} items"
