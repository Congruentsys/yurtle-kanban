"""Issue #1101: `roadmap --export md` printed item title and assignee raw, so an
ESC could reach the terminal and a newline in a title forged a list line.

Expected ([steer] bucket-1): title and assignee through `escape_nonprintable`
(plain text: markdown brackets stay verbatim); `--json` stays raw."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

CONFIG = """\
kanban:
  theme: software
  paths:
    root: work/
    scan_paths:
    - "work/"
"""

TITLE = "T\x1b[2J\nFORGED [x](y)"
ASSIGNEE = "bob\x1b[31m"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_mod._theme_cache.clear()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(CONFIG)
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "FEAT-001.md").write_text(
        "---\nid: FEAT-001\ntype: feature\nstatus: ready\npriority: high\n"
        'created: 2026-09-29\ntitle: "T\\e[2J\\nFORGED [x](y)"\nassignee: "bob\\e[31m"\n'
        "---\n\n# FEAT-001\n"
    )
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    config_mod._theme_cache.clear()


def test_md_export_escapes_title_and_assignee(repo: Path) -> None:
    result = CliRunner().invoke(main, ["roadmap", "--export", "md"], color=True)
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "\x1b" not in out, repr(out)
    assert not any(ln.startswith("FORGED") for ln in out.splitlines()), repr(out)
    entry = next(ln for ln in out.splitlines() if "FEAT-001" in ln)
    assert "T\\x1b[2J\\nFORGED [x](y)" in entry, repr(out)
    assert "@bob\\x1b[31m" in entry, repr(out)


def test_json_stays_raw(repo: Path) -> None:
    result = CliRunner().invoke(main, ["roadmap", "--json"])
    assert result.exit_code == 0, result.output
    item = next(i for i in json.loads(result.stdout) if i["id"] == "FEAT-001")
    assert item["title"] == TITLE and item["assignee"] == ASSIGNEE
