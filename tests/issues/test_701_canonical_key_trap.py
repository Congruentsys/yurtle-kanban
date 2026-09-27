"""#701: a `status_mappings` key that is a canonical name (or alias) of a DIFFERENT
status than its target can never be used — canonical names win (#587, #683) — so
it is dropped at theme load with one warning, and `move <that status>` writes the
canonical name, which reads back as moved."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_615_folded_status_keys import (  # noqa: F401  (fixtures)
    LAYOUTS,
    MOVE,
    _clean_theme_cache,
    _created_id,
    _file_status,
    _repo,
    _run,
    _show_status,
    _theme,
    warnings_log,
)

TRAP = {"todo": "backlog", "doing": "in_progress", "done": "review", "shipped": "done"}


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_trap_key_dropped_with_one_warning_and_review_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, layout: str  # noqa: F811
) -> None:
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(TRAP))
    result, _ = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    item_id = _created_id(result)
    _, warned = _run(repo, monkeypatch, warnings_log, ["move", item_id, "review", *MOVE])
    about = [w for w in warned if "status_mappings.done" in w]
    assert len(about) == 1, warned
    assert _file_status(repo, item_id) == "review"
    assert _show_status(repo, monkeypatch, warnings_log, item_id) == "review"


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_key_matching_its_own_target_is_kept_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, layout: str  # noqa: F811
) -> None:
    mappings = {"todo": "backlog", "done": "done", "doing": "in_progress"}
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(mappings))
    _, warned = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    assert not [w for w in warned if "status_mappings" in w], warned
