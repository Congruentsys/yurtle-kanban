"""Issue #369 — follow-ups from the #366 (#357) review of `hooks._path_safe`.

1. `_path_safe` left `:` in place. On Windows, `{title}.log` with title `C:foo`
   renders `C:foo.log`, which is drive-relative, and `repo_root / "C:foo.log"` drops
   repo_root and escapes the repo. Decided: a `:` in a substituted value becomes `_`
   (it is illegal in Windows filenames anyway). Tested on POSIX by the rendered name.
2. An empty value (`{assignee}` with no assignee) rendered `""`: a template that is
   only the placeholder became `repo_root/.` (the open failed with a caught warning),
   and `logs/{assignee}/x` collapsed to `logs/x`. Decided: an empty value renders
   as `_` in paths.
3. Controls: plain values are unchanged; `/`, `\\` and `..` behave as in #357; the
   author's own `:` or empty segments in the template are untouched; and
   `render_template` (shell, nats, notify) is unchanged for `:` and empty values.

Helpers are reused from the #357 and #347 tests.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.issues.test_347_hooks_repo_cwd import _assert_cwd_untouched
from tests.issues.test_357_hooks_context_copy_safe_paths import (
    _ctx,
    _files_under,
    _log_entries,
    _log_service,
)
from yurtle_kanban.hooks import HookEvent, _path_safe

# --- 1. `:` in a substituted value becomes `_` ------------------------------------


def test_path_safe_maps_colon() -> None:
    assert _path_safe("C:foo") == "C_foo"


def test_render_path_title_with_drive_colon() -> None:
    assert _ctx(title="C:foo").render_path("{title}.log") == "C_foo.log"


def test_log_action_colon_title_lands_in_repo_without_colon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, "{title}.log")
    monkeypatch.chdir(elsewhere)
    before = _files_under(tmp_path)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx(title="C:foo"))
    new = _files_under(tmp_path) - before
    assert len(new) == 1, new
    (written,) = new
    assert written.parent == repo.resolve(), f"log did not land directly in repo: {written}"
    assert ":" not in written.name, f"`:` from the title kept in the file name: {written}"
    assert written.name == "C_foo.log"
    _assert_cwd_untouched(elsewhere)


# --- 2. an empty value renders as `_` in paths ------------------------------------


def test_path_safe_maps_empty() -> None:
    assert _path_safe("") == "_"


def test_render_path_missing_assignee_is_underscore_segment() -> None:
    assert _ctx().render_path("logs/{assignee}/x.jsonl") == "logs/_/x.jsonl"


def test_log_action_missing_assignee_keeps_directory_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, "logs/{assignee}/x.jsonl")
    monkeypatch.chdir(elsewhere)
    with caplog.at_level(logging.WARNING, logger="yurtle-kanban.hooks"):
        service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx())
    target = repo / "logs" / "_" / "x.jsonl"
    assert target.is_file(), f"expected R/logs/_/x.jsonl, got {list(repo.rglob('*'))}"
    assert not (repo / "logs" / "x.jsonl").exists(), "empty value collapsed the segment"
    (entry,) = _log_entries(target)
    assert entry["item_id"] == "E-1"
    assert "log action failed" not in caplog.text
    _assert_cwd_untouched(elsewhere)


def test_log_action_template_only_placeholder_writes_underscore_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, "{assignee}")
    monkeypatch.chdir(elsewhere)
    with caplog.at_level(logging.WARNING, logger="yurtle-kanban.hooks"):
        service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx())
    assert "log action failed" not in caplog.text, caplog.text
    target = repo / "_"
    assert target.is_file(), f"expected the file R/_, got {list(repo.iterdir())}"
    (entry,) = _log_entries(target)
    assert entry["item_id"] == "E-1"
    _assert_cwd_untouched(elsewhere)


# --- 3. controls ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("EXP-1", "EXP-1"),
        ("Some Title", "Some Title"),
        ("a/b", "a_b"),
        ("a\\b", "a_b"),
        ("..", "_"),
        (".", "_"),
        ("../../escape", ".._.._escape"),
    ],
    ids=["plain-id", "plain-title", "slash", "backslash", "dotdot", "dot", "traversal"],
)
def test_path_safe_controls(value: str, expected: str) -> None:
    assert _path_safe(value) == expected


def test_render_path_keeps_authors_colon_and_empty_segments() -> None:
    ctx = _ctx(item_id="EXP-1")
    assert ctx.render_path("logs/a:b//{item_id}.jsonl") == "logs/a:b//EXP-1.jsonl"


def test_render_template_unchanged_for_colon_and_empty() -> None:
    ctx = _ctx(title="C:foo")
    assert ctx.render_template("{title}") == "C:foo"
    assert ctx.render_template("[{assignee}]") == "[]"
    assert ctx.render_template("{old_status}|{new_status}") == "|"
