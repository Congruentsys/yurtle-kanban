"""Issue #1202: PyPI's project links named the old hankh95/yurtle-kanban repository.
Every `[project.urls]` value names Congruentsys/yurtle-kanban, where the repo,
releases and trusted publishing live."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # the dev extra's tomli (#555)
    tomllib = pytest.importorskip("tomli")

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
HOME = "https://github.com/Congruentsys/yurtle-kanban"


def test_every_project_url_names_congruentsys() -> None:
    urls = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["urls"]
    assert urls, "no [project.urls]"
    wrong = {k: v for k, v in urls.items() if not v.startswith(HOME)}
    assert not wrong, wrong
