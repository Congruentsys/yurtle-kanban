"""Issue #900: config loader polish, found in the PR #898 (#864) review.

Decided spec ([steer] on #900, bucket 1):

1. A non-string ``version`` (``[1]``, ``2``, ``{a: 1}``) is refused by field: an
   ``InputRefused`` that names ``version``. Today each loads silently as v1. A string
   ``"1.0"`` / ``"2.0"``, or no ``version`` at all, loads as before (controls).
2. The ``from_text`` backstop (#864) logs the original traceback at DEBUG: a record
   at DEBUG level with ``exc_info`` set, carrying the TypeError that was turned into
   the refusal.
3. Every per-board refusal uses one word order:
   ``\\`<field>\\` on board '<name>' must be …, got …``. When the board's own ``name``
   is the bad value there is no name to quote, so the board is named by its index
   (``on board 1``; 0- or 1-based is the driver's call). Unnamed boards otherwise
   keep today's ``'default'``.
4. The dead ``boards is None`` branch in ``_load_v2`` needs no test.

Today's wording of each per-board message (red unless noted):

- name:             a board's `name` must be a string, got int 5
- path:             `path` must be a path string on board 'a', got list [1]
- wip_limits:       `wip_limits` on board 'a' must be a mapping of column to limit
                    (or null), got int 5                          (already green)
- gates:            `gates` must be a mapping on board 'a', got int 5
- wip_exempt_types: `wip_exempt_types` on board 'a' must be a list of item types,
                    got str 'expedite'                            (already green)
- preset:           `preset` for board 'a' must be a string theme name, got list [...]
- scan_paths:       scan_paths: expected a list of paths, got int 5
- scan_paths entry: scan_paths: every entry must be a path string, got 1
- ignore:           ignore: expected a list of glob patterns, got int 5
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import InputRefused

V2 = 'version: "2.0"\n'
LOGGER = "yurtle-kanban"


@pytest.fixture(autouse=True)
def _fresh_theme_cache() -> None:
    config_mod._theme_cache.clear()


def _load(text: str, tmp_path: Path) -> KanbanConfig:
    return KanbanConfig.from_text(text, tmp_path)


def _board(extra: str, name: str = "a") -> str:
    return V2 + f"boards:\n  - name: {name}\n    path: work/\n    {extra}\n"


# --- 1. a non-string version is refused by field -----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "version: [1]\n",
        "version: 2\n",
        "version: {a: 1}\n",
        # with boards too: must not quietly drop them by loading as v1
        "version: [1]\nboards: [{name: a, path: work/}]\n",
        "version: 2\nboards: [{name: a, path: work/}]\n",
    ],
    ids=["list", "int", "mapping", "list-with-boards", "int-with-boards"],
)
def test_non_string_version_is_refused_naming_version(text: str, tmp_path: Path) -> None:
    with pytest.raises(InputRefused) as exc:
        _load(text, tmp_path)
    assert "version" in str(exc.value), str(exc.value)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('version: "1.0"\n', "1.0"),
        ('version: "2.0"\nboards: [{name: a, path: work/}]\n', "2.0"),
        ("kanban:\n  theme: software\n", "1.0"),
        ("", "1.0"),
    ],
    ids=["string-1.0", "string-2.0", "absent", "empty-file"],
)
def test_string_or_absent_version_still_loads(text: str, expected: str, tmp_path: Path) -> None:
    assert _load(text, tmp_path).version == expected


# --- 2. the backstop logs the original traceback at DEBUG ----------------------------


def _boom(*_args: Any, **_kwargs: Any) -> KanbanConfig:
    raise TypeError("boom-900: unexpected shape")


@pytest.mark.parametrize("where", ["_from_data", "_load_v1"])
def test_backstop_logs_original_traceback_at_debug(
    where: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(KanbanConfig, where, classmethod(_boom))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        with pytest.raises(InputRefused):
            _load("kanban:\n  theme: software\n", tmp_path)
    carrying = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG
        and r.exc_info is not None
        and isinstance(r.exc_info[1], TypeError)
        and "boom-900" in str(r.exc_info[1])
    ]
    assert carrying, [(r.levelname, r.getMessage(), r.exc_info) for r in caplog.records]


def test_backstop_still_refuses_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control (#864): the refusal itself is unchanged."""
    monkeypatch.setattr(KanbanConfig, "_load_v1", classmethod(_boom))
    with pytest.raises(InputRefused) as exc:
        _load("kanban:\n  theme: software\n", tmp_path)
    assert "boom-900" in str(exc.value)
    assert "\n" not in str(exc.value)


# --- 3. one word order for every per-board message ------------------------------------


def _shape(field: str, board: str) -> re.Pattern[str]:
    return re.compile(rf"^`{re.escape(field)}` on board {board} must be .+, got .+$", re.S)


NAMED = "'a'"
INDEX = r"\d+"  # `name` itself is the bad value: the board is named by its index

# (id, field, config text, how the board is named in the message)
BOARD_CASES: list[tuple[str, str, str, str]] = [
    ("name", "name", V2 + "boards: [{name: 5, path: x}]\n", INDEX),
    ("path", "path", V2 + "boards: [{name: a, path: [1]}]\n", NAMED),
    ("wip_limits", "wip_limits", _board("wip_limits: 5"), NAMED),
    ("gates", "gates", _board("gates: 5"), NAMED),
    ("wip_exempt_types", "wip_exempt_types", _board("wip_exempt_types: expedite"), NAMED),
    ("preset", "preset", _board("preset: [software]"), NAMED),
    ("scan_paths", "scan_paths", _board("scan_paths: 5"), NAMED),
    ("scan_paths-entry", "scan_paths", _board("scan_paths: [1]"), NAMED),
    ("ignore", "ignore", _board("ignore: 5"), NAMED),
    # an unnamed board keeps today's name for it, 'default'
    ("unnamed-gates", "gates", V2 + "boards: [{path: x, gates: 5}]\n", "'default'"),
]


@pytest.mark.parametrize(
    ("field", "text", "board"),
    [c[1:] for c in BOARD_CASES],
    ids=[c[0] for c in BOARD_CASES],
)
def test_board_refusal_uses_one_word_order(
    field: str, text: str, board: str, tmp_path: Path
) -> None:
    with pytest.raises(InputRefused) as exc:
        _load(text, tmp_path)
    message = str(exc.value)
    assert _shape(field, board).match(message), message


def test_v1_scan_paths_and_ignore_still_refused_by_field(tmp_path: Path) -> None:
    """Control: the shared helpers still refuse by field outside a board (#864)."""
    for text, key in [
        ("kanban:\n  paths: {scan_paths: 5}\n", "scan_paths"),
        ("kanban:\n  paths: {ignore: 5}\n", "ignore"),
    ]:
        with pytest.raises(InputRefused) as exc:
            _load(text, tmp_path)
        assert key in str(exc.value), str(exc.value)
