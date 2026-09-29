"""Issue #1055 item 3 (Captain's ruling, 2026-09-29, option 2).

Plain ``list --json`` skips the git status-date fallback: ``since`` comes from the
item's history, else ``created:`` (``since_source: created``), else ``unknown``.
``--older-than``, ``--stale`` and an explicitly given ``--stale-after`` run the full
lookup, git included. This amends #579's Acceptance 1.

The fixture is #579's aging board. EXP-4 has no history, a committed status line
and ``created: 2026-05-01``; EXP-3's history disagrees with its committed status
and it has ``created:`` too.
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.issues import test_579_list_aging as base

pytestmark = pytest.mark.usefixtures("claim_env")

board = base.board
clock = base.clock

CREATED = date(2026, 5, 1)  # EXP-3's and EXP-4's `created:`


def _commit_one(root: Path, item_id: str, text: str, message: str) -> None:
    """Write and commit ONE item (EXP-5/EXP-6 stay uncommitted)."""
    base._write(root, item_id, text)
    base._git(root, "add", str(base._path(root, item_id)))
    base._git(root, "commit", "-m", message, author_date=base.DATE_4)


def _git_logs(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> tuple[dict[str, base.Row], list[list[str]]]:
    """`list <args> --json` rows by id, and the `git log` calls it made."""
    calls: list[list[str]] = []
    real = subprocess.Popen

    class Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, cmd: Any, *a: Any, **kw: Any) -> None:
            argv = [cmd] if isinstance(cmd, (str, bytes)) else list(cmd)
            calls.append([str(x) for x in argv])
            super().__init__(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    try:
        data = base.by_id(base.rows(args))
    finally:
        monkeypatch.setattr(subprocess, "Popen", real)
    logs = [c for c in calls if c and Path(c[0]).name.startswith("git") and "log" in c]
    return data, logs


def test_plain_list_json_takes_created_not_git(
    board: Path, clock: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, logs = _git_logs(monkeypatch, [])
    assert logs == [], f"plain list --json ran git log: {logs}"
    for item_id in ("EXP-3", "EXP-4"):
        row = data[item_id]
        assert row["since_source"] == "created", row
        expected = base.local_midnight(CREATED)
        assert base.since_of(row) == expected
        assert row["age_seconds"] == base.age(expected)
    # history, uncommitted-created and unknown are as before
    assert data["EXP-1"]["since_source"] == "history"
    assert data["EXP-5"]["since_source"] == "created"
    assert data["EXP-6"]["since_source"] == "unknown"
    assert set(data) == base.ALL_IDS


def test_plain_list_json_without_created_is_unknown(
    board: Path, clock: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed item with neither history nor `created:` is `unknown`, not git."""
    _commit_one(board, "EXP-4", base.item_md("EXP-4", "ready"), "EXP-4 drops created")
    data, logs = _git_logs(monkeypatch, [])
    assert logs == [], f"plain list --json ran git log: {logs}"
    row = data["EXP-4"]
    assert row["since_source"] == "unknown", row
    assert row["since"] is None and row["age_seconds"] is None, row


@pytest.mark.parametrize(
    "args", [["--stale"], ["--older-than", "1m"], ["--stale-after", "1h"]],
    ids=["stale", "older-than", "stale-after"],
)
def test_aging_flags_keep_the_git_source(
    board: Path, clock: datetime, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    # EXP-7: in progress, no history, committed (status-line commit at DATE_4),
    # so `--stale` lists it too
    _commit_one(
        board, "EXP-7",
        base.item_md("EXP-7", "in_progress", assignee=base.A, created=CREATED), "add EXP-7",
    )
    data, logs = _git_logs(monkeypatch, args)
    assert len(logs) == 1, f"expected one git log, got {len(logs)}: {logs}"
    row = data["EXP-7"]
    assert row["since_source"] == "git", row
    expected = datetime.fromisoformat(base.DATE_4)
    assert base.since_of(row) == expected
    assert row["age_seconds"] == base.age(expected)
    if "--stale" not in args:  # EXP-4 is not in progress
        assert data["EXP-4"]["since_source"] == "git", data["EXP-4"]
    if "--stale-after" in args:  # doesn't filter: every row, EXP-3 via git too
        assert set(data) == {*base.ALL_IDS, "EXP-7"}
        assert data["EXP-3"]["since_source"] == "git", data["EXP-3"]
