"""Issue #378 — field-level shapes inside ``columns.<id>`` / ``item_types.<id>``.

Follow-up from the review of PR #376 (#363), whose guard (``config._drop_bad_sections``)
checks the sections and each entry's shape, but not the fields inside an entry.
Via a custom ``.kanban/themes/mytheme.yaml``, observed today:

- ``columns.<id>.wip_limit: "two"`` / ``[1]`` → ``board`` → ``TypeError: '>' not supported``;
  ``wip_limit: true`` renders as ``(n/True)``;
- ``columns.<id>.order: "z"`` / ``[1]`` / null → ``board`` → ``TypeError: '<' not supported``;
- ``item_types.<id>.path: 5`` / ``[a]`` / null / ``true`` → ``list`` and ``create task hi``
  → ``TypeError: expected str, bytes or os.PathLike``;
- ``item_types.<id>.id_prefix: [a]`` / ``5`` / null / ``true`` → ``create task hi``
  → ``TypeError`` (``prefix + "-"``);
- a non-str column key (``columns: {5: {...}}``) → ``board`` →
  ``AttributeError: 'int' object has no attribute 'title'``.

Decided behaviour (extends #363 one level down, into the fields):

1. ``columns.<id>.wip_limit`` / ``.order`` must be an int, not a bool. Otherwise the
   field is dropped with ONE warning naming the file and the path (e.g.
   ``columns.todo.wip_limit``): no WIP limit / the default order. A null
   ``wip_limit`` already means "no limit" today, so it is kept; a null ``order``
   crashes today, so it is dropped.
2. ``item_types.<id>.path`` / ``.id_prefix`` must be a str. Otherwise dropped with one
   warning, and the type falls back as when the field is absent (observed today:
   no ``path`` → ``work/tasks/``; no ``id_prefix`` → ``TASK``).
3. A column whose key is not a str is dropped with one warning.
4. ``board`` / ``list`` / ``create task hi`` don't raise, and print what they print
   for the same theme with that field (or column) absent.
5. Controls: valid values kept; ``columns.<id>.name: [1]`` and ``status_aliases``
   entries don't crash today and are left alone; the built-in themes load with no
   warnings; a theme left empty still falls through to the built-in (#365).
"""

from __future__ import annotations

import copy
import logging
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

LOGGER = "yurtle-kanban"
THEME = "mytheme"
THEME_FILE = f"{THEME}.yaml"
BUILTIN_NAMES = ("hdd", "nautical", "software", "spec")

ABSENT = object()  # a field left out of the theme

# Bad shapes per field. `wip_limit: null` is NOT here: it means "no limit" today.
BAD_FIELDS: dict[tuple[str, str], dict[str, Any]] = {
    ("columns", "wip_limit"): {
        "str": "two",
        "list": [1],
        "bool": True,
        "float": 1.5,
        "dict": {"a": 1},
    },
    ("columns", "order"): {
        "str": "z",
        "list": [1],
        "bool": True,
        "float": 1.5,
        "null": None,
        "dict": {"a": 1},
    },
    ("item_types", "path"): {
        "int": 5,
        "list": ["a"],
        "bool": True,
        "null": None,
        "dict": {"a": 1},
    },
    ("item_types", "id_prefix"): {
        "int": 5,
        "list": ["a"],
        "bool": True,
        "null": None,
        "dict": {"a": 1},
    },
}

FIELD_CASES = [
    pytest.param(section, fld, shape, id=f"{section}.{fld}-{shape}")
    for (section, fld), shapes in BAD_FIELDS.items()
    for shape in shapes
]

BAD_KEYS: dict[str, Any] = {"int": 5, "bool": True, "null": None, "float": 1.5}

# the bad field goes on the first entry; the entry's other fields and the sibling
# entry are well-formed and must survive
ENTRIES: dict[str, tuple[str, dict[str, Any], str, dict[str, Any]]] = {
    "columns": (
        "in_progress",
        {"name": "In Progress", "order": 2, "wip_limit": 3, "description": "wip"},
        "backlog",
        {"name": "Backlog", "order": 1},
    ),
    "item_types": (
        "task",
        {"id_prefix": "TSK", "path": "work/tasks/", "name": "Task"},
        "bug",
        {"id_prefix": "BUG", "path": "work/bugs/"},
    ),
}

GOOD_THEME: dict[str, Any] = {
    "theme": {"name": "acme", "description": "custom"},
    "item_types": {
        "task": {"id_prefix": "TSK", "path": "work/tasks/"},
        "bug": {"id_prefix": "BUG", "path": "work/bugs/"},
    },
    "columns": {
        "backlog": {"name": "Backlog", "order": 1, "wip_limit": None},
        "in_progress": {"name": "In Progress", "order": 2, "wip_limit": 2},
        "done": {"name": "Done", "order": 3, "wip_limit": 0},
    },
    "status_mappings": {"backlog": "backlog", "in_progress": "in_progress"},
    "transitions": {"backlog": ["in_progress"], "in_progress": ["backlog"]},
}

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = (
    'version: "2.0"\nboards:\n  - name: devboard\n'
    f"    preset: {THEME}\n    path: work/\n"
)
CONFIGS = [pytest.param(SINGLE_CFG, id="single"), pytest.param(MULTI_CFG, id="multi")]


# --- fixtures / helpers (as in test_363) ------------------------------------------------


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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _dump(data: dict[Any, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False)


def _repo(root: Path, config: str | None, theme: str | None = None) -> Path:
    """A git repo at ``root`` with ``.kanban/config.yaml`` (+ ``themes/mytheme.yaml``)."""
    root.mkdir(parents=True, exist_ok=True)
    kanban = root / ".kanban"
    kanban.mkdir()
    if config is not None:
        (kanban / "config.yaml").write_text(config)
    if theme is not None:
        (kanban / "themes").mkdir()
        (kanban / "themes" / THEME_FILE).write_text(theme)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    (root / "work").mkdir()
    (root / "work" / ".keep").write_text("")
    return root


def _item(repo: Path, item_id: str, item_type: str, status: str) -> Path:
    path = repo / "work" / f"{item_id}.md"
    path.write_text(
        f"---\nid: {item_id}\ntype: {item_type}\nstatus: {status}\n"
        f"title: Item {item_id}\n---\n\n# Item {item_id}\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    return path


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


def _names(message: str, dotted: str) -> bool:
    """The warning names ``dotted`` (quoted or bare, as a whole token)."""
    return re.search(rf"(?<![\w.]){re.escape(dotted)}(?![\w.])", message) is not None


def _hits(caplog: pytest.LogCaptureFixture, *dotted: str) -> list[str]:
    """Warnings naming the theme file and any of ``dotted``."""
    return [
        m for m in _warnings(caplog) if THEME_FILE in m and any(_names(m, d) for d in dotted)
    ]


def _load(repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any] | None:
    monkeypatch.chdir(repo)
    return config_mod._load_builtin_theme(THEME, repo)


def _field_theme(section: str, fld: str, value: Any) -> dict[str, Any]:
    """A theme with ``section.<first entry>.fld`` set to ``value`` (or absent)."""
    entry, entry_def, sibling, sibling_def = ENTRIES[section]
    first = {k: v for k, v in entry_def.items() if k != fld}
    if value is not ABSENT:
        first[fld] = value
    return {
        "theme": {"name": "acme"},
        section: {entry: first, sibling: copy.deepcopy(sibling_def)},
    }


def _column_key_theme(key: Any) -> dict[Any, Any]:
    columns: dict[Any, Any] = {} if key is ABSENT else {key: {"name": "Five", "order": 5}}
    columns["backlog"] = {"name": "Backlog", "order": 1}
    columns["in_progress"] = {"name": "In Progress", "order": 2, "wip_limit": 3}
    return {"theme": {"name": "acme"}, "columns": columns}


def _key_names(key: Any) -> tuple[str, ...]:
    """Ways a warning may spell ``columns.<key>``."""
    spelled = {str(key), yaml.safe_dump(key).splitlines()[0]}
    return tuple(f"columns.{s}" for s in spelled)


def _seed(repo: Path) -> None:
    _item(repo, "TASK-001", "task", "in_progress")
    _item(repo, "TASK-002", "task", "in_progress")
    _item(repo, "TASK-003", "task", "backlog")


def _pair(
    tmp_path: Path, cfg: str, bad: dict[Any, Any], ref: dict[Any, Any]
) -> tuple[Path, Path]:
    """Two seeded repos, same-length paths: ``bad`` with the bad field, ``ref`` without."""
    bad_repo = _repo(tmp_path / "bad", cfg, _dump(bad))
    ref_repo = _repo(tmp_path / "ref", cfg, _dump(ref))
    _seed(bad_repo)
    _seed(ref_repo)
    return bad_repo, ref_repo


def _normalized(result, repo: Path) -> str:
    out = result.output or ""
    for p in {str(repo.resolve()), str(repo)}:
        out = out.replace(p, "<repo>")
    return out


def _tree(repo: Path) -> list[str]:
    return sorted(
        p.relative_to(repo).as_posix()
        for p in repo.rglob("*.md")
        if ".git" not in p.parts and ".kanban" not in p.parts
    )


def _same_as_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cfg: str,
    bad: dict[Any, Any],
    ref: dict[Any, Any],
    args: list[str],
) -> None:
    bad_repo, ref_repo = _pair(tmp_path, cfg, bad, ref)
    got = _invoke(bad_repo, monkeypatch, args)
    _no_crash(got)
    want = _invoke(ref_repo, monkeypatch, args)
    _no_crash(want)  # the reference itself must be clean
    assert want.exit_code == 0, want.output
    assert got.exit_code == want.exit_code, got.output
    assert _normalized(got, bad_repo) == _normalized(want, ref_repo)
    assert _tree(bad_repo) == _tree(ref_repo)


# ---------------------------------------------------------------------------
# 1. Loader — a bad field is dropped, once, with one warning naming the path
# ---------------------------------------------------------------------------


class TestLoaderDropsBadField:
    @pytest.mark.parametrize(("section", "fld", "shape"), FIELD_CASES)
    def test_bad_field_dropped_once_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        section: str,
        fld: str,
        shape: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        bad = _field_theme(section, fld, BAD_FIELDS[(section, fld)][shape])
        expected = _field_theme(section, fld, ABSENT)
        repo = _repo(tmp_path / "repo", None, _dump(bad))
        first = _load(repo, monkeypatch)
        second = config_mod._load_builtin_theme(THEME, repo)  # cached lookup
        assert first == expected, first
        assert second == expected, second
        dotted = f"{section}.{ENTRIES[section][0]}.{fld}"
        assert len(_hits(warnings_log, dotted)) == 1, _warnings(warnings_log)
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)

    def test_two_bad_fields_one_warning_each(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        bad = _field_theme("columns", "wip_limit", "two")
        bad["columns"]["in_progress"]["order"] = "z"
        expected = _field_theme("columns", "wip_limit", ABSENT)
        del expected["columns"]["in_progress"]["order"]
        repo = _repo(tmp_path / "repo", None, _dump(bad))
        assert _load(repo, monkeypatch) == expected
        for fld in ("wip_limit", "order"):
            dotted = f"columns.in_progress.{fld}"
            assert len(_hits(warnings_log, dotted)) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("shape", list(BAD_KEYS))
    def test_non_str_column_key_dropped_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        shape: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        key = BAD_KEYS[shape]
        repo = _repo(tmp_path / "repo", None, _dump(_column_key_theme(key)))
        expected = _column_key_theme(ABSENT)
        assert _load(repo, monkeypatch) == expected
        assert config_mod._load_builtin_theme(THEME, repo) == expected
        assert len(_hits(warnings_log, *_key_names(key))) == 1, _warnings(warnings_log)
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)


# ---------------------------------------------------------------------------
# 2. Controls at load — valid values and the non-crashing shapes are left alone
# ---------------------------------------------------------------------------


class TestLoaderControls:
    def test_good_theme_loads_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _dump(GOOD_THEME))
        assert _load(repo, monkeypatch) == GOOD_THEME
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_null_wip_limit_kept(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        theme = _field_theme("columns", "wip_limit", None)
        repo = _repo(tmp_path / "repo", None, _dump(theme))
        assert _load(repo, monkeypatch) == theme
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_non_str_column_name_kept(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        theme = _field_theme("columns", "name", [1])
        repo = _repo(tmp_path / "repo", None, _dump(theme))
        assert _load(repo, monkeypatch) == theme
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_status_aliases_entries_kept(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        theme = {
            "theme": {"name": "acme"},
            "status_aliases": {"backlog": 5, "ready": [1], "done": None, 7: "x"},
        }
        repo = _repo(tmp_path / "repo", None, _dump(theme))
        assert _load(repo, monkeypatch) == theme
        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("name", BUILTIN_NAMES)
    def test_builtin_themes_load_without_warnings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        data = config_mod._load_builtin_theme(name, None)
        assert isinstance(data, dict) and data, data
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_theme_left_empty_falls_through_to_builtin(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        # #365: a repo-local software.yaml with nothing usable yields the built-in
        builtin = config_mod._load_builtin_theme("software", None)
        config_mod._theme_cache.clear()
        repo = tmp_path / "repo"
        themes = repo / ".kanban" / "themes"
        themes.mkdir(parents=True)
        (themes / "software.yaml").write_text(_dump({"columns": 5, "item_types": [1]}))
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme("software", repo) == builtin


# ---------------------------------------------------------------------------
# 3. Public paths — no TypeError/AttributeError; output as with the field absent
# ---------------------------------------------------------------------------


class TestBoard:
    @pytest.mark.parametrize("cfg", CONFIGS)
    @pytest.mark.parametrize("shape", list(BAD_FIELDS[("columns", "wip_limit")]))
    def test_board_bad_wip_limit_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, cfg: str
    ) -> None:
        value = BAD_FIELDS[("columns", "wip_limit")][shape]
        bad = _field_theme("columns", "wip_limit", value)
        ref = _field_theme("columns", "wip_limit", ABSENT)
        _same_as_absent(tmp_path, monkeypatch, cfg, bad, ref, ["board"])

    @pytest.mark.parametrize("cfg", CONFIGS)
    @pytest.mark.parametrize("shape", list(BAD_FIELDS[("columns", "order")]))
    def test_board_bad_order_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, cfg: str
    ) -> None:
        value = BAD_FIELDS[("columns", "order")][shape]
        bad = _field_theme("columns", "order", value)
        ref = _field_theme("columns", "order", ABSENT)
        _same_as_absent(tmp_path, monkeypatch, cfg, bad, ref, ["board"])

    @pytest.mark.parametrize("cfg", CONFIGS)
    @pytest.mark.parametrize("shape", list(BAD_KEYS))
    def test_board_non_str_column_key_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, cfg: str
    ) -> None:
        bad = _column_key_theme(BAD_KEYS[shape])
        ref = _column_key_theme(ABSENT)
        _same_as_absent(tmp_path, monkeypatch, cfg, bad, ref, ["board"])

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_control_board_null_wip_limit_no_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        bad = _field_theme("columns", "wip_limit", None)
        ref = _field_theme("columns", "wip_limit", ABSENT)
        _same_as_absent(tmp_path, monkeypatch, cfg, bad, ref, ["board"])

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_control_board_good_theme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(GOOD_THEME))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "(2/2)" in result.output, result.output  # the valid WIP limit is shown
        for name in ("Backlog", "In Progress", "Done"):
            assert name in result.output, result.output

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_control_board_non_str_column_name_no_crash_today(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(_field_theme("columns", "name", [1])))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output

    def test_control_board_bad_status_aliases_no_crash_today(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        theme = _column_key_theme(ABSENT)
        theme["status_aliases"] = {"backlog": 5, "ready": [1], "done": None}
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _dump(theme))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output


ITEM_TYPE_CASES = [
    pytest.param(fld, shape, id=f"{fld}-{shape}")
    for fld in ("path", "id_prefix")
    for shape in BAD_FIELDS[("item_types", fld)]
]


class TestItemTypes:
    @pytest.mark.parametrize(("fld", "shape"), ITEM_TYPE_CASES)
    def test_list_bad_field_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fld: str, shape: str
    ) -> None:
        bad = _field_theme("item_types", fld, BAD_FIELDS[("item_types", fld)][shape])
        ref = _field_theme("item_types", fld, ABSENT)
        _same_as_absent(tmp_path, monkeypatch, SINGLE_CFG, bad, ref, ["list"])

    @pytest.mark.parametrize(("fld", "shape"), ITEM_TYPE_CASES)
    def test_create_task_bad_field_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fld: str, shape: str
    ) -> None:
        bad = _field_theme("item_types", fld, BAD_FIELDS[("item_types", fld)][shape])
        ref = _field_theme("item_types", fld, ABSENT)
        _same_as_absent(tmp_path, monkeypatch, SINGLE_CFG, bad, ref, ["create", "task", "hi"])

    def test_control_create_task_without_path_goes_to_work_tasks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(
            tmp_path / "repo", SINGLE_CFG, _dump(_field_theme("item_types", "path", ABSENT))
        )
        result = _invoke(repo, monkeypatch, ["create", "task", "hi"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert (repo / "work" / "tasks" / "TSK-001-hi.md").exists(), result.output

    def test_control_create_task_without_id_prefix_uses_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(
            tmp_path / "repo",
            SINGLE_CFG,
            _dump(_field_theme("item_types", "id_prefix", ABSENT)),
        )
        result = _invoke(repo, monkeypatch, ["create", "task", "hi"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert (repo / "work" / "tasks" / "TASK-001-hi.md").exists(), result.output

    def test_control_create_task_good_theme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _dump(GOOD_THEME))
        result = _invoke(repo, monkeypatch, ["create", "task", "hi"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert (repo / "work" / "tasks" / "TSK-001-hi.md").exists(), result.output
