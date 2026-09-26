"""#434: ``WorkItemIndexer`` is deprecated in favour of ``KanbanService.scan``.

Captain ruling (2026-09-26): keep ``WorkItemIndexer`` working, but constructing
it emits a ``DeprecationWarning`` that points at ``KanbanService.scan`` and names
#434. The warning is raised at construction (not import), exactly once, with a
stacklevel that blames the caller. The message starts with ``WorkItemIndexer`` so
``filterwarnings("ignore:WorkItemIndexer:DeprecationWarning")`` in the older
indexer tests (#407/#416/#421/#426/#494) matches it.
"""

from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.indexer import WorkItemIndexer


def _ours(records: list[warnings.WarningMessage]) -> list[warnings.WarningMessage]:
    return [
        r
        for r in records
        if issubclass(r.category, DeprecationWarning) and "WorkItemIndexer" in str(r.message)
    ]


def test_construction_warns_pointing_at_service_scan(tmp_path: Path) -> None:
    with pytest.warns(DeprecationWarning, match=r"KanbanService\.scan") as rec:
        WorkItemIndexer(KanbanConfig(), tmp_path)
    ours = _ours(list(rec))
    assert ours, "no warning mentioning WorkItemIndexer"
    msg = str(ours[0].message)
    assert "#434" in msg, f"warning should name #434: {msg!r}"
    assert msg.startswith("WorkItemIndexer"), f"message should start with the class: {msg!r}"


def test_construction_warns_exactly_once(tmp_path: Path) -> None:
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        WorkItemIndexer(KanbanConfig(), tmp_path)
    ours = _ours(rec)
    assert len(ours) == 1, f"expected one WorkItemIndexer DeprecationWarning, got {len(ours)}"


def test_warning_stacklevel_points_at_caller(tmp_path: Path) -> None:
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        WorkItemIndexer(KanbanConfig(), tmp_path)
    ours = _ours(rec)
    assert ours, "no WorkItemIndexer DeprecationWarning emitted"
    assert Path(ours[0].filename).resolve() == Path(__file__).resolve(), (
        f"warning blamed {ours[0].filename}, not the caller {__file__}"
    )


def test_import_alone_does_not_warn() -> None:
    code = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as rec:\n"
        "    warnings.simplefilter('always')\n"
        "    import yurtle_kanban.indexer\n"
        "bad = [str(r.message) for r in rec if issubclass(r.category, DeprecationWarning)"
        " and ('WorkItemIndexer' in str(r.message) or '#434' in str(r.message))]\n"
        "print(len(bad))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "0", f"importing yurtle_kanban.indexer emitted {out} deprecation warning(s)"


def test_scan_still_works_after_warning(tmp_path: Path) -> None:
    """Control: deprecation keeps behaviour — an empty repo scans to []."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert WorkItemIndexer(KanbanConfig(), tmp_path).scan() == []
