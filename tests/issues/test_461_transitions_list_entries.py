"""Issue #461 — follow-ups from the review of PR #458 (#457).

1. A non-string entry inside a ``transitions.<status>`` list (``draft: [5, active]``,
   ``draft: [{a: 1}, active]``) is kept by the loader, and
   ``get_allowed_transitions`` then raises ``AttributeError`` (``from_string`` on an
   int) / ``TypeError: unhashable type: 'dict'``.
2. The dedupe in ``get_allowed_transitions`` is untested (hdd never maps two natives
   to one canonical status).
3. An unmapped native spelled ``in-progress`` is offered by
   ``get_allowed_transitions`` (``from_string`` normalises it) but ``move`` refuses
   it (it compares exact strings).

Decided behaviour:

1. The theme loader drops a non-string entry inside a ``transitions.<status>`` list
   with ONE warning naming the file and ``transitions.<status>``; the string entries
   are kept (``[5, active]`` -> ``[active]``). ``get_allowed_transitions`` then does
   not crash and returns ``[in_progress]`` for an hdd-like theme; ``move`` still
   works.
2. ``get_allowed_transitions`` returns each canonical status once, also when the
   theme's targets alias the same canonical status (a repeated target, a native and
   its canonical spelling, two natives mapped to one canonical).
3. An unmapped native written ``in-progress`` is not offered, because ``move``
   refuses it: the two agree.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_457_transitions_shapes import (  # noqa: F401 (fixtures)
    CFGS,
    THEME,
    THEME_FILE,
    _allowed,
    _clean_theme_cache,
    _git,
    _hdd,
    _hdd_with,
    _load,
    _move,
    _move_ok,
    _names_entry,
    _repo,
    _seed,
    _status,
    _warnings,
    warnings_log,
    wide,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItemStatus

# non-string entries inside `transitions.draft`: dropped, the strings kept
BAD_ENTRIES: dict[str, Any] = {
    "int": 5,
    "mapping": {"a": 1},
    "bool": True,
    "null": None,
    "list": ["x"],
    "float": 1.5,
}

TARGETS = [s for s in WorkItemStatus if s != WorkItemStatus.BACKLOG]


def _bad_list(shape: str) -> list[Any]:
    return [BAD_ENTRIES[shape], "active"]


def _assert_agrees(repo: Path, item_id: str) -> None:
    """For every target: offered by get_allowed_transitions <=> accepted by move."""
    offered = set(_allowed(repo, item_id))
    for target in TARGETS:
        accepted = _move_ok(repo, item_id, target)
        _git(repo, "reset", "--hard", "-q")  # undo the probe: each target from the seed
        _git(repo, "clean", "-fdq")
        assert (target.value in offered) == accepted, (
            f"get_allowed_transitions {'offers' if target.value in offered else 'omits'} "
            f"{target.value}; move {'accepts' if accepted else 'refuses'} it"
        )


# ---------------------------------------------------------------------------
# 1. Non-string list entries
# ---------------------------------------------------------------------------


class TestLoaderDropsNonStringEntries:
    @pytest.mark.parametrize("shape", list(BAD_ENTRIES))
    def test_entry_dropped_strings_kept_one_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        shape: str,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _hdd_with(_bad_list(shape)))
        expected = _hdd_with(["active"])

        first = _load(repo, monkeypatch)
        second = config_mod._load_builtin_theme(THEME, repo)  # cached lookup

        assert first == expected, first
        assert second == expected, second
        hits = [
            m
            for m in _warnings(warnings_log)
            if THEME_FILE in m and _names_entry(m, "transitions.draft")
        ]
        assert len(hits) == 1, _warnings(warnings_log)

    def test_two_bad_entries_one_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        """`[5, active, {a: 1}, abandoned]` -> `[active, abandoned]`, still one warning."""
        repo = _repo(tmp_path / "repo", None, _hdd_with([5, "active", {"a": 1}, "abandoned"]))

        assert _load(repo, monkeypatch) == _hdd()
        hits = [
            m
            for m in _warnings(warnings_log)
            if THEME_FILE in m and _names_entry(m, "transitions.draft")
        ]
        assert len(hits) == 1, _warnings(warnings_log)

    def test_other_statuses_untouched(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _hdd_with(_bad_list("int")))

        loaded = _load(repo, monkeypatch)

        assert loaded is not None
        assert loaded["transitions"] == {**_hdd()["transitions"], "draft": ["active"]}
        assert not [
            m
            for m in _warnings(warnings_log)
            if any(_names_entry(m, f"transitions.{s}") for s in ("active", "complete", "abandoned"))
        ], _warnings(warnings_log)


class TestNonStringEntryAfterLoad:
    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("shape", ["int", "mapping"])
    def test_get_allowed_transitions_no_crash(self, tmp_path: Path, cfg: str, shape: str) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd_with(_bad_list(shape)))
        _seed(repo, "IDEA-001", "backlog")

        assert _allowed(repo, "IDEA-001") == ["in_progress"]

    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("shape", ["int", "mapping"])
    def test_move_to_kept_entry_works(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        wide: io.StringIO,  # noqa: F811
        cfg: str,
        shape: str,
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd_with(_bad_list(shape)))
        _seed(repo, "IDEA-001", "backlog")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "active")

        assert result.exit_code == 0, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.IN_PROGRESS

    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("shape", ["int", "mapping"])
    def test_move_to_unlisted_refused(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        wide: io.StringIO,  # noqa: F811
        cfg: str,
        shape: str,
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd_with(_bad_list(shape)))
        _seed(repo, "IDEA-001", "backlog")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "abandoned")

        assert result.exit_code != 0, out
        assert "Invalid transition from draft to abandoned" in out, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.BACKLOG

    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("shape", ["int", "mapping"])
    def test_agrees_with_move(self, tmp_path: Path, cfg: str, shape: str) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd_with(_bad_list(shape)))
        _seed(repo, "IDEA-001", "backlog")

        _assert_agrees(repo, "IDEA-001")


# ---------------------------------------------------------------------------
# 2. Dedupe: targets that alias one canonical status
# ---------------------------------------------------------------------------


def _two_natives() -> dict[str, Any]:
    """hdd plus `running`, a second native mapped to in_progress."""
    data = _hdd()
    data["status_mappings"]["running"] = "in_progress"
    data["transitions"]["running"] = ["complete"]
    data["transitions"]["draft"] = ["active", "running"]
    return data


ALIASING: dict[str, Any] = {
    "repeated": lambda: _hdd_with(["active", "active"]),
    "native_and_canonical": lambda: _hdd_with(["active", "in_progress"]),
    "two_natives": _two_natives,
}


class TestDedupe:
    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("theme", list(ALIASING))
    def test_each_canonical_once(self, tmp_path: Path, cfg: str, theme: str) -> None:
        repo = _repo(tmp_path / "repo", cfg, ALIASING[theme]())
        _seed(repo, "IDEA-001", "backlog")

        assert _allowed(repo, "IDEA-001") == ["in_progress"]

    @pytest.mark.parametrize("cfg", CFGS)
    @pytest.mark.parametrize("theme", list(ALIASING))
    def test_agrees_with_move(self, tmp_path: Path, cfg: str, theme: str) -> None:
        repo = _repo(tmp_path / "repo", cfg, ALIASING[theme]())
        _seed(repo, "IDEA-001", "backlog")

        _assert_agrees(repo, "IDEA-001")


# ---------------------------------------------------------------------------
# 3. An unmapped native spelled `in-progress`
# ---------------------------------------------------------------------------


def _unmapped(draft: list[str]) -> dict[str, Any]:
    """hdd with in_progress left unmapped (no `active`), and `transitions.draft`."""
    data = _hdd_with(draft)
    del data["status_mappings"]["active"]
    assert "in_progress" not in data["status_mappings"].values()  # premise
    return data


class TestHyphenSpelling:
    @pytest.mark.parametrize("cfg", CFGS)
    def test_move_refuses_hyphen_spelling(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        wide: io.StringIO,  # noqa: F811
        cfg: str,
    ) -> None:
        """Probe: `move` compares exact strings, so `in-progress` never admits in_progress."""
        repo = _repo(tmp_path / "repo", cfg, _unmapped(["in-progress", "abandoned"]))
        _seed(repo, "IDEA-001", "backlog")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "in_progress")

        assert result.exit_code != 0, out
        assert "Invalid transition from draft to in_progress" in out, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.BACKLOG
        assert not _move_ok(repo, "IDEA-001", WorkItemStatus.IN_PROGRESS)

    @pytest.mark.parametrize("cfg", CFGS)
    def test_hyphen_spelling_not_offered(self, tmp_path: Path, cfg: str) -> None:
        repo = _repo(tmp_path / "repo", cfg, _unmapped(["in-progress", "abandoned"]))
        _seed(repo, "IDEA-001", "backlog")

        assert _allowed(repo, "IDEA-001") == ["blocked"]

    @pytest.mark.parametrize("cfg", CFGS)
    def test_hyphen_spelling_agrees_with_move(self, tmp_path: Path, cfg: str) -> None:
        repo = _repo(tmp_path / "repo", cfg, _unmapped(["in-progress", "abandoned"]))
        _seed(repo, "IDEA-001", "backlog")

        _assert_agrees(repo, "IDEA-001")

    @pytest.mark.parametrize("cfg", CFGS)
    def test_control_exact_canonical_spelling_offered(self, tmp_path: Path, cfg: str) -> None:
        """`in_progress` written exactly is offered, and move accepts it."""
        repo = _repo(tmp_path / "repo", cfg, _unmapped(["in_progress", "abandoned"]))
        _seed(repo, "IDEA-001", "backlog")

        assert _allowed(repo, "IDEA-001") == ["in_progress", "blocked"]
        _assert_agrees(repo, "IDEA-001")
