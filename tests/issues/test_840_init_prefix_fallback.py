"""#840: init's template prefix fallback satisfies the prefix grammar.

Follow-up from the PR #837 review (#816):

1. The theme loader's warning for a dropped `id_prefix` describes the grammar.
   Since #817 `ID_PREFIX_FORM` mentions the optional trailing '.' (`H130.`), so
   this part is already true on main; a test pins it.
2. `init` writes each type's `_TEMPLATE.md` with the prefix
   `type_def.get("id_prefix", type_id[:4].upper())`. For a custom theme's type
   key such as `1ab`, that fallback (`1AB`) fails the #802 grammar.

Decided ([steer] on #840, bucket 1): the fallback drops the characters of the
type key that the grammar can't use and any leading non-letters, upper-cases,
takes the first 4, and falls back to `ITEM` if nothing valid remains. The result
passes through `models.id_prefix()`. A theme's explicit (already validated)
`id_prefix` is unchanged.

The prefix lands on two lines of the template (`_generate_template`):
`id: <prefix>-XXX` in the front matter and `# <prefix>-XXX: Title`.

Controls: `spike` -> `SPIK`; an explicit `id_prefix: SPK` -> `SPK`.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import _generate_template, main
from yurtle_kanban.models import id_prefix

LOGGER = "yurtle-kanban"
THEME = "mytheme840"

ID_LINE = re.compile(r"^id: (?P<prefix>.+)-XXX$", re.MULTILINE)
HEADING_LINE = re.compile(r"^# (?P<prefix>.+)-XXX: Title$", re.MULTILINE)


# --- fixtures / helpers ------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def warnings_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, attached straight to the yurtle-kanban logger (whatever its propagation)."""
    log = logging.getLogger(LOGGER)
    log.addHandler(caplog.handler)
    old_level = log.level
    log.setLevel(logging.WARNING)
    caplog.handler.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        log.removeHandler(caplog.handler)
        log.setLevel(old_level)


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, item_types: dict) -> Path:
    """A git repo holding `.kanban/themes/<THEME>.yaml` with `item_types`; cwd there."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    themes = tmp_path / ".kanban" / "themes"
    themes.mkdir(parents=True)
    theme = {"name": THEME, "item_types": item_types}
    (themes / f"{THEME}.yaml").write_text(yaml.safe_dump(theme, allow_unicode=True))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _init_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, type_key: str, extra: dict | None = None
) -> str:
    """Run `init --theme <THEME>` for one custom type `type_key`; its _TEMPLATE.md text."""
    type_def = {"name": "Custom", "path": "work/custom/", **(extra or {})}
    root = _repo(tmp_path, monkeypatch, {type_key: type_def})
    result = CliRunner().invoke(main, ["init", "--theme", THEME])
    assert result.exit_code == 0, result.output
    template = root / "work" / "custom" / "_TEMPLATE.md"
    assert template.is_file(), f"non-vacuity: init wrote no {template}:\n{result.output}"
    return template.read_text()


def _template_prefix(text: str) -> str:
    """The prefix the template carries, from its `id:` line; the heading must agree."""
    id_match = ID_LINE.search(text)
    heading = HEADING_LINE.search(text)
    assert id_match, f"no `id: <prefix>-XXX` line in the template:\n{text}"
    assert heading, f"no `# <prefix>-XXX: Title` line in the template:\n{text}"
    assert id_match["prefix"] == heading["prefix"], text
    return id_match["prefix"]


# --- the template lines carrying the prefix (pins _generate_template's shape) ------


def test_generate_template_puts_prefix_on_id_and_heading_lines() -> None:
    text = _generate_template("SPK", "spike", ["Description"])
    assert ID_LINE.search(text)["prefix"] == "SPK"
    assert HEADING_LINE.search(text)["prefix"] == "SPK"


# --- 1. the dropped-prefix warning names the trailing '.' (GREEN pin) --------------


def test_dropped_prefix_warning_mentions_trailing_dot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log: pytest.LogCaptureFixture
) -> None:
    _repo(tmp_path, monkeypatch, {"bug": {"name": "Bug", "path": "work/bugs/", "id_prefix": "1ab"}})
    data = config_mod._load_builtin_theme(THEME, tmp_path)
    assert data is not None
    assert "id_prefix" not in data["item_types"]["bug"], "non-vacuity: `1ab` was not dropped"
    dropped = [r.getMessage() for r in warnings_log.records if "'1ab'" in r.getMessage()]
    assert dropped, f"no warning names the dropped prefix:\n{warnings_log.text}"
    assert any("trailing '.'" in m for m in dropped), dropped


# --- 2. init's fallback prefix satisfies the grammar (RED) -------------------------


def test_leading_digit_key_drops_the_digit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, "1ab"))
    assert prefix == "AB"


@pytest.mark.parametrize(
    "type_key",
    [
        pytest.param("_x-y", id="leading-underscore"),
        pytest.param("ab.c", id="mid-dot"),
        pytest.param("abc-de", id="dash-at-cut"),  # `[:4]` would end on the dash: `ABC-`
        pytest.param("9-x", id="digit-then-dash"),
    ],
)
def test_fallback_prefix_is_grammar_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, type_key: str
) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, type_key))
    assert id_prefix(prefix) is not None, f"{type_key!r} -> {prefix!r} fails the grammar"


def test_mid_dot_key_has_no_dot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, "ab.c"))
    assert "." not in prefix
    assert id_prefix(prefix) is not None, prefix


@pytest.mark.parametrize("type_key", ["123", "_1.", "---"])
def test_nothing_valid_falls_back_to_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, type_key: str
) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, type_key))
    assert prefix == "ITEM"


# --- controls ------------------------------------------------------------------------


def test_control_plain_key_takes_first_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, "spike"))
    assert prefix == "SPIK"


def test_control_explicit_prefix_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = _init_template(tmp_path, monkeypatch, "spike", {"id_prefix": "SPK"})
    assert _template_prefix(text) == "SPK"
