"""Issue #457 — a non-list theme ``transitions.<status>`` entry crashes ``move``;
``KanbanService.get_allowed_transitions`` ignores the theme's ``transitions``.

Follow-ups from the review of PR #455 (#450):

- ``transitions: {draft: null}`` → ``move X active`` raises ``TypeError: argument
  of type 'NoneType' is not iterable`` at ``to_native in allowed`` (single-board
  since #450, multi-board before). ``draft: 5`` likewise.
- ``get_allowed_transitions`` reads only the workflow file / default rules, so on
  hdd it offers ``ready`` from draft (which ``move`` refuses) and not ``active``
  (which ``move`` accepts).

Decided behaviour:

1. At theme load (``config._drop_bad_sections``), a ``transitions.<status>`` entry
   that is neither a list nor a string (null, int, bool, mapping) is dropped with
   ONE warning naming the file and ``transitions.<status>``. The theme then behaves
   as one without that key: nothing is allowed from that status, so ``move`` refuses
   with "Invalid transition from draft to active" (observed today for a missing key).
2. A lone string (``draft: active``) is read as a one-element list, with no warning
   — the #432 precedent (lone-string hook ``item_types``).
3. ``get_allowed_transitions(item) -> list[str]`` (canonical status values, as its
   default path returns) uses the same theme lookup as ``_validate_transition``:
   single-board ``config.get_theme()``, multi-board the board's preset. It agrees
   with ``move`` for every candidate status. Themes without transitions: unchanged.
4. Controls: #450's tests stay green; valid hdd transitions load unchanged, no
   warnings.

NOTE for the driver: ``test_363_theme_nested_shapes.py::TestLoaderDropsBadEntry::
test_control_status_mappings_and_transitions_entries_kept[transitions]`` pins
``transitions: {todo: 5}`` as kept; decision 1 supersedes it for ``transitions``.
"""

from __future__ import annotations

import io
import logging
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

LOGGER = "yurtle-kanban"
THEME = "mytheme"
THEME_FILE = f"{THEME}.yaml"
HDD_FILE = Path(__file__).resolve().parents[2] / "themes" / "hdd.yaml"

# non-list, non-str `transitions.draft` values: dropped with a warning
DROPPED_SHAPES: dict[str, Any] = {
    "null": None,
    "int": 5,
    "bool": True,
    "mapping": {"active": 1},
}

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = (
    'version: "2.0"\nboards:\n  - name: research\n'
    f"    preset: {THEME}\n    path: work/\n    scan_paths:\n      - work/\n"
)
CFGS = [pytest.param(SINGLE_CFG, id="single"), pytest.param(MULTI_CFG, id="multi")]


# --- fixtures / helpers (as in test_363 / test_450) -------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def warnings_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, attached straight to the yurtle-kanban logger (whatever its propagation)."""
    log = logging.getLogger(LOGGER)
    log.addHandler(caplog.handler)
    old_level = log.level
    log.setLevel(logging.WARNING)
    caplog.handler.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        log.removeHandler(caplog.handler)
        log.setLevel(old_level)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    records = {id(r): r for r in caplog.records}.values()
    return [r.getMessage() for r in records if r.name == LOGGER and r.levelno >= logging.WARNING]


def _names_entry(message: str, dotted: str) -> bool:
    """The warning names ``section.entry`` (quoted or bare, as a whole token)."""
    return re.search(rf"(?<![\w.]){re.escape(dotted)}(?![\w.])", message) is not None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _hdd() -> dict[str, Any]:
    data = yaml.safe_load(HDD_FILE.read_text())
    assert data["transitions"]["draft"] == ["active", "abandoned"]  # premise
    return data


def _hdd_with(draft: Any, *, drop: bool = False) -> dict[str, Any]:
    """hdd with ``transitions.draft`` set to ``draft`` (or removed if ``drop``)."""
    data = _hdd()
    if drop:
        del data["transitions"]["draft"]
    else:
        data["transitions"]["draft"] = draft
    return data


def _repo(root: Path, config: str | None, theme: dict[str, Any]) -> Path:
    """A git repo at ``root`` with ``.kanban/config.yaml`` + ``themes/mytheme.yaml``."""
    root.mkdir(parents=True, exist_ok=True)
    kanban = root / ".kanban"
    (kanban / "themes").mkdir(parents=True)
    if config is not None:
        (kanban / "config.yaml").write_text(config)
    (kanban / "themes" / THEME_FILE).write_text(yaml.safe_dump(theme, sort_keys=False))
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    (root / "work").mkdir()
    (root / "work" / ".keep").write_text("")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def _seed(repo: Path, item_id: str, status: str, item_type: str = "idea") -> None:
    (repo / "work" / f"{item_id}.md").write_text(
        f"---\nid: {item_id}\ntype: {item_type}\nstatus: {status}\n"
        f"title: Item {item_id}\n---\n\n# Item {item_id}\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")


def _load(repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any] | None:
    monkeypatch.chdir(repo)
    return config_mod._load_builtin_theme(THEME, repo)


def _service(repo: Path) -> KanbanService:
    config_mod._theme_cache.clear()
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


@pytest.fixture
def wide(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Route cli's console to a wide, colourless buffer."""
    buf = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=buf, width=300, color_system=None, force_terminal=False)
    )
    return buf


def _move(
    repo: Path, monkeypatch: pytest.MonkeyPatch, buf: io.StringIO, item_id: str, column: str
) -> tuple[Result, str]:
    """Run `move` via the CLI; a non-SystemExit exception is a crash."""
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    buf.seek(0)
    buf.truncate()
    result = CliRunner().invoke(
        main, ["move", item_id, column, "--no-commit", "--skip-gates"]
    )
    out = buf.getvalue() + (result.output or "")
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    return result, out


def _status(repo: Path, item_id: str) -> WorkItemStatus:
    item = _service(repo).get_item(item_id)
    assert item is not None, item_id
    return item.status


# ---------------------------------------------------------------------------
# 1. Loader — a non-list transitions.<status> entry
# ---------------------------------------------------------------------------


class TestLoader:
    @pytest.mark.parametrize("shape", list(DROPPED_SHAPES))
    def test_bad_entry_dropped_once_with_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _hdd_with(DROPPED_SHAPES[shape]))
        expected = _hdd_with(None, drop=True)

        first = _load(repo, monkeypatch)
        second = config_mod._load_builtin_theme(THEME, repo)  # cached lookup

        assert first == expected, first
        assert second == expected, second
        hits = [
            m for m in _warnings(warnings_log)
            if THEME_FILE in m and _names_entry(m, "transitions.draft")
        ]
        assert len(hits) == 1, _warnings(warnings_log)

    def test_lone_string_is_one_element_list_no_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        """#432 precedent: a lone string means that one status."""
        repo = _repo(tmp_path / "repo", None, _hdd_with("active"))

        loaded = _load(repo, monkeypatch)

        assert loaded == _hdd_with(["active"]), loaded
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_valid_hdd_loads_unchanged_no_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _hdd())

        assert _load(repo, monkeypatch) == _hdd()
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_builtin_hdd_loads_no_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        loaded = config_mod._load_builtin_theme("hdd", tmp_path)
        assert loaded is not None
        assert loaded["transitions"] == _hdd()["transitions"]
        assert not _warnings(warnings_log), _warnings(warnings_log)


# ---------------------------------------------------------------------------
# 2. move — no crash; the entry behaves as dropped (or as a one-element list)
# ---------------------------------------------------------------------------


class TestMove:
    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("shape", list(DROPPED_SHAPES))
    def test_move_from_dropped_entry_refused_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wide: io.StringIO,
        shape: str, cfg: str,
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd_with(DROPPED_SHAPES[shape]))
        _seed(repo, "IDEA-001", "backlog")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "active")

        assert result.exit_code != 0, out
        assert "Invalid transition from draft to active" in out, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.BACKLOG

    @pytest.mark.parametrize("cfg", CFGS)
    def test_control_move_from_missing_key_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wide: io.StringIO, cfg: str,
    ) -> None:
        """What a dropped entry must match: the theme without `transitions.draft`."""
        repo = _repo(tmp_path / "repo", cfg, _hdd_with(None, drop=True))
        _seed(repo, "IDEA-001", "backlog")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "active")

        assert result.exit_code != 0, out
        assert "Invalid transition from draft to active" in out, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.BACKLOG

    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("shape", list(DROPPED_SHAPES))
    def test_move_other_statuses_unaffected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wide: io.StringIO,
        shape: str, cfg: str,
    ) -> None:
        """Siblings survive: active -> complete is still allowed."""
        repo = _repo(tmp_path / "repo", cfg, _hdd_with(DROPPED_SHAPES[shape]))
        _seed(repo, "IDEA-001", "in_progress")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "complete")

        assert result.exit_code == 0, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.DONE

    @pytest.mark.parametrize("cfg", CFGS)
    def test_lone_string_allows_that_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wide: io.StringIO, cfg: str,
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd_with("active"))
        _seed(repo, "IDEA-001", "backlog")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "active")

        assert result.exit_code == 0, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.IN_PROGRESS

    @pytest.mark.parametrize("cfg", CFGS)
    def test_lone_string_refuses_others(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wide: io.StringIO, cfg: str,
    ) -> None:
        """`draft: active` is ["active"]: abandoned is refused."""
        repo = _repo(tmp_path / "repo", cfg, _hdd_with("active"))
        _seed(repo, "IDEA-001", "backlog")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "abandoned")

        assert result.exit_code != 0, out
        assert "Invalid transition from draft to abandoned" in out, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.BACKLOG


# ---------------------------------------------------------------------------
# 3. get_allowed_transitions follows the theme, and agrees with move
# ---------------------------------------------------------------------------

# hdd, in canonical statuses: (seeded status, what the theme allows from it)
HDD_ALLOWED: dict[str, set[str]] = {
    "backlog": {"in_progress", "blocked"},        # draft -> active, abandoned
    "in_progress": {"done", "blocked", "backlog"},  # active -> complete, abandoned, draft
    "done": set(),                                 # complete -> (terminal)
    "blocked": {"backlog"},                        # abandoned -> draft
}


def _allowed(repo: Path, item_id: str) -> list[str]:
    service = _service(repo)
    item = service.get_item(item_id)
    assert item is not None, item_id
    allowed = service.get_allowed_transitions(item)
    assert isinstance(allowed, list), allowed
    assert all(isinstance(s, str) for s in allowed), allowed
    assert len(allowed) == len(set(allowed)), allowed
    return allowed


def _move_ok(repo: Path, item_id: str, status: WorkItemStatus) -> bool:
    """Does `move_item` (validation only: no WIP, no gates, no commit) accept it?"""
    service = _service(repo)
    try:
        service.move_item(item_id, status, commit=False, skip_wip_check=True, skip_gates=True)
    except ValueError as e:
        assert "Invalid transition" in str(e), e
        return False
    return True


def _hdd_repo(root: Path, cfg: str, status: str) -> Path:
    repo = _repo(root, cfg, _hdd())
    _seed(repo, "IDEA-001", status)
    return repo


class TestGetAllowedTransitions:
    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("status", list(HDD_ALLOWED))
    def test_hdd_returns_theme_transitions(
        self, tmp_path: Path, cfg: str, status: str
    ) -> None:
        repo = _hdd_repo(tmp_path / "repo", cfg, status)

        assert set(_allowed(repo, "IDEA-001")) == HDD_ALLOWED[status]

    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize(
        "target", [s for s in WorkItemStatus if s != WorkItemStatus.BACKLOG],
        ids=lambda s: s.value,
    )
    def test_hdd_draft_agrees_with_move(
        self, tmp_path: Path, cfg: str, target: WorkItemStatus
    ) -> None:
        repo = _hdd_repo(tmp_path / "repo", cfg, "backlog")
        offered = target.value in _allowed(repo, "IDEA-001")

        assert offered == _move_ok(repo, "IDEA-001", target), (
            f"get_allowed_transitions {'offers' if offered else 'omits'} {target.value}; "
            "move disagrees"
        )

    def test_builtin_hdd_single_board_draft(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`init --theme hdd` (the built-in theme, no override) as in #450."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "T")
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(main, ["init", "--theme", "hdd"])
        assert result.exit_code == 0, result.output
        config_mod._theme_cache.clear()
        created = CliRunner().invoke(main, ["create", "idea", "x"])
        assert created.exit_code == 0, created.output
        service = _service(repo)
        items = service.scan()
        assert len(items) == 1, items
        assert items[0].status == WorkItemStatus.BACKLOG

        assert set(service.get_allowed_transitions(items[0])) == HDD_ALLOWED["backlog"]

    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("shape", list(DROPPED_SHAPES))
    def test_dropped_entry_allows_nothing(
        self, tmp_path: Path, cfg: str, shape: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd_with(DROPPED_SHAPES[shape]))
        _seed(repo, "IDEA-001", "backlog")

        assert _allowed(repo, "IDEA-001") == []

    @pytest.mark.parametrize("cfg", CFGS)
    def test_lone_string_allows_that_one(self, tmp_path: Path, cfg: str) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd_with("active"))
        _seed(repo, "IDEA-001", "backlog")

        assert _allowed(repo, "IDEA-001") == ["in_progress"]

    @pytest.mark.parametrize(
        ("theme", "item_type"), [("software", "feature"), ("nautical", "expedition")]
    )
    def test_control_theme_without_transitions_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, theme: str, item_type: str
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "T")
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(main, ["init", "--theme", theme])
        assert result.exit_code == 0, result.output
        config_mod._theme_cache.clear()
        created = CliRunner().invoke(main, ["create", item_type, "x"])
        assert created.exit_code == 0, created.output
        service = _service(repo)
        items = service.scan()
        assert len(items) == 1, items

        assert service.get_allowed_transitions(items[0]) == ["ready", "blocked"]

