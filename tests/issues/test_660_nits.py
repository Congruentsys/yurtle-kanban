"""#660: small follow-ups from several reviews (see the [steer] comment on #660)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from yurtle_kanban.inputs import resolve_actor

ROOT = Path(__file__).resolve().parents[2]


def _assembler():
    spec = importlib.util.spec_from_file_location(
        "assemble_changelog", ROOT / "scripts" / "assemble_changelog.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_blank_explicit_actor_names_the_given_flag():
    with pytest.raises(ValueError, match="--agent/--run-by"):
        resolve_actor("  ", flag="--agent/--run-by")


def test_blank_explicit_actor_default_flag_is_agent():
    with pytest.raises(ValueError, match="--agent"):
        resolve_actor("  ")


@pytest.mark.parametrize("indent", ["  ", "\t", "    "])
def test_indented_section_line_outside_a_fence_is_refused(tmp_path, indent):
    mod = _assembler()
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "903.md").write_text(
        f"<!-- section: Fixed -->\n- one (#903):\n{indent}<!-- section: Changed -->\n"
    )
    with pytest.raises(mod.FragmentError, match="903.md"):
        mod.read_fragments(d)


def test_indented_section_line_inside_a_fence_is_text(tmp_path):
    mod = _assembler()
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "904.md").write_text(
        "<!-- section: Fixed -->\n- quoting (#904):\n\n  ```\n  <!-- section: Changed -->\n  ```\n"
    )
    [(num, section, _t, _p)] = mod.read_fragments(d)
    assert (num, section) == (904, "Fixed")


def test_title_ending_in_a_period_gets_no_second_one():
    from yurtle_kanban.service import _created_and_pushed_message

    got = _created_and_pushed_message("EXP-001", "main", "Fix the thing.", local=False)
    assert ".." not in got
    got_local = _created_and_pushed_message("EXP-001", "main", "Fix the thing.", local=True)
    assert got_local == "Created and pushed EXP-001 to origin/main: Fix the thing."
