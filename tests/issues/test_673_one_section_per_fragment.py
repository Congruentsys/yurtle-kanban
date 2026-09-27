"""#673: a changelog fragment holds exactly one `<!-- section: X -->` line.

`read_fragments` takes a fragment's section from its first line only, so a second
section line would be filed under the first section as text. A fragment with a
second section line (outside a code fence) is refused, and every fragment in the
real `changelog.d/` assembles.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _assembler():
    spec = importlib.util.spec_from_file_location(
        "assemble_changelog", ROOT / "scripts" / "assemble_changelog.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, name: str, text: str) -> Path:
    d = tmp_path / "changelog.d"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text)
    return d


@pytest.mark.parametrize("second", ["Changed", "Removed", "Fixed"])
def test_second_section_line_is_refused(tmp_path, second):
    mod = _assembler()
    d = _write(
        tmp_path,
        "900.md",
        f"<!-- section: Fixed -->\n- one (#900).\n\n<!-- section: {second} -->\n- two (#900).\n",
    )
    with pytest.raises(mod.FragmentError, match="900.md"):
        mod.read_fragments(d)


def test_section_line_inside_a_fence_is_text(tmp_path):
    mod = _assembler()
    d = _write(
        tmp_path,
        "901.md",
        "<!-- section: Fixed -->\n- quoting a fragment (#901):\n\n"
        "  ```\n  <!-- section: Changed -->\n  ```\n",
    )
    [(num, section, _text, _path)] = mod.read_fragments(d)
    assert (num, section) == (901, "Fixed")


def test_split_fragments_each_keep_their_section(tmp_path):
    mod = _assembler()
    d = _write(tmp_path, "902.md", "<!-- section: Fixed -->\n- one (#902).\n")
    _write(tmp_path, "902-removed.md", "<!-- section: Removed -->\n- two (#902).\n")
    got = [(n, s) for n, s, _t, _p in mod.read_fragments(d)]
    assert got == [(902, "Fixed"), (902, "Removed")]


def test_every_real_fragment_assembles():
    mod = _assembler()
    # read_fragments refuses a second section line (outside a fence) itself (#660)
    assert mod.read_fragments(ROOT / "changelog.d"), "changelog.d/ has fragments"
