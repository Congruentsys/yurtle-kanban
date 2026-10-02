"""Issue #1242 — upgrade-check: the resolution check reads board items' front
matter, a skill's inline-code commands are medium, the config walk-up stops at $HOME.

[steer] ruling (bucket 2, from the #1231 review; #1243 closed as its duplicate):

1. 2.x had no ``--resolution`` CLI flag, so the scan's ``--resolution
   obsolete|merged`` CLI check goes. What a 2.x board does carry is an item's
   front matter ``resolution: obsolete`` / ``resolution: merged``: values #581
   dropped from the vocabulary. The scan already identifies items (and skips
   them); for this one check it reads their front matter: kind
   ``resolution-value``, confidence ``high``. Item bodies are still never
   scanned for CLI forms.
2. The suggestions match UPGRADING.md exactly: ``obsolete`` -> ``wont_do`` (a
   dead dependency: its dependents stop being pickable) or ``superseded
   --superseded-by ID``; ``merged`` -> ``superseded --superseded-by ID`` or
   ``duplicate --superseded-by ID``. Help and changelog say "values #581 dropped
   from the vocabulary", not "#581 removed them".
3. A skill's (``.claude/skills/**/SKILL.md``, ``skills/**/SKILL.md``)
   inline-code command in prose (``**Atomic claim:** `yurtle-kanban move … -a
   <agent>` ``) is ``medium``: fenced and command lines stay ``high``, other
   docs ``low``.
4. The ``.kanban`` config walk-up stops at $HOME (as at the git root and /):
   ``~/.kanban`` is read only when PATH is $HOME itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main

NAUTICAL_CONFIG = """\
version: '2.0'
boards:
- name: development
  preset: nautical
  path: kanban-work/
"""

OBSOLETE_ITEM = """\
---
id: EXP-002
title: "Dropped"
type: expedition
status: done
resolution: obsolete
---

Notes: `yurtle-kanban move EXP-002 in_progress -a Air` was how we claimed.
Run: yurtle-kanban comment EXP-002 --author Air "done"
"""
OBSOLETE_LINE = 6

MERGED_ITEM = """\
---
id: EXP-003
title: "Folded in"
type: expedition
status: done
resolution: "merged"
---

resolution: obsolete
"""
MERGED_LINE = 6

FINE_ITEM = """\
---
id: EXP-004
title: "Fine"
type: expedition
status: done
resolution: wont_do
---
"""

OBSOLETE_SUGGESTION = ("`wont_do` (a dead dependency: its dependents stop being pickable) "
                       "or `superseded --superseded-by ID`")
MERGED_SUGGESTION = "`superseded --superseded-by ID` or `duplicate --superseded-by ID`"
VOCAB = "a value #581 dropped from the vocabulary"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _run(*args: str):
    return CliRunner().invoke(main, ["upgrade-check", *args])


def _json(path: Path) -> dict:
    res = _run(str(path), "--json")
    assert res.exit_code in (0, 1), res.output
    return json.loads(res.stdout)


def _board(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    _write(root, ".kanban/config.yaml", NAUTICAL_CONFIG)
    _write(root, "kanban-work/expeditions/EXP-002.md", OBSOLETE_ITEM)
    _write(root, "kanban-work/expeditions/EXP-003.md", MERGED_ITEM)
    _write(root, "kanban-work/expeditions/EXP-004.md", FINE_ITEM)
    return root


# --- 1-2. front matter `resolution: obsolete|merged` ------------------------------------


def test_item_front_matter_resolution_values_are_flagged(tmp_path: Path) -> None:
    root = _board(tmp_path / "r")
    findings = _json(root)["findings"]
    res = [f for f in findings if f["kind"] == "resolution-value"]
    by_file = {f["file"]: f for f in res}
    assert set(by_file) == {
        "kanban-work/expeditions/EXP-002.md", "kanban-work/expeditions/EXP-003.md",
    }, res
    obsolete = by_file["kanban-work/expeditions/EXP-002.md"]
    assert obsolete["line"] == OBSOLETE_LINE and obsolete["confidence"] == "high", obsolete
    assert "obsolete" in obsolete["old"], obsolete
    assert VOCAB in obsolete["suggestion"], obsolete
    assert OBSOLETE_SUGGESTION in obsolete["suggestion"], obsolete
    merged = by_file["kanban-work/expeditions/EXP-003.md"]
    assert merged["line"] == MERGED_LINE and merged["confidence"] == "high", merged
    assert "merged" in merged["old"], merged
    assert VOCAB in merged["suggestion"], merged
    assert MERGED_SUGGESTION in merged["suggestion"], merged


def test_item_bodies_are_still_not_scanned(tmp_path: Path) -> None:
    """Only the front matter's resolution is read: no CLI form, no body line."""
    root = _board(tmp_path / "r")
    findings = _json(root)["findings"]
    items = [f for f in findings if f["file"].startswith("kanban-work/")]
    assert all(f["kind"] == "resolution-value" for f in items), items
    assert len(items) == 2, items  # EXP-003's body `resolution: obsolete` is not front matter


def test_a_vendored_item_found_by_front_matter_is_checked(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / ".git").mkdir(parents=True)
    _write(root, "vendor/other/EXP-002.md", OBSOLETE_ITEM)
    findings = _json(root)["findings"]
    assert [(f["file"], f["kind"], f["line"]) for f in findings] == [
        ("vendor/other/EXP-002.md", "resolution-value", OBSOLETE_LINE),
    ], findings


def test_a_doc_that_is_not_an_item_is_not_resolution_checked(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / ".git").mkdir(parents=True)
    _write(root, "docs/notes.md", "---\ntitle: notes\nresolution: obsolete\n---\n")
    assert [f for f in _json(root)["findings"] if f["kind"] == "resolution-value"] == []


def test_the_cli_resolution_flag_is_no_longer_checked(tmp_path: Path) -> None:
    """2.x had no `--resolution` flag: a script passing one is not a 2.x usage."""
    root = tmp_path / "r"
    (root / ".git").mkdir(parents=True)
    _write(root, "r.sh", (
        "#!/usr/bin/env bash\nexport YURTLE_AGENT=Air\n"
        'yurtle-kanban move "$ID" done --resolution obsolete\n'
        'yurtle-kanban update "$ID" --resolution=merged\n'
        'yurtle-kanban list --resolution "obsolete" --json\n'
    ))
    _write(root, "r.py", (
        "import subprocess\n\nYK = 'yurtle-kanban'\n\n\ndef f(iid):\n"
        "    subprocess.run([YK, 'move', iid, 'done', '--resolution', 'merged', '--agent', 'Air'])\n"
    ))
    data = _json(root)
    assert data["findings"] == [], data


def test_help_and_changelog_say_values_dropped_from_the_vocabulary() -> None:
    res = _run("--help")
    assert res.exit_code == 0, res.output
    text = " ".join(res.output.split())
    assert "values #581 dropped from the vocabulary" in text, text
    assert "#581 removed them" not in text, text
    assert "--resolution obsolete" not in text, text
    changelog = (Path(__file__).resolve().parents[2] / "changelog.d" / "1231.md").read_text()
    flat = " ".join(changelog.split())
    assert "values #581 dropped from the vocabulary" in flat, flat
    assert "--resolution obsolete" not in flat, flat
    assert "which #581 removed" not in flat, flat


# --- 3. a skill's inline-code commands are medium -----------------------------------------

SKILL_MD = """\
# Claim

**Atomic claim:** `yurtle-kanban move X in_progress -a <agent>` then push.
Claim it with yurtle-kanban move X in_progress -a Air before you start.

```bash
yurtle-kanban move "$ID" in_progress -a "$AGENT"
```

1. `yurtle-kanban comment "$ID" --author Air "picked up"`
"""


def test_skill_inline_code_commands_are_medium(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / ".git").mkdir(parents=True)
    _write(root, ".claude/skills/claim/SKILL.md", SKILL_MD)
    _write(root, "skills/claim/SKILL.md", SKILL_MD)
    _write(root, "docs/claim.md", SKILL_MD)
    findings = _json(root)["findings"]
    for rel in (".claude/skills/claim/SKILL.md", "skills/claim/SKILL.md"):
        conf = {
            f["line"]: f["confidence"] for f in findings
            if f["file"] == rel and f["kind"] == "removed-form"
        }
        assert conf == {3: "medium", 4: "low", 7: "high", 10: "high"}, (rel, conf)
    doc = [f for f in findings if f["file"] == "docs/claim.md"]
    assert doc and all(f["confidence"] == "low" for f in doc), doc


def test_medium_is_shown_and_counted_in_text(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / ".git").mkdir(parents=True)
    _write(root, "skills/claim/SKILL.md", SKILL_MD)
    res = _run(str(root))
    assert res.exit_code == 1, res.output
    assert "  3: [medium] removed-form:" in res.stdout, res.stdout
    # line 10's `comment --author A ID TEXT` is two removed forms; line 7 adds a low actor note
    assert "6 finding(s) in 1 file(s): 3 high, 1 medium, 2 low confidence" in res.stdout, (
        res.stdout
    )


# --- 4. the config walk-up stops at $HOME -------------------------------------------------

CLAIM = """\
def step(i, me):
    st = i.get('status')
    if st == 'in_progress':
        return 'mine'
    return None
"""
NOTE = "no .kanban config found: status checks skipped"


def test_the_walk_up_never_reads_the_home_kanban(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write(home, ".kanban/config.yaml", NAUTICAL_CONFIG)
    _write(home, "proj/scripts/claim.py", CLAIM)  # no .git: only $HOME stops the walk
    monkeypatch.setenv("HOME", str(home))
    data = _json(home / "proj")
    assert [f for f in data["findings"] if f["kind"] == "status-check"] == [], data
    assert NOTE in data["notes"], data


def test_home_itself_reads_its_own_kanban(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write(home, ".kanban/config.yaml", NAUTICAL_CONFIG)
    _write(home, "proj/scripts/claim.py", CLAIM)
    monkeypatch.setenv("HOME", str(home))
    data = _json(home)
    assert [f for f in data["findings"] if f["kind"] == "status-check"], data
    assert data["notes"] == [], data


def test_a_config_below_home_is_still_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write(home, "proj/.kanban/config.yaml", NAUTICAL_CONFIG)
    _write(home, "proj/scripts/claim.py", CLAIM)
    monkeypatch.setenv("HOME", str(home))
    data = _json(home / "proj" / "scripts")
    assert [f for f in data["findings"] if f["kind"] == "status-check"], data
