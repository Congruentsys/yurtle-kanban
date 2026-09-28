"""Issue #604 — follow-ups from the review of PR #600 (#587).

1. MCP `kanban_move_item` accepts only the six canonical names: its schema is an
   enum of them and `_move_item` parses with `WorkItemStatus.from_string`. It must
   resolve `new_status` through `service.resolve_status_name` (the item's own
   theme: native + canonical names), write the theme's native spelling, and refuse
   another theme's name with an error that names the legal ones. The schema no
   longer restricts `new_status` to an enum.
2. Nautical's names come from a hard-coded `_THEME_STATUS_NAMES` table that
   `legal_status_names` applies before the theme's own `status_mappings`, so a
   repo-local `.kanban/themes/nautical.yaml` can't remap or drop them. They move
   into themes/nautical.yaml `status_mappings` and the table goes. The board's
   other column names become names too: `stranded` (blocked) and
   `approaching_port` (review), beside the five existing ones.
3. spec's hard-coded entry is redundant since #588 (spec.yaml `status_mappings`);
   it goes with the table, and spec's names still work, from the theme file.

Consequence of (2), pinned here: nautical now has `status_mappings`, so `move`
writes nautical's native names (`underway`, `stranded`) as it does hdd's and
spec's, and they scan back to the canonical status. Which of `approaching` /
`approaching_port` is *written* for review is left to the implementation.

CLI moves use `--force --skip-gates --no-commit` to isolate name resolution from
transition legality (as in test_587). MCP has no force, so MCP moves go from a
state where the target is legal (default table / hdd `transitions`), reaching it
first with a canonical name where needed.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result
from rich.console import Console

from tests.issues._snapshot import glob_outside_git
from yurtle_kanban import cli
from yurtle_kanban import service as service_mod
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"

ITEM_TYPE: dict[str, str] = {
    "software": "feature",
    "nautical": "expedition",
    "hdd": "idea",
    "spec": "issue",
    "custom": "feature",
}

CANONICAL = ("backlog", "ready", "in_progress", "review", "done", "blocked")

# nautical's names after #604: the five from the old table + stranded, approaching_port
NAUTICAL_NAMES: dict[str, str] = {
    "harbor": "backlog",
    "provisioning": "ready",
    "underway": "in_progress",
    "approaching": "review",
    "approaching_port": "review",
    "arrived": "done",
    "stranded": "blocked",
}


def _fold(name: str) -> str:
    return name.lower().replace("-", "_").replace(" ", "_")


# ---------------------------------------------------------------------------
# Fixtures / helpers (after test_587)
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
    _git_init(tmp_path)
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def buf(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=out, width=300, color_system=None, force_terminal=False)
    )
    return out


def _run(buf: io.StringIO, args: list[str]) -> tuple[Result, str]:
    buf.seek(0)
    buf.truncate()
    result = CliRunner().invoke(main, args)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    return result, buf.getvalue() + result.output


def _ok(buf: io.StringIO, args: list[str]) -> str:
    result, text = _run(buf, args)
    assert result.exit_code == 0, text
    return text


def _create(buf: io.StringIO, theme: str) -> str:
    text = _ok(buf, ["create", ITEM_TYPE[theme], f"a {theme} item"])
    match = re.search(r"Created (\S+):", text)
    assert match, text
    return match.group(1)


def _item_file(repo: Path, item_id: str) -> Path:
    files = list(glob_outside_git(repo, f"{item_id}-*.md"))
    assert len(files) == 1, files
    return files[0]


def _file_status(repo: Path, item_id: str) -> str:
    front = _item_file(repo, item_id).read_text().split("---", 2)[1]
    return str(yaml.safe_load(front)["status"])


def _status(buf: io.StringIO, item_id: str) -> str:
    return str(json.loads(_ok(buf, ["show", item_id, "--json"]))["status"])


def _cli_move(buf: io.StringIO, item_id: str, name: str) -> tuple[Result, str]:
    return _run(buf, ["move", item_id, name, "--force", "--skip-gates", "--no-commit"])


def _write_theme(repo: Path, name: str, theme: dict[str, Any]) -> None:
    themes = repo / ".kanban" / "themes"
    themes.mkdir(parents=True, exist_ok=True)
    (themes / f"{name}.yaml").write_text(yaml.safe_dump(theme, sort_keys=False))
    _clear_theme_cache()


def _builtin(name: str) -> dict[str, Any]:
    return yaml.safe_load((THEMES_DIR / f"{name}.yaml").read_text())


def _custom_theme(repo: Path) -> None:
    """test_587 round 2's custom theme: software + keys with `-`/space/capitals."""
    theme = _builtin("software")
    theme["theme"]["name"] = "custom"
    theme["status_mappings"] = {
        "on-hold": "blocked",
        "In Review": "review",
        "doing": "in_progress",
    }
    _write_theme(repo, "custom", theme)


def _setup(repo: Path, buf: io.StringIO, theme: str, layout: str = "single") -> str:
    """One item of `theme`: on its only board (single), or on a multi-board repo
    that also has a nautical board (multi; nautical's own item is on the default)."""
    if theme == "custom":
        _custom_theme(repo)
    if layout == "single":
        _ok(buf, ["init", "--theme", theme])
    elif theme == "spec":
        # spec's types declare no path, so `create` puts them on the default board:
        # make spec the default and put the nautical board second
        _ok(buf, ["init", "--theme", "spec"])
        _ok(buf, ["board-add", "second", "--preset", "nautical", "--path", "second/"])
        _clear_theme_cache()
    else:
        _ok(buf, ["init", "--theme", "nautical"])
        second = "hdd" if theme == "nautical" else theme
        _ok(buf, ["board-add", "second", "--preset", second, "--path", "second/"])
        _clear_theme_cache()
    return _create(buf, theme)


def _mcp(repo: Path, item_id: str, name: str) -> dict[str, Any]:
    """`kanban_move_item` through a fresh server (a fresh scan each call)."""
    return KanbanMCPServer(repo_root=repo).handle_tool_call(
        "kanban_move_item", {"item_id": item_id, "new_status": name}
    )


def _mcp_ok(repo: Path, item_id: str, name: str) -> dict[str, Any]:
    result = _mcp(repo, item_id, name)
    assert "error" not in result and result.get("success"), (
        f"MCP move {item_id} {name!r} failed: {result}"
    )
    return result


# ---------------------------------------------------------------------------
# 1. MCP kanban_move_item: the item's own names, native spelling written
# ---------------------------------------------------------------------------


def test_mcp_move_schema_has_no_status_enum() -> None:
    (tool,) = [t for t in KanbanMCPServer().get_tools() if t["name"] == "kanban_move_item"]
    prop = tool["inputSchema"]["properties"]["new_status"]
    assert prop.get("type") == "string"
    assert "enum" not in prop, f"new_status still restricted: {prop['enum']}"


# theme, [canonical steps to a state the target is legal from], name, written, canonical
MCP_ACCEPT = [
    ("hdd", [], "active", "active", "in_progress"),
    ("hdd", [], "Abandoned", "abandoned", "blocked"),
    ("nautical", ["ready"], "underway", "underway", "in_progress"),
    ("nautical", [], "stranded", "stranded", "blocked"),
    ("nautical", [], "Provisioning", "provisioning", "ready"),
    ("spec", ["ready"], "implementing", "implementing", "in_progress"),
    ("spec", [], "proposed", "proposed", "ready"),
    ("custom", [], "on-hold", "on-hold", "blocked"),
    ("custom", [], "On_Hold", "on-hold", "blocked"),
    ("custom", ["ready"], "doing", "doing", "in_progress"),
]


@pytest.mark.parametrize("layout", ["single", "multi"])
@pytest.mark.parametrize(
    ("theme", "steps", "name", "written", "canonical"),
    MCP_ACCEPT,
    ids=[f"{t}-{n}" for t, _, n, _, _ in MCP_ACCEPT],
)
def test_mcp_move_accepts_item_native_name(
    repo: Path,
    buf: io.StringIO,
    layout: str,
    theme: str,
    steps: list[str],
    name: str,
    written: str,
    canonical: str,
) -> None:
    item_id = _setup(repo, buf, theme, layout)
    for step in steps:
        _mcp_ok(repo, item_id, step)
    result = _mcp_ok(repo, item_id, name)
    assert result["item"]["status"] == canonical  # JSON stays canonical
    assert _file_status(repo, item_id) == written  # the theme's own spelling
    assert _status(buf, item_id) == canonical  # and it scans back


def test_mcp_move_canonical_names_still_accepted(repo: Path, buf: io.StringIO) -> None:
    item_id = _setup(repo, buf, "software")
    for name in ("ready", "in_progress", "blocked"):
        assert _mcp_ok(repo, item_id, name)["item"]["status"] == name


# theme, another theme's name, names the error must offer (the theme's own
# spelling; a renamed status's canonical name isn't listed since #643)
MCP_REFUSE = [
    ("nautical", "active", ["underway", "stranded"]),
    ("nautical", "on-hold", ["harbor", "provisioning"]),
    ("hdd", "underway", ["active", "abandoned"]),
    ("hdd", "implementing", ["draft", "complete"]),
    ("software", "stranded", list(CANONICAL)),
    ("spec", "active", ["implementing", "accepted"]),
    ("custom", "abandoned", ["on-hold", "doing"]),
]


@pytest.mark.parametrize("layout", ["single", "multi"])
@pytest.mark.parametrize(
    ("theme", "name", "offered"), MCP_REFUSE, ids=[f"{t}-{n}" for t, n, _ in MCP_REFUSE]
)
def test_mcp_move_refuses_other_theme_name(
    repo: Path, buf: io.StringIO, layout: str, theme: str, name: str, offered: list[str]
) -> None:
    if theme == "software" and layout == "multi":
        pytest.skip("software board beside nautical: covered by the single layout")
    item_id = _setup(repo, buf, theme, layout)
    path = _item_file(repo, item_id)
    before = path.read_bytes()
    result = _mcp(repo, item_id, name)
    assert "error" in result and not result.get("success"), (
        f"MCP move {item_id} {name!r} ({theme}) accepted: {result}"
    )
    assert path.read_bytes() == before
    message = str(result["error"])
    words = {_fold(w) for w in re.findall(r"[A-Za-z_-]+", message)}
    missing = [n for n in offered if _fold(n) not in words]
    assert not missing, f"error doesn't name legal {missing}: {message}"


# ---------------------------------------------------------------------------
# 2. Nautical's names live in nautical.yaml; the hard-coded table is gone
# ---------------------------------------------------------------------------


def test_hardcoded_theme_status_table_gone() -> None:
    table = getattr(service_mod, "_THEME_STATUS_NAMES", None)
    assert not table, f"_THEME_STATUS_NAMES still hard-codes {sorted(table)}"


def test_nautical_names_in_theme_status_mappings() -> None:
    mappings = _builtin("nautical").get("status_mappings") or {}
    folded = {_fold(str(k)): str(v) for k, v in mappings.items()}
    assert {n: folded.get(n) for n in NAUTICAL_NAMES} == NAUTICAL_NAMES


def test_spec_names_in_theme_status_mappings() -> None:
    assert _builtin("spec")["status_mappings"] == {
        "draft": "backlog",
        "proposed": "ready",
        "implementing": "in_progress",
        "accepted": "done",
    }


NAUTICAL_CLI = [
    ("stranded", "blocked"),
    ("Stranded", "blocked"),
    ("approaching_port", "review"),
    ("Approaching Port", "review"),
    ("approaching-port", "review"),
    ("approaching", "review"),
    ("underway", "in_progress"),
    ("harbor", "backlog"),
]


@pytest.mark.parametrize("layout", ["single", "multi"])
@pytest.mark.parametrize(("name", "canonical"), NAUTICAL_CLI, ids=[n for n, _ in NAUTICAL_CLI])
def test_nautical_column_names_accepted(
    repo: Path, buf: io.StringIO, layout: str, name: str, canonical: str
) -> None:
    item_id = _setup(repo, buf, "nautical", layout)
    if canonical == "backlog":
        _ok(buf, ["move", item_id, "ready", "--force", "--skip-gates", "--no-commit"])
    result, text = _cli_move(buf, item_id, name)
    assert result.exit_code == 0, f"`move {item_id} {name!r}` refused:\n{text}"
    assert _status(buf, item_id) == canonical


@pytest.mark.parametrize("layout", ["single", "multi"])
@pytest.mark.parametrize(
    ("typed", "written", "canonical"),
    [("in_progress", "underway", "in_progress"), ("blocked", "stranded", "blocked")],
)
def test_nautical_move_writes_native_and_scans_back(
    repo: Path, buf: io.StringIO, layout: str, typed: str, written: str, canonical: str
) -> None:
    item_id = _setup(repo, buf, "nautical", layout)
    _ok(buf, ["move", item_id, typed, "--force", "--skip-gates", "--no-commit"])
    assert _file_status(repo, item_id) == written
    assert _status(buf, item_id) == canonical


def _nautical_override(repo: Path) -> None:
    """A repo-local nautical.yaml: `arrived` dropped, `underway` remapped to review,
    a new `docked` for done."""
    theme = _builtin("nautical")
    mappings = {
        str(k): str(v) for k, v in (theme.get("status_mappings") or {}).items()
        if _fold(str(k)) != "arrived"
    }
    mappings = {k: ("review" if _fold(k) == "underway" else v) for k, v in mappings.items()}
    mappings.setdefault("underway", "review")
    mappings["docked"] = "done"
    theme["status_mappings"] = mappings
    _write_theme(repo, "nautical", theme)


@pytest.mark.parametrize("layout", ["single", "multi"])
def test_nautical_override_drops_a_name(repo: Path, buf: io.StringIO, layout: str) -> None:
    _nautical_override(repo)
    item_id = _setup(repo, buf, "nautical", layout)
    path = _item_file(repo, item_id)
    before = path.read_bytes()
    result, text = _cli_move(buf, item_id, "arrived")
    assert result.exit_code != 0, f"dropped name `arrived` still accepted:\n{text}"
    assert path.read_bytes() == before
    mcp = _mcp(repo, item_id, "arrived")
    assert "error" in mcp, f"MCP accepts dropped `arrived`: {mcp}"


@pytest.mark.parametrize("layout", ["single", "multi"])
def test_nautical_override_remaps_a_name(repo: Path, buf: io.StringIO, layout: str) -> None:
    _nautical_override(repo)
    item_id = _setup(repo, buf, "nautical", layout)
    result, text = _cli_move(buf, item_id, "underway")
    assert result.exit_code == 0, text
    assert _status(buf, item_id) == "review", "override's `underway: review` not applied"


@pytest.mark.parametrize("layout", ["single", "multi"])
def test_nautical_override_adds_a_name(repo: Path, buf: io.StringIO, layout: str) -> None:
    _nautical_override(repo)
    item_id = _setup(repo, buf, "nautical", layout)
    result, text = _cli_move(buf, item_id, "docked")
    assert result.exit_code == 0, text
    assert _status(buf, item_id) == "done"


# ---------------------------------------------------------------------------
# 3. spec's names come from spec.yaml, so an override governs them too
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["single", "multi"])
@pytest.mark.parametrize(
    ("name", "canonical"),
    [("draft", "backlog"), ("proposed", "ready"), ("implementing", "in_progress"),
     ("accepted", "done")],
)
def test_spec_names_accepted(
    repo: Path, buf: io.StringIO, layout: str, name: str, canonical: str
) -> None:
    item_id = _setup(repo, buf, "spec", layout)
    if canonical == "backlog":
        _ok(buf, ["move", item_id, "ready", "--force", "--skip-gates", "--no-commit"])
    result, text = _cli_move(buf, item_id, name)
    assert result.exit_code == 0, text
    assert _file_status(repo, item_id) == name
    assert _status(buf, item_id) == canonical


@pytest.mark.parametrize("layout", ["single", "multi"])
def test_spec_override_drops_a_name(repo: Path, buf: io.StringIO, layout: str) -> None:
    theme = _builtin("spec")
    theme["status_mappings"] = {
        k: v for k, v in theme["status_mappings"].items() if k != "implementing"
    }
    _write_theme(repo, "spec", theme)
    item_id = _setup(repo, buf, "spec", layout)
    path = _item_file(repo, item_id)
    before = path.read_bytes()
    result, text = _cli_move(buf, item_id, "implementing")
    assert result.exit_code != 0, f"dropped name `implementing` still accepted:\n{text}"
    assert path.read_bytes() == before
