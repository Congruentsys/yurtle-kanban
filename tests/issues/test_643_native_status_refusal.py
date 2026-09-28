"""Issue #643 — an unknown-status refusal lists a jumble of names.

On main (40ec2ee) a nautical `move EXP-001 active` prints
'Valid statuses: approaching, approaching_port, arrived, backlog, blocked, done,
harbor, in_progress, provisioning, ready, review, stranded, underway': canonical
names, native names and folded alias keys, alphabetised.

Decided ([steer] on #643, bucket 2): the refusal lists each status ONCE, in the
item's theme's native spelling, in canonical workflow order (the `WorkItemStatus`
order: backlog, ready, in_progress, review, done, blocked). Nautical lists
`harbor, provisioning, underway, approaching, arrived, stranded`. CLI `move` and MCP
`kanban_move_item` give the same list. Canonical names and aliases are still
accepted, just not listed. A theme with no native names lists the canonical ones.
The stale `_get_reverse_status_mapping` docstring ("For nautical ... returns empty
dict") is fixed too.

A status the theme doesn't map is listed under its canonical name (that is its
spelling in that theme), so hdd lists `draft, ready, active, review, complete,
abandoned`: derived below from themes/hdd.yaml, not hard-coded.

Helpers follow test_587 / test_604. CLI moves use `--force --skip-gates
--no-commit` so only name resolution is under test.
"""

from __future__ import annotations

import inspect
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
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"

ITEM_TYPE: dict[str, str] = {"software": "feature", "nautical": "expedition", "hdd": "idea"}

# canonical workflow order
CANONICAL_ORDER: list[str] = [s.value for s in WorkItemStatus]

NAUTICAL_LIST = ["harbor", "provisioning", "underway", "approaching", "arrived", "stranded"]

# a name each theme must refuse (another theme's)
FOREIGN: dict[str, str] = {"nautical": "active", "hdd": "underway", "software": "harbor"}


def _fold(name: str) -> str:
    return name.lower().replace("-", "_").replace(" ", "_")


def _expected_list(theme: str) -> list[str]:
    """Each canonical status once, in canonical order, spelt as `theme` spells it:
    its `status_mappings` name (the last listed wins, as `move` writes it), else the
    canonical name."""
    data = yaml.safe_load((THEMES_DIR / f"{theme}.yaml").read_text())
    native: dict[str, str] = {}
    for name, canonical in (data.get("status_mappings") or {}).items():
        native[str(canonical)] = str(name)
    return [native.get(c, c) for c in CANONICAL_ORDER]


# ---------------------------------------------------------------------------
# Fixtures / helpers (after test_587 / test_604)
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


def _single(buf: io.StringIO, theme: str) -> str:
    _ok(buf, ["init", "--theme", theme])
    text = _ok(buf, ["create", ITEM_TYPE[theme], f"a {theme} item"])
    match = re.search(r"Created (\S+):", text)
    assert match, text
    return match.group(1)


def _item_file(repo: Path, item_id: str) -> Path:
    files = list(glob_outside_git(repo, f"{item_id}-*.md"))
    assert len(files) == 1, files
    return files[0]


def _move(buf: io.StringIO, item_id: str, name: str) -> tuple[Result, str]:
    return _run(buf, ["move", item_id, name, "--force", "--skip-gates", "--no-commit"])


def _status(buf: io.StringIO, item_id: str) -> str:
    return str(json.loads(_ok(buf, ["show", item_id, "--json"]))["status"])


def _mcp(repo: Path, item_id: str, name: str) -> dict[str, Any]:
    return KanbanMCPServer(repo_root=repo).handle_tool_call(
        "kanban_move_item", {"item_id": item_id, "new_status": name}
    )


def _listed(text: str) -> list[str]:
    """The last comma-separated list of names in the refusal."""
    lists = re.findall(r"[A-Za-z_-]+(?:,[ \t]*[A-Za-z_-]+)+", text)
    assert lists, f"refusal lists no names:\n{text}"
    return [n.strip() for n in lists[-1].split(",")]


def _assert_list(text: str, expected: list[str], typed: str) -> None:
    listed = _listed(text)
    assert listed == expected, (
        f"refusal lists {listed}, want {expected} (native spelling, canonical order, "
        f"each once):\n{text}"
    )
    # nothing else anywhere in the message: no alias, no canonical duplicate
    words = {_fold(w) for w in re.findall(r"[A-Za-z_-]+", text)}
    every = set(CANONICAL_ORDER) | {"approaching_port", "harbor", "provisioning",
                                    "underway", "approaching", "arrived", "stranded",
                                    "draft", "active", "complete", "abandoned"}
    stray = sorted((every - set(expected) - {_fold(typed)}) & words)
    assert not stray, f"refusal also names {stray}:\n{text}"


# ---------------------------------------------------------------------------
# 0. Derivation pinned: nautical's derived list is the decision's literal list
# ---------------------------------------------------------------------------


def test_nautical_derivation_matches_decision() -> None:
    assert _expected_list("nautical") == NAUTICAL_LIST
    assert _expected_list("software") == CANONICAL_ORDER
    hdd = _expected_list("hdd")
    assert {"draft", "active", "complete", "abandoned"} <= set(hdd)


# ---------------------------------------------------------------------------
# (a) nautical CLI
# ---------------------------------------------------------------------------


def test_a_nautical_cli_refusal_lists_native_canonical_order(
    repo: Path, buf: io.StringIO
) -> None:
    item_id = _single(buf, "nautical")
    path = _item_file(repo, item_id)
    before = path.read_bytes()
    result, text = _move(buf, item_id, "active")
    assert result.exit_code != 0, f"`move {item_id} active` accepted:\n{text}"
    assert path.read_bytes() == before
    listed = _listed(text)
    for bad in ("approaching_port", "in_progress", "backlog"):
        assert bad not in listed, f"refusal lists {bad!r}:\n{text}"
    assert all(listed.count(n) == 1 for n in listed), f"duplicates in {listed}"
    _assert_list(text, NAUTICAL_LIST, "active")


# ---------------------------------------------------------------------------
# (b) nautical MCP: same list
# ---------------------------------------------------------------------------


def test_b_nautical_mcp_refusal_lists_same(repo: Path, buf: io.StringIO) -> None:
    item_id = _single(buf, "nautical")
    result = _mcp(repo, item_id, "active")
    assert "error" in result and not result.get("success"), result
    message = str(result["error"])
    _assert_list(message, NAUTICAL_LIST, "active")


def test_b_cli_and_mcp_lists_agree(repo: Path, buf: io.StringIO) -> None:
    item_id = _single(buf, "nautical")
    _, text = _move(buf, item_id, "active")
    mcp = _mcp(repo, item_id, "active")
    assert _listed(text) == _listed(str(mcp["error"]))


# ---------------------------------------------------------------------------
# (c) hdd, derived from themes/hdd.yaml; (d) software, canonical names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("via", ["cli", "mcp"])
@pytest.mark.parametrize("theme", ["hdd", "software"])
def test_cd_refusal_lists_theme_statuses(
    repo: Path, buf: io.StringIO, theme: str, via: str
) -> None:
    item_id = _single(buf, theme)
    name = FOREIGN[theme]
    if via == "cli":
        result, text = _move(buf, item_id, name)
        assert result.exit_code != 0, f"`move {item_id} {name}` accepted:\n{text}"
    else:
        mcp = _mcp(repo, item_id, name)
        assert "error" in mcp, mcp
        text = str(mcp["error"])
    _assert_list(text, _expected_list(theme), name)


# ---------------------------------------------------------------------------
# (e) canonical and alias names still accepted (controls)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ready", "provisioning"])
def test_e_nautical_canonical_and_native_still_accepted(
    repo: Path, buf: io.StringIO, name: str
) -> None:
    item_id = _single(buf, "nautical")
    result, text = _move(buf, item_id, name)
    assert result.exit_code == 0, f"`move {item_id} {name}` refused:\n{text}"
    assert _status(buf, item_id) == "ready"


# ---------------------------------------------------------------------------
# (f) the stale docstring
# ---------------------------------------------------------------------------


def test_f_reverse_mapping_docstring_not_stale() -> None:
    doc = inspect.getdoc(KanbanService._get_reverse_status_mapping) or ""
    assert not re.search(r"nautical[^\n]*empty", doc, re.IGNORECASE), doc
