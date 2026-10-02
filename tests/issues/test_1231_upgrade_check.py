"""Issue #1231 — ``yurtle-kanban upgrade-check [PATH] [--json]``: scan a repo for
2.x usages that 3.x changed.

Captain's ruling (2026-10-02): FOSS users upgrading from 2.x hit the same breaks
the fleet's labs would. The scanner is read-only and heuristic, and reports per
finding: file, line, kind, the old form, a suggestion and a confidence.

1. ``removed-form``: a form #580 removed (``move -a``, ``create --assignee/-a/
   --description/-d``, ``comment --author/-a`` and positional text, ``next
   --assignee/-a``, ``list -a``), in shell strings and in Python arg lists whose
   first element is a yurtle-kanban executable (``[YK, 'move', iid,
   'in_progress', '-a', agent]``, rachael-lab ``lab_next.py``; ``[exe,
   "comment", "--author", author, item_id, text]``, rachael-neural-lab
   ``nlab/nightly.py``).
2. ``status-check``: a raw comparison on a canonical status name the board's
   theme renames (``status == 'in_progress'`` on a nautical board), only when
   ``.kanban/config.yaml`` uses such a theme.
3. ``actor``: a ``comment``/``move`` call with no ``--agent`` and no
   ``YURTLE_AGENT`` (low confidence).

Docs (``*.md``) and backtick-quoted mentions are reported at ``low``. Exit 1 with
findings, 0 without. ``--json`` prints ONE object ``{"findings": [...],
"heuristic": true}``. ``.git``, venvs, ``node_modules`` and the board's own item
files are skipped.

The fixtures below are minimal synthetic copies of the labs' real shapes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main

FINDING_KEYS = {"file", "line", "kind", "old", "suggestion", "confidence"}

NAUTICAL_CONFIG = """\
version: '2.0'
boards:
- name: development
  preset: nautical
  path: kanban-work/
- name: research
  preset: hdd
  path: research/
"""

SOFTWARE_CONFIG = """\
version: '2.0'
boards:
- name: development
  preset: software
  path: kanban-work/
"""

# rachael-lab scripts/lab_next.py claim(): the 2.x `-a` on move, and a backlog check
LAB_NEXT = """\
import subprocess

YK = '.venv/bin/yurtle-kanban'


def claim(item, agent):
    iid = item['id']
    if item.get('status') == 'backlog':
        r = subprocess.run([YK, 'move', iid, 'ready', '-m', f'ready {iid}'], capture_output=True)
    r = subprocess.run([YK, 'move', iid, 'in_progress', '-a', agent, '-m', f'claim {iid} ({agent})'],
                       capture_output=True, text=True)
    return r
"""
LAB_NEXT_MOVE_A_LINE = 10
LAB_NEXT_BACKLOG_LINE = 8

# rachael-neural-lab nlab/nightly.py _board_comment(): --author and positional text
NIGHTLY = """\
import subprocess


def _board_comment(board, exe, author, item_id, text):
    p = subprocess.run([exe, "comment", "--author", author, item_id, text], cwd=str(board),
                       capture_output=True, text=True)
    return p
"""
NIGHTLY_LINE = 5

# rachael-lab scripts/claim.py / scripts/nlab_next.py: picker status checks
CLAIM = """\
def step(i, me):
    st, who = i.get('status'), str(i.get('assignee') or '')
    if st == 'in_progress' and who == me:
        return 'mine'
    return None
"""
CLAIM_LINE = 3

NLAB_NEXT = """\
def offer(items):
    out = []
    for i in items:
        if i.get('type') not in ('expedition', 'chore') or i.get('status') not in ('backlog', 'ready'):
            continue
        out.append(i)
    return out
"""
NLAB_NEXT_LINE = 4

# a shell script with 2.x forms, via the name and via $YK
CLAIM_SH = """\
#!/usr/bin/env bash
YK=.venv/bin/yurtle-kanban
yurtle-kanban move "$ID" in_progress -a "$AGENT"
$YK comment "$ID" "picked up"
yurtle-kanban create expedition "Title" --assignee Air -d "the body"
yurtle-kanban next --assignee Air
yurtle-kanban list -a Air
"""

# the 3.x forms: nothing here may be flagged
NEW_FORMS_PY = """\
import os
import subprocess

YK = 'yurtle-kanban'
AGENT = os.environ.get('YURTLE_AGENT', 'Air')


def go(iid, agent, text):
    subprocess.run([YK, 'move', iid, 'in_progress', '--assign', agent, '--agent', agent, '-m', 'claim'])
    subprocess.run([YK, 'comment', iid, '--body', text, '--agent', agent])
    subprocess.run([YK, 'create', 'expedition', 'Title', '--assign', agent, '--body', text, '-p', 'high'])
    subprocess.run([YK, 'next', '--agent', agent, '--json'])
    subprocess.run([YK, 'list', '--assignee', agent, '-s', 'ready', '--json'])
"""

NEW_FORMS_SH = """\
#!/usr/bin/env bash
export YURTLE_AGENT=Air
yurtle-kanban move "$ID" in_progress --assign Air --agent Air
yurtle-kanban comment "$ID" --body "picked up" --agent Air
yurtle-kanban comment "$ID" --body-file - --agent Air
yurtle-kanban create expedition "Title" --assign Air --body "text"
yurtle-kanban next --agent Air
yurtle-kanban list --assignee Air -s ready
"""

# a comment with no actor: the low-confidence actor note
NO_ACTOR_PY = """\
import subprocess

YK = 'yurtle-kanban'


def note(iid, text):
    subprocess.run([YK, 'comment', iid, '--body', text])
"""
NO_ACTOR_LINE = 7

DOC_MD = """\
# How to claim

Claim with `yurtle-kanban move <ID> in_progress -a <agent>` and push.
"""
DOC_LINE = 3

ITEM_MD = """\
---
id: EXP-001
title: "An item"
type: expedition
status: in_progress
---

Notes: `yurtle-kanban move EXP-001 in_progress -a Air` was how we claimed.
"""


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _lab(root: Path, config: str = NAUTICAL_CONFIG) -> Path:
    _write(root, ".kanban/config.yaml", config)
    _write(root, "kanban-work/expeditions/EXP-001.md", ITEM_MD)
    _write(root, "scripts/lab_next.py", LAB_NEXT)
    _write(root, "nlab/nightly.py", NIGHTLY)
    _write(root, "scripts/claim.py", CLAIM)
    _write(root, "scripts/nlab_next.py", NLAB_NEXT)
    _write(root, "scripts/claim.sh", CLAIM_SH)
    _write(root, "scripts/new_forms.py", NEW_FORMS_PY)
    _write(root, "scripts/new_forms.sh", NEW_FORMS_SH)
    _write(root, "scripts/no_actor.py", NO_ACTOR_PY)
    _write(root, "docs/howto.md", DOC_MD)
    return root


def _run(*args: str):
    return CliRunner().invoke(main, ["upgrade-check", *args])


def _json(path: Path) -> dict:
    res = _run(str(path), "--json")
    assert res.exit_code in (0, 1), res.output
    return json.loads(res.stdout)


def _in(findings: list[dict], file: str, **match) -> list[dict]:
    return [
        f for f in findings
        if f["file"] == file and all(f[k] == v for k, v in match.items())
    ]


@pytest.fixture
def lab(tmp_path: Path) -> Path:
    return _lab(tmp_path / "lab")


# --- 1. removed #580 forms ------------------------------------------------------


def test_lab_next_claim_move_dash_a_is_found(lab: Path) -> None:
    findings = _json(lab)["findings"]
    hits = _in(findings, "scripts/lab_next.py", kind="removed-form", line=LAB_NEXT_MOVE_A_LINE)
    assert len(hits) == 1, findings
    assert hits[0]["confidence"] == "high"
    assert "-a" in hits[0]["old"]
    assert "--assign" in hits[0]["suggestion"]


def test_nightly_comment_author_and_positional_text_are_found(lab: Path) -> None:
    findings = _json(lab)["findings"]
    hits = _in(findings, "nlab/nightly.py", kind="removed-form", line=NIGHTLY_LINE)
    suggestions = " | ".join(h["suggestion"] for h in hits)
    assert len(hits) == 2, findings
    assert all(h["confidence"] == "high" for h in hits)
    assert "--agent" in suggestions  # --author -> --agent
    assert "--body" in suggestions  # comment ID TEXT -> comment ID --body TEXT


def test_shell_forms_are_found_by_name_and_by_alias_var(lab: Path) -> None:
    findings = _in(_json(lab)["findings"], "scripts/claim.sh", kind="removed-form")
    by_line: dict[int, list[str]] = {}
    for f in findings:
        by_line.setdefault(f["line"], []).append(f["suggestion"])
    assert any("--assign" in s for s in by_line.get(3, [])), findings  # move -a
    assert any("--body" in s for s in by_line.get(4, [])), findings  # $YK comment ID TEXT
    line5 = " | ".join(by_line.get(5, []))
    assert "--assign" in line5 and "--body" in line5, findings  # create --assignee / -d
    assert any("--agent" in s for s in by_line.get(6, [])), findings  # next --assignee
    assert any("--assignee" in s for s in by_line.get(7, [])), findings  # list -a


def test_new_3x_forms_are_not_flagged(lab: Path) -> None:
    findings = _json(lab)["findings"]
    assert _in(findings, "scripts/new_forms.py") == []
    assert _in(findings, "scripts/new_forms.sh") == []


def test_docs_and_backtick_mentions_are_low_confidence(lab: Path) -> None:
    findings = _json(lab)["findings"]
    hits = _in(findings, "docs/howto.md", line=DOC_LINE)
    assert hits, findings
    assert all(h["confidence"] == "low" for h in hits)
    assert any(h["kind"] == "removed-form" for h in hits)


# --- 2. raw status comparisons, only on a board whose theme renames them ----------


def test_status_checks_flagged_on_a_nautical_board(lab: Path) -> None:
    findings = _json(lab)["findings"]
    claim = _in(findings, "scripts/claim.py", kind="status-check", line=CLAIM_LINE)
    assert len(claim) == 1, findings
    assert "in_progress" in claim[0]["old"]
    assert "underway" in claim[0]["suggestion"]
    assert _in(findings, "scripts/nlab_next.py", kind="status-check", line=NLAB_NEXT_LINE)
    assert _in(findings, "scripts/lab_next.py", kind="status-check", line=LAB_NEXT_BACKLOG_LINE)


def test_status_checks_not_flagged_on_a_software_board(tmp_path: Path) -> None:
    root = _lab(tmp_path / "sw", SOFTWARE_CONFIG)
    findings = _json(root)["findings"]
    assert [f for f in findings if f["kind"] == "status-check"] == []
    # the removed forms are still found: they do not depend on the theme
    assert _in(findings, "scripts/lab_next.py", kind="removed-form")


def test_status_checks_not_flagged_without_a_config(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    _write(root, "scripts/claim.py", CLAIM)
    res = _run(str(root), "--json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["findings"] == []


def test_a_message_naming_the_command_is_not_a_call(tmp_path: Path) -> None:
    """nightly.py's error text: `yurtle-kanban comment failed (rc=…)` is prose."""
    root = tmp_path / "r"
    _write(root, "m.py", 'def f(p):\n'
           '    raise RuntimeError(f"yurtle-kanban comment failed (rc={p.returncode})")\n'
           '    raise RuntimeError("yurtle-kanban move did not commit")\n')
    assert _json(root)["findings"] == []


def test_a_status_string_in_python_is_low(tmp_path: Path) -> None:
    """`"status: done\\n"` in Python is often a fixture writing front matter: low;
    in a shell script (`grep 'status: in_progress'`) it is a check: high."""
    root = tmp_path / "r"
    _write(root, ".kanban/config.yaml", NAUTICAL_CONFIG)
    _write(root, "fixture.py", 'ITEM = "---\\nid: EXP-1\\nstatus: done\\n---\\n"\n')
    _write(root, "check.sh", "grep -l 'status: in_progress' kanban-work/*/*.md\n")
    findings = _json(root)["findings"]
    assert [(f["file"], f["confidence"]) for f in findings] == [
        ("check.sh", "high"), ("fixture.py", "low"),
    ], findings


# --- 3. actor note ---------------------------------------------------------------


def test_comment_without_agent_gets_a_low_actor_note(lab: Path) -> None:
    findings = _json(lab)["findings"]
    hits = _in(findings, "scripts/no_actor.py", kind="actor", line=NO_ACTOR_LINE)
    assert len(hits) == 1, findings
    assert hits[0]["confidence"] == "low"
    assert "--agent" in hits[0]["suggestion"] and "YURTLE_AGENT" in hits[0]["suggestion"]


def test_yurtle_agent_in_the_file_silences_the_actor_note(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _write(root, "a.py", NO_ACTOR_PY.replace("import subprocess", "import os, subprocess\n"
                                             "os.environ['YURTLE_AGENT'] = 'Air'"))
    assert _json(root)["findings"] == []


# --- output, exit codes, skipped paths --------------------------------------------


def test_json_shape(lab: Path) -> None:
    res = _run(str(lab), "--json")
    assert res.exit_code == 1, res.output
    data = json.loads(res.stdout)
    assert set(data) == {"findings", "heuristic"}
    assert data["heuristic"] is True
    assert data["findings"]
    for f in data["findings"]:
        assert set(f) == FINDING_KEYS, f
        assert isinstance(f["line"], int) and f["line"] >= 1
        assert f["confidence"] in ("high", "low")
        assert f["kind"] in ("removed-form", "status-check", "actor")


def test_text_output_is_grouped_per_file_and_says_heuristic(lab: Path) -> None:
    res = _run(str(lab))
    assert res.exit_code == 1, res.output
    assert "heuristic" in res.output.splitlines()[0].lower()
    headers = [ln for ln in res.output.splitlines() if ln.strip() == "nlab/nightly.py"]
    assert len(headers) == 1, res.output


def test_clean_repo_exits_zero(tmp_path: Path) -> None:
    root = tmp_path / "clean"
    _write(root, ".kanban/config.yaml", NAUTICAL_CONFIG)
    _write(root, "scripts/new_forms.py", NEW_FORMS_PY)
    _write(root, "scripts/new_forms.sh", NEW_FORMS_SH)
    res = _run(str(root))
    assert res.exit_code == 0, res.output
    assert "heuristic" in res.output.lower()
    res = _run(str(root), "--json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == {"findings": [], "heuristic": True}


def test_skipped_dirs_and_board_items(lab: Path) -> None:
    for rel in (
        ".git/hooks/claim.sh",
        ".venv/bin/claim.sh",
        "venv/lib/claim.sh",
        "env/lib/python3.12/site-packages/pkg/claim.py",
        "node_modules/pkg/claim.sh",
    ):
        _write(lab, rel, CLAIM_SH if rel.endswith(".sh") else LAB_NEXT)
    files = {f["file"] for f in _json(lab)["findings"]}
    assert not any(
        part in Path(f).parts
        for f in files
        for part in (".git", ".venv", "venv", "site-packages", "node_modules")
    ), files
    # the board's own item files are not scanned (their `status:` is the board's)
    assert not any(f.startswith("kanban-work/") for f in files), files


def test_a_vendored_boards_item_files_are_skipped(lab: Path) -> None:
    """Another repo's board vendored here: its items are data, known by their
    front matter, though no board of this config names the directory."""
    _write(lab, "vendor/other-lab/kanban-work/EXP-009.md", ITEM_MD)
    files = {f["file"] for f in _json(lab)["findings"]}
    assert not any(f.startswith("vendor/") for f in files), files


def test_scan_is_read_only(lab: Path) -> None:
    before = {p: p.read_bytes() for p in lab.rglob("*") if p.is_file()}
    _run(str(lab))
    after = {p: p.read_bytes() for p in lab.rglob("*") if p.is_file()}
    assert before == after
