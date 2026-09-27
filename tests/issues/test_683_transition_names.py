"""Issue #683 — theme `transitions` that use a status name losing the reverse map.

`legal_next` decides legality with `reverse.get(status) == native`. With
`doing: in_progress` then `wip: in_progress`, `wip` wins the reverse map, so
`transitions` written with `doing` (as a target or as a source) never match:
in_progress is unreachable from ready, and nothing is legal from in_progress.

Decided ([steer] on #683, bucket 2, option 2): a theme's `transitions` names, on
BOTH sides, are resolved to their canonical status through that theme's own names,
folded as `move` folds them, before legality is decided. `doing` and `wip` both
mean in_progress, whichever wins the reverse map; no warning about the losing name
(the trap is removed, not reported). A transition name that resolves to no status
is dropped with ONE load-time warning naming the theme file and the bad name (as
#613 does for bad theme entries), and nothing crashes.

Covered (custom-theme helpers from test_615 / test_659):
(a) `doing: in_progress` then `wip: in_progress`; transitions `ready: [doing]`,
    `doing: [done]`: ready -> in_progress and in_progress -> done are legal in
    `move` (no --force), `show --json` next_statuses and `states --json`.
(b) the same with the target spelt `wip: in-progress`; and transitions spelt with
    folded variants (`Doing`, `in-progress`).
(c) `ready: [nonsense, doing]` (and a source `nonsense: [done]`): the bad name is
    dropped with exactly one warning per command naming the theme file and the
    name; the good names still work.
(d) controls: shipped themes behave as before — nautical harbor -> provisioning
    (default table), hdd's own transitions table.
"""

from __future__ import annotations

import io
import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from tests.issues.test_615_folded_status_keys import (
    LAYOUTS,
    THEME_FILE,
    _file_status,
    _names,
    _repo,
    _warnings,
)
from tests.issues.test_643_native_status_refusal import (
    _ok,
    buf,  # noqa: F401 (fixture)
    repo,  # noqa: F401 (fixture)
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import WorkItemStatus

LOGGER = "yurtle-kanban"
CANONICAL = [s.value for s in WorkItemStatus]
# legality is under test: no --force
MOVE = ["--skip-gates", "--no-commit"]
BAD = "nonsense"


# --- fixtures / helpers -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def warnings_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
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


def _theme(mappings: dict[str, str], transitions: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "theme": {"name": "acme"},
        "item_types": {"task": {"id_prefix": "TASK", "path": "work/tasks/"}},
        "columns": {
            "backlog": {"name": "Backlog", "order": 1},
            "ready": {"name": "Ready", "order": 2},
            "doing": {"name": "Doing", "order": 3},
            "done": {"name": "Done", "order": 4},
        },
        "status_mappings": dict(mappings),
        "transitions": {k: list(v) for k, v in transitions.items()},
    }


def _invoke(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    args: list[str],
) -> tuple[Result, list[str]]:
    """One command as a fresh process runs it (fresh theme cache): its result and
    the warnings it logged. A crash is a failure; the exit code is the caller's."""
    monkeypatch.chdir(root)
    config_mod._theme_cache.clear()
    caplog.clear()
    result = CliRunner().invoke(main, args)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`{' '.join(args)}` crashed: {result.exception!r}") from (
            result.exception
        )
    assert "Traceback" not in (result.output or ""), result.output
    return result, _warnings(caplog)


def _okw(
    root: Path, mp: pytest.MonkeyPatch, cl: pytest.LogCaptureFixture, args: list[str]
) -> tuple[Result, list[str]]:
    result, warned = _invoke(root, mp, cl, args)
    assert result.exit_code == 0, f"`{' '.join(args)}` failed:\n{result.output}"
    return result, warned


def _create(root: Path, mp: pytest.MonkeyPatch, cl: pytest.LogCaptureFixture) -> str:
    result, _ = _okw(root, mp, cl, ["create", "task", "an item"])
    match = re.search(r"Created (\S+):", result.output)
    assert match, result.output
    return match.group(1)


def _show_next(
    root: Path, mp: pytest.MonkeyPatch, cl: pytest.LogCaptureFixture, item_id: str
) -> list[str]:
    result, _ = _okw(root, mp, cl, ["show", item_id, "--json"])
    return list(json.loads(result.stdout)["next_statuses"])


def _states(
    root: Path, mp: pytest.MonkeyPatch, cl: pytest.LogCaptureFixture
) -> tuple[dict[str, list[str]], list[str]]:
    """Canonical next statuses per canonical status from `states --json` (one board)."""
    result, warned = _okw(root, mp, cl, ["states", "--json"])
    data = json.loads(result.stdout)
    assert isinstance(data, list) and len(data) == 1, data
    got = {s: [] for s in CANONICAL}
    for state in data[0]["states"]:
        got[state["canonical"]] = [n["canonical"] for n in state["next"]]
    return got, warned


def _transition_warnings(warned: list[str]) -> list[str]:
    return [w for w in warned if "transitions" in w]


# ---------------------------------------------------------------------------
# (a), (b) a transition name that loses the reverse map still means its status
# ---------------------------------------------------------------------------

LOSER_TRANSITIONS = {"backlog": ["ready"], "ready": ["doing"], "doing": ["done"]}

CASES: dict[str, tuple[dict[str, str], dict[str, list[str]]]] = {
    # (a) same target spelling; `wip` wins the reverse map, transitions say `doing`
    "a-same-target": (
        {"doing": "in_progress", "wip": "in_progress"}, LOSER_TRANSITIONS,
    ),
    # (b) the winning name's target spelt with a dash
    "b-dash-target": (
        {"doing": "in_progress", "wip": "in-progress"}, LOSER_TRANSITIONS,
    ),
    # (b) transitions spelt as `move` folds: `Doing`, canonical `in-progress`
    "b-folded-names": (
        {"doing": "in_progress", "wip": "in_progress"},
        {"backlog": ["ready"], "ready": ["in-progress"], "Doing": ["done"]},
    ),
}
EXPECTED_NEXT = {
    **{s: [] for s in CANONICAL},
    "backlog": ["ready"],
    "ready": ["in_progress"],
    "in_progress": ["done"],
}
WINNER = "wip"

PARAMS = [(c, lay) for c in CASES for lay in LAYOUTS]
IDS = [f"{c}-{lay}" for c, lay in PARAMS]


@pytest.fixture
def custom(
    tmp_path: Path, request: pytest.FixtureRequest
) -> Path:
    case, layout = request.param
    mappings, transitions = CASES[case]
    return _repo(tmp_path / "repo", LAYOUTS[layout], _theme(mappings, transitions))


@pytest.mark.parametrize("case", list(CASES))
def test_ab_fixture_is_the_trap(case: str) -> None:
    """Non-vacuity: the theme keeps order, `wip` wins, and the transitions name a
    status by some name OTHER than the winner."""
    mappings, transitions = CASES[case]
    dumped = yaml.safe_load(yaml.safe_dump(_theme(mappings, transitions), sort_keys=False))
    assert list(dumped["status_mappings"]) == list(mappings)
    assert list(mappings)[-1] == WINNER
    names = {n for k, v in transitions.items() for n in [k, *v]}
    assert WINNER not in names


@pytest.mark.parametrize("custom", PARAMS, ids=IDS, indirect=True)
def test_ab_move_through_losing_name_is_legal(
    custom: Path, monkeypatch: pytest.MonkeyPatch, warnings_log: pytest.LogCaptureFixture
) -> None:
    root = custom
    item_id = _create(root, monkeypatch, warnings_log)
    _okw(root, monkeypatch, warnings_log, ["move", item_id, "ready", *MOVE])
    # ready -> in_progress, named by the loser: legal, written as the winner
    _okw(root, monkeypatch, warnings_log, ["move", item_id, "doing", *MOVE])
    assert _file_status(root, item_id) == WINNER
    # in_progress -> done: legal although the source is keyed by the loser
    _okw(root, monkeypatch, warnings_log, ["move", item_id, "done", *MOVE])
    assert _file_status(root, item_id) == "done"


@pytest.mark.parametrize("target", ["wip", "in_progress", "in-progress"])
@pytest.mark.parametrize("custom", PARAMS, ids=IDS, indirect=True)
def test_ab_move_ready_to_in_progress_any_name(
    custom: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    target: str,
) -> None:
    root = custom
    item_id = _create(root, monkeypatch, warnings_log)
    _okw(root, monkeypatch, warnings_log, ["move", item_id, "ready", *MOVE])
    _okw(root, monkeypatch, warnings_log, ["move", item_id, target, *MOVE])
    assert _file_status(root, item_id) == WINNER


@pytest.mark.parametrize("custom", PARAMS, ids=IDS, indirect=True)
def test_ab_show_next_statuses(
    custom: Path, monkeypatch: pytest.MonkeyPatch, warnings_log: pytest.LogCaptureFixture
) -> None:
    root = custom
    item_id = _create(root, monkeypatch, warnings_log)
    assert _show_next(root, monkeypatch, warnings_log, item_id) == ["ready"]
    _okw(root, monkeypatch, warnings_log, ["move", item_id, "ready", *MOVE])
    assert _show_next(root, monkeypatch, warnings_log, item_id) == ["in_progress"]
    # reach in_progress whatever legality says, then ask what's next
    _okw(root, monkeypatch, warnings_log, ["move", item_id, "in_progress", "--force", *MOVE])
    assert _show_next(root, monkeypatch, warnings_log, item_id) == ["done"]


@pytest.mark.parametrize("custom", PARAMS, ids=IDS, indirect=True)
def test_ab_states_json(
    custom: Path, monkeypatch: pytest.MonkeyPatch, warnings_log: pytest.LogCaptureFixture
) -> None:
    got, warned = _states(custom, monkeypatch, warnings_log)
    assert got == EXPECTED_NEXT, got
    # the trap is removed, not reported: no warning about the transitions
    assert _transition_warnings(warned) == [], warned


# ---------------------------------------------------------------------------
# (c) a transition name that resolves to nothing: dropped, one warning, no crash
# ---------------------------------------------------------------------------

BAD_CASES: dict[str, dict[str, list[str]]] = {
    "target": {"backlog": ["ready"], "ready": [BAD, "doing"], "doing": ["done"]},
    "source": {"backlog": ["ready"], "ready": ["doing"], "doing": ["done"], BAD: ["done"]},
}
BAD_MAPPINGS = {"doing": "in_progress"}
BAD_PARAMS = [(c, lay) for c in BAD_CASES for lay in LAYOUTS]
BAD_IDS = [f"{c}-{lay}" for c, lay in BAD_PARAMS]


@pytest.fixture
def bad(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    case, layout = request.param
    return _repo(tmp_path / "repo", LAYOUTS[layout], _theme(BAD_MAPPINGS, BAD_CASES[case]))


def _about_bad(warned: list[str]) -> list[str]:
    return [w for w in warned if _names(w, BAD)]


def _one_bad_warning(warned: list[str]) -> None:
    about = _about_bad(warned)
    assert len(about) == 1, warned
    assert THEME_FILE in about[0], about


def test_c_bad_name_resolves_to_nothing() -> None:
    """Non-vacuity: `nonsense` is no status by any spelling."""
    with pytest.raises(ValueError):
        WorkItemStatus.from_string(BAD)
    assert BAD not in BAD_MAPPINGS


@pytest.mark.parametrize("bad", BAD_PARAMS, ids=BAD_IDS, indirect=True)
def test_c_states_warns_once_and_keeps_good_names(
    bad: Path, monkeypatch: pytest.MonkeyPatch, warnings_log: pytest.LogCaptureFixture
) -> None:
    got, warned = _states(bad, monkeypatch, warnings_log)
    _one_bad_warning(warned)
    assert got == EXPECTED_NEXT, got


@pytest.mark.parametrize("bad", BAD_PARAMS, ids=BAD_IDS, indirect=True)
def test_c_move_and_show_warn_once_no_crash(
    bad: Path, monkeypatch: pytest.MonkeyPatch, warnings_log: pytest.LogCaptureFixture
) -> None:
    root = bad
    item_id = _create(root, monkeypatch, warnings_log)
    _, warned = _okw(root, monkeypatch, warnings_log, ["move", item_id, "ready", *MOVE])
    _one_bad_warning(warned)
    _, warned = _okw(root, monkeypatch, warnings_log, ["show", item_id, "--json"])
    _one_bad_warning(warned)
    _, warned = _okw(root, monkeypatch, warnings_log, ["move", item_id, "doing", *MOVE])
    _one_bad_warning(warned)
    assert _file_status(root, item_id) == "doing"
    # the bad name is never a status `move` accepts
    result, _ = _invoke(root, monkeypatch, warnings_log, ["move", item_id, BAD, *MOVE])
    assert result.exit_code != 0, result.output


# ---------------------------------------------------------------------------
# (d) controls: shipped themes unchanged
# ---------------------------------------------------------------------------

HDD_NEXT = {
    **{s: [] for s in CANONICAL},
    "backlog": ["in_progress", "blocked"],
    "in_progress": ["done", "blocked", "backlog"],
    "blocked": ["backlog"],
}


def _states_cli() -> dict[str, list[str]]:
    result = CliRunner().invoke(main, ["states", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    got = {s: [] for s in CANONICAL}
    for state in data[0]["states"]:
        got[state["canonical"]] = [n["canonical"] for n in state["next"]]
    return got


def test_d_hdd_transitions_unchanged(repo: Path, buf: io.StringIO) -> None:  # noqa: F811
    _ok(buf, ["init", "--theme", "hdd"])
    assert _states_cli() == HDD_NEXT
    text = _ok(buf, ["create", "idea", "an idea"])
    match = re.search(r"Created (\S+):", text)
    assert match, text
    item_id = match.group(1)
    _ok(buf, ["move", item_id, "active", *MOVE])
    result = CliRunner().invoke(main, ["move", item_id, "ready", *MOVE])
    assert result.exit_code != 0, result.output  # hdd lists no route to ready


def test_d_nautical_harbor_to_provisioning(repo: Path, buf: io.StringIO) -> None:  # noqa: F811
    _ok(buf, ["init", "--theme", "nautical"])
    got = _states_cli()
    assert "ready" in got["backlog"], got
    text = _ok(buf, ["create", "expedition", "a voyage"])
    match = re.search(r"Created (\S+):", text)
    assert match, text
    item_id = match.group(1)
    _ok(buf, ["move", item_id, "provisioning", *MOVE])
    shown = json.loads(CliRunner().invoke(main, ["show", item_id, "--json"]).stdout)
    assert shown["status"] == "ready", shown
