"""#700: `_get_type_prefix` has one path per mode, and an empty type definition
(`expedition: {}` or `expedition:`) counts as not defined in both modes."""

from __future__ import annotations

import pytest

from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService


class _Config:
    is_multi_board = False

    def __init__(self, theme):
        self._theme = theme

    def get_theme(self):
        return self._theme


def _svc(theme) -> KanbanService:
    svc = KanbanService.__new__(KanbanService)
    svc.config = _Config(theme)
    return svc


@pytest.mark.parametrize("empty", [{}, None], ids=["empty-mapping", "null"])
def test_single_board_empty_def_uses_builtin(empty):
    svc = _svc({"item_types": {"expedition": empty}})
    assert svc._get_type_prefix(WorkItemType.EXPEDITION) == "EXP"


def test_single_board_defined_prefix_wins():
    svc = _svc({"item_types": {"expedition": {"id_prefix": "VOYX"}}})
    assert svc._get_type_prefix(WorkItemType.EXPEDITION) == "VOYX"


def test_single_board_def_without_prefix_uses_type_head():
    svc = _svc({"item_types": {"expedition": {"name": "Expedition"}}})
    assert svc._get_type_prefix(WorkItemType.EXPEDITION) == "EXPE"
