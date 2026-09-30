"""Issue #1167: the checkout's allocations file is read directly; a file that is
gone by the time it is read (deleted after any existence check) is missing — a
fresh list — not "could not be read"."""
from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban.models import InputRefused
from yurtle_kanban.service import _local_allocations_text


def test_deleted_between_check_and_read_is_missing(tmp_path, monkeypatch) -> None:
    lock = tmp_path / "_ID_ALLOCATIONS.json"
    monkeypatch.setattr(Path, "exists", lambda self: True)  # it existed a moment ago
    assert _local_allocations_text(lock) is None


def test_control_missing_file_is_none(tmp_path) -> None:
    assert _local_allocations_text(tmp_path / "_ID_ALLOCATIONS.json") is None


def test_control_unreadable_path_is_refused(tmp_path) -> None:
    lock = tmp_path / "_ID_ALLOCATIONS.json"
    lock.mkdir()  # a directory: read_bytes raises IsADirectoryError, an OSError
    with pytest.raises(InputRefused, match="could not be read"):
        _local_allocations_text(lock)
