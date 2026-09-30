"""Issue #1183: a file named `.kanban` is refused in ONE wording, whether the
allocations reader (#1174) or `init`/`board-add`/`control` (#1179) meets it."""
from __future__ import annotations

import pytest

from yurtle_kanban.models import InputRefused
from yurtle_kanban.service import _local_allocations_text, kanban_dir_refusal


def test_allocations_reader_and_writers_say_the_same(tmp_path) -> None:
    (tmp_path / ".kanban").write_text("not a directory\n")
    with pytest.raises(InputRefused) as info:
        _local_allocations_text(tmp_path / ".kanban" / "_ID_ALLOCATIONS.json")
    assert str(info.value) == kanban_dir_refusal(tmp_path)
