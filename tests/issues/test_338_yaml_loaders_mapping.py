"""Issue #338 — config/run/theme YAML loaders that parse to a list or bare scalar.

Follow-ups from the review of PR #334 (#330). Each loader does
``yaml.safe_load(...) or {}`` and then treats the result as a mapping, so a
non-empty YAML list or scalar escapes as an AttributeError/TypeError:

- ``KanbanConfig.load`` (``config.py``): ``data.get("version", …)`` → AttributeError;
  every CLI command shows a raw exception instead of the #220 ``Invalid …`` line.
- ``KanbanService.get_experiment_runs``: a run's ``config.yaml`` = ``- x`` →
  AttributeError; the loop only catches ``yaml.YAMLError``, so one bad run breaks
  the whole listing (and ``experiment status``).
- ``KanbanService.update_run_status``: run ``config.yaml`` = ``- x`` →
  ``TypeError: list indices must be integers``.
- ``_load_builtin_theme``: returns (and caches) whatever ``safe_load`` gives; a
  theme file ``- columns`` / ``columns`` makes ``board`` raise TypeError, ``init
  --theme`` raise AttributeError, ``board-add --preset`` accept it as a valid
  preset, and ``get_theme()`` return a non-mapping.

Decided behaviour (bucket 2, #321/#330 precedent):

1. ``KanbanConfig.load`` of a list/scalar config raises ``ValueError`` saying the
   file must be a mapping; the CLI prints its one-line ``Invalid <path>: …`` and
   exits 1, no traceback. Control: an empty config.yaml loads the defaults (today).
2. ``get_experiment_runs``: a run whose config.yaml is a list/scalar is skipped with
   a logged warning; the other runs are still listed; nothing raises.
3. ``update_run_status``: a list/scalar run config raises a clean ``ValueError``
   naming the file (not TypeError) and leaves the file unchanged. (No CLI command
   calls it today.)
4. A theme file that parses to a list/scalar is treated like a missing theme, with a
   warning: ``_load_builtin_theme`` returns None, ``get_theme()`` returns None,
   ``board`` falls back as for an unknown theme (default columns, "<Name> Board"
   title, exit 0), ``init --theme`` scaffolds as for an unknown theme, and
   ``board-add --preset`` refuses it as ``Unknown preset``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

LOGGER = "yurtle-kanban"
THEME = "mytheme"

# Non-empty, parseable, not a mapping: one list and one scalar per loader.
CONFIG_NON_MAPPING = {"list": "- a\n- b\n", "scalar": "just text\n"}
RUN_NON_MAPPING = {"list": "- x\n", "scalar": "just text\n"}
# `columns` / `item_types` make the membership tests downstream pass, so the
# non-mapping reaches a subscript or `.get()` (the crashes in the issue).
THEME_NON_MAPPING = {"list": "- columns\n", "scalar": "columns\n"}
INIT_THEME_NON_MAPPING = {"list": "- item_types\n", "scalar": "item_types\n"}
CASES = ["list", "scalar"]

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = (
    'version: "2.0"\nboards:\n  - name: devboard\n'
    f"    preset: {THEME}\n    path: work/\n"
)


# --- fixtures / helpers ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    """The cache is keyed by resolved theme file (#287); clear it anyway so a
    non-mapping entry cached by one test can never be seen by another."""
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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(root: Path, config: str | None, theme: str | None = None) -> Path:
    """A git repo at ``root`` with ``.kanban/config.yaml`` (+ ``themes/mytheme.yaml``)."""
    root.mkdir(parents=True, exist_ok=True)
    kanban = root / ".kanban"
    kanban.mkdir()
    if config is not None:
        (kanban / "config.yaml").write_text(config)
    if theme is not None:
        (kanban / "themes").mkdir()
        (kanban / "themes" / f"{THEME}.yaml").write_text(theme)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    (root / "work").mkdir()
    (root / "work" / ".keep").write_text("")
    return root


def _invoke(repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]):
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, args)


def _no_crash(result) -> None:
    """No exception other than SystemExit reached the runner, no traceback printed."""
    out = result.output or ""
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception


# ---------------------------------------------------------------------------
# 1. KanbanConfig.load — a non-mapping config is an invalid config
# ---------------------------------------------------------------------------


class TestConfigLoad:
    @pytest.mark.parametrize("case", CASES)
    def test_load_raises_value_error_mapping(self, tmp_path: Path, case: str) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_NON_MAPPING[case])
        with pytest.raises(ValueError) as exc:
            KanbanConfig.load(cfg)
        assert "mapping" in str(exc.value).lower(), str(exc.value)

    @pytest.mark.parametrize("case", CASES)
    def test_cli_list_exits_one_with_invalid_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        repo = _repo(tmp_path / "repo", CONFIG_NON_MAPPING[case])
        result = _invoke(repo, monkeypatch, ["list"])
        _no_crash(result)
        out = result.output or ""
        assert result.exit_code == 1, out
        flat = " ".join(out.split())
        assert "Invalid" in flat and "config.yaml" in flat, out
        assert "mapping" in flat.lower(), out

    def test_control_empty_config_loads_defaults(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("")
        config = KanbanConfig.load(cfg)
        assert config.theme == "software"
        assert not config.is_multi_board

    def test_control_empty_config_cli_list_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", "")
        result = _invoke(repo, monkeypatch, ["list"])
        _no_crash(result)
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 2. get_experiment_runs — a non-mapping run is skipped with a warning
# ---------------------------------------------------------------------------

EXPR = "EXPR-1"
GOOD_RUN = "2026-01-01T00-00-00"
BAD_RUN = "2026-01-02T00-00-00"


def _runs_repo(root: Path, bad: str) -> Path:
    runs = root / "research" / "runs" / EXPR
    (runs / GOOD_RUN).mkdir(parents=True)
    (runs / GOOD_RUN / "config.yaml").write_text("being: alpha\nstatus: complete\n")
    (runs / BAD_RUN).mkdir()
    (runs / BAD_RUN / "config.yaml").write_text(bad)
    return root


class TestExperimentRuns:
    @pytest.mark.parametrize("case", CASES)
    def test_bad_run_skipped_others_listed(
        self, tmp_path: Path, case: str, warnings_log: pytest.LogCaptureFixture
    ) -> None:
        repo = _runs_repo(tmp_path, RUN_NON_MAPPING[case])
        service = KanbanService(KanbanConfig(), repo)
        runs = service.get_experiment_runs(EXPR)
        assert [r["run_path"].name for r in runs] == [GOOD_RUN]
        assert runs[0]["being"] == "alpha" and runs[0]["status"] == "complete"
        assert any(BAD_RUN in m for m in _warnings(warnings_log)), _warnings(warnings_log)

    @pytest.mark.parametrize("case", CASES)
    def test_cli_experiment_status_json_lists_good_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        repo = _repo(tmp_path / "repo", None)
        _runs_repo(repo, RUN_NON_MAPPING[case])
        result = _invoke(repo, monkeypatch, ["experiment", "status", EXPR, "--json"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        start = result.output.index("[")
        runs = json.loads(result.output[start:])
        assert [Path(r["run_path"]).name for r in runs] == [GOOD_RUN]

    def test_control_all_good_runs_listed(self, tmp_path: Path) -> None:
        repo = _runs_repo(tmp_path, "being: beta\n")
        service = KanbanService(KanbanConfig(), repo)
        names = [r["run_path"].name for r in service.get_experiment_runs(EXPR)]
        assert names == [BAD_RUN, GOOD_RUN]


# ---------------------------------------------------------------------------
# 3. update_run_status — a non-mapping run config is a clean ValueError
# ---------------------------------------------------------------------------


class TestUpdateRunStatus:
    @pytest.mark.parametrize("case", CASES)
    def test_raises_value_error_naming_file_unchanged(self, tmp_path: Path, case: str) -> None:
        run = tmp_path / "research" / "runs" / EXPR / BAD_RUN
        run.mkdir(parents=True)
        cfg = run / "config.yaml"
        cfg.write_text(RUN_NON_MAPPING[case])
        service = KanbanService(KanbanConfig(), tmp_path)
        with pytest.raises(ValueError) as exc:
            service.update_run_status(run, "complete", outcome="VALIDATED")
        msg = str(exc.value)
        assert "config.yaml" in msg and BAD_RUN in msg, msg
        assert cfg.read_text() == RUN_NON_MAPPING[case]

    def test_control_mapping_updated(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        run.mkdir()
        (run / "config.yaml").write_text("being: alpha\nstatus: pending\n")
        KanbanService(KanbanConfig(), tmp_path).update_run_status(run, "complete")
        assert "status: complete" in (run / "config.yaml").read_text()

    def test_control_empty_config_updated(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        run.mkdir()
        (run / "config.yaml").write_text("")
        KanbanService(KanbanConfig(), tmp_path).update_run_status(run, "running")
        assert "status: running" in (run / "config.yaml").read_text()


# ---------------------------------------------------------------------------
# 4. Themes — a non-mapping theme file is treated as missing, with a warning
# ---------------------------------------------------------------------------


def _theme_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [m for m in _warnings(caplog) if THEME in m]


class TestThemeLoader:
    @pytest.mark.parametrize("case", CASES)
    def test_load_builtin_theme_returns_none_and_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        case: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", None, THEME_NON_MAPPING[case])
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(THEME, repo) is None
        assert _theme_warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("case", CASES)
    def test_non_mapping_not_served_from_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        repo = _repo(tmp_path / "repo", None, THEME_NON_MAPPING[case])
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(THEME, repo) is None
        assert config_mod._load_builtin_theme(THEME, repo) is None  # second, cached lookup
        assert all(isinstance(v, dict) or v is None for v in config_mod._theme_cache.values())

    @pytest.mark.parametrize("case", CASES)
    def test_single_board_get_theme_none_and_load_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        case: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, THEME_NON_MAPPING[case])
        monkeypatch.chdir(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")  # config.py:103
        assert config.theme == THEME
        assert _theme_warnings(warnings_log), _warnings(warnings_log)
        assert config.get_theme() is None  # config.py:506

    @pytest.mark.parametrize("case", CASES)
    def test_multi_board_get_theme_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        repo = _repo(tmp_path / "repo", MULTI_CFG, THEME_NON_MAPPING[case])
        monkeypatch.chdir(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        assert config.get_theme("devboard") is None  # config.py:503 → :173
        board = config.get_board("devboard")
        assert board is not None and board.get_theme(repo) is None

    def test_control_mapping_theme_loads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", None, "name: Acme\n")
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(THEME, repo) == {"name": "Acme"}


class TestThemeCli:
    @pytest.mark.parametrize("case", CASES)
    def test_board_falls_back_like_unknown_theme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, THEME_NON_MAPPING[case])
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        # today's unknown-theme fallback: the default columns under a "<Name> Board" title
        assert "Mytheme Board" in result.output, result.output
        assert "Backlog" in result.output and "In Progress" in result.output, result.output

    def test_control_board_unknown_theme_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG)  # no mytheme.yaml anywhere
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Mytheme Board" in result.output and "Backlog" in result.output

    @pytest.mark.parametrize("case", CASES)
    def test_init_with_non_mapping_theme_like_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        repo = tmp_path / "repo"
        themes = repo / ".kanban" / "themes"
        themes.mkdir(parents=True)
        (themes / f"{THEME}.yaml").write_text(INIT_THEME_NON_MAPPING[case])
        _git(repo, "init", "-b", "main")
        result = _invoke(repo, monkeypatch, ["init", "--theme", THEME])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        text = (repo / ".kanban" / "config.yaml").read_text()
        assert f"theme: {THEME}" in text and "root: work/" in text, text  # unknown-theme scaffold

    def test_control_init_unknown_theme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        result = _invoke(repo, monkeypatch, ["init", "--theme", "nosuch"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "root: work/" in (repo / ".kanban" / "config.yaml").read_text()

    @pytest.mark.parametrize("case", CASES)
    def test_board_add_refuses_non_mapping_preset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        repo = _repo(tmp_path / "repo", "", THEME_NON_MAPPING[case])
        result = _invoke(
            repo, monkeypatch, ["board-add", "extra", "--preset", THEME, "--path", "extra/"]
        )
        _no_crash(result)
        assert result.exit_code == 1, result.output
        assert "Unknown preset" in result.output, result.output
