"""#1105: `epic show` / `voyage show` and their Research Interlinks render control characters escaped.

The #251/#215 ruling as #1093 applied it (repo text reaches the terminal through `safe()`:
markup escaped + `escape_nonprintable`; `--json` raw) extends to `epic_commands._do_show`
(the header line and the linked-items table) and `research_interlinks.render_research_interlinks`
(the section `epic show` / `voyage show` print for linked HDD items) -- [steer] on #1105.

Both escaped Rich markup only, so an item file's ESC (YAML `\\e`, Turtle `\\u001B`) went to
the terminal raw (`\\x1b[2J` clears the reader's screen) and its newline (YAML / Turtle `\\n`)
broke the line (forging an output line that starts `FORGED`).

Decided behaviour: every item-sourced field these views print shows ESC as `\\x1b`, a newline
as `\\n`, brackets verbatim. There is no `epic list` / `voyage list`, and neither `show` has a
`--json`; the raw-JSON guard is the plain `show --json` of a linked item (unchanged by #1105).

Each run swaps `epic_commands.console` for a wide, NON-terminal Rich `Console` (the
interlinks renderer is handed that same console): Rich writes no colour codes of its own, so
ANY `\\x1b` in the output came from an item.

Controls: printable text with `\\` and `[x]` renders exactly as written.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import config as config_mod
from yurtle_kanban import epic_commands
from yurtle_kanban.cli import main

# the texts as they are once parsed; the files carry them as YAML / Turtle escapes
# 24 characters once escaped: within every slice these views cut a label to ([:30] the
# shortest), whether it is cut before or after escaping
TITLE = "T\x1b[2J\nFORGED[b]x[/b]"
TITLE_SHOWN = "T\\x1b[2J\\nFORGED[b]x[/b]"
# short: the Assignee column is 10 wide, Target 14, Unit 12, Category 14
ASSIGNEE = "b\x1b[1m"
ASSIGNEE_SHOWN = "b\\x1b[1m"
TARGET = "t\x1b[5m"
TARGET_SHOWN = "t\\x1b[5m"
UNIT = "u\x1b[4m"
UNIT_SHOWN = "u\\x1b[4m"
CATEGORY = "c\x1b[3m"
CATEGORY_SHOWN = "c\\x1b[3m"

PRINTABLE = r"back\slash [x] and [bold]b[/bold]"

CONFIG = (
    "kanban:\n  theme: nautical\n  paths:\n    root: kanban-work/\n"
    "    scan_paths:\n      - kanban-work/\n"
)


def _yaml(text: str) -> str:
    """`text` as a YAML double-quoted scalar (`\\e`, `\\n`, `\\\\`, `\\"` escapes)."""
    body = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\x1b", "\\e").replace("\n", "\\n")
    )
    return f'"{body}"'


def _ttl(text: str) -> str:
    """`text` as a Turtle string literal (`\\u001B`, `\\n`, `\\\\`, `\\"` escapes)."""
    body = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\x1b", "\\u001B")
        .replace("\n", "\\n")
    )
    return f'"{body}"'


# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(
    repo: Path, item_id: str, item_type: str, front: dict[str, str], turtle: str = ""
) -> None:
    lines = [f"id: {item_id}", f"type: {item_type}", "priority: medium", "created: 2026-09-25"]
    lines += [f"{k}: {v}" for k, v in front.items()]
    body = f"# {item_id}\n\n"
    if turtle:
        body += "```turtle\n" + turtle + "```\n"
    path = repo / "kanban-work" / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + "\n".join(lines) + "\n---\n\n" + body)


PREFIXES_TTL = (
    "@prefix hyp: <https://nusy.dev/hypothesis/> .\n"
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix expr: <https://nusy.dev/experiment/> .\n"
    "@prefix measure: <https://nusy.dev/measure/> .\n"
    "@prefix lit: <https://nusy.dev/literature/> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n\n"
)


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(CONFIG)
    (tmp_path / "kanban-work").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _clean() -> None:
    config_mod._theme_cache.clear()


@pytest.fixture
def evil_repo(tmp_path: Path) -> Path:
    """VOY-001 (evil title) links EXP-001 (evil title + assignee) and one HDD item of
    each rendered kind, whose labels / target / unit / category carry ESC + newline."""
    repo = _repo(tmp_path)
    link = {"status": "backlog", "related": "[VOY-001]"}
    _item(repo, "VOY-001", "voyage", {"title": _yaml(TITLE), "status": "in_progress"})
    _item(
        repo,
        "EXP-001",
        "expedition",
        {"title": _yaml(TITLE), "assignee": _yaml(ASSIGNEE), **link},
    )
    _item(
        repo,
        "PAPER-130",
        "paper",
        {"title": '"p"', **link},
        PREFIXES_TTL + f"<#PAPER-130> a paper:Paper ;\n    rdfs:label {_ttl(TITLE)} .\n",
    )
    _item(
        repo,
        "H130.1",
        "hypothesis",
        {"title": '"h"', **link},
        PREFIXES_TTL + f"<#H130.1> a hyp:Hypothesis ;\n    rdfs:label {_ttl(TITLE)} ;\n"
        f"    hyp:paper paper:PAPER-130 ;\n    hyp:target {_ttl(TARGET)} .\n",
    )
    _item(
        repo,
        "EXPR-130",
        "experiment",
        {"title": '"e"', **link},
        PREFIXES_TTL + f"<#EXPR-130> a expr:Experiment ;\n    rdfs:label {_ttl(TITLE)} ;\n"
        "    expr:hypothesis hyp:H130.1 .\n",
    )
    _item(
        repo,
        "M-130",
        "measure",
        {"title": '"m"', **link},
        PREFIXES_TTL + f"<#M-130> a measure:Measure ;\n    rdfs:label {_ttl(TITLE)} ;\n"
        f"    measure:unit {_ttl(UNIT)} ;\n    measure:category {_ttl(CATEGORY)} .\n",
    )
    # no rdfs:label: the section falls back to the item's (YAML) title
    _item(
        repo,
        "LIT-130",
        "literature",
        {"title": _yaml(TITLE), **link},
        PREFIXES_TTL + "<#LIT-130> a lit:Literature ;\n    lit:explores hyp:H130.1 .\n",
    )
    return repo


def _run(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> str:
    """Run with `epic_commands.console` swapped for a wide non-terminal one: no colour, so
    any ESC in the output is an item's."""
    buf = io.StringIO()
    monkeypatch.setattr(epic_commands, "console", Console(file=buf, width=300))
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, (result.output, result.stderr, result.exception)
    return buf.getvalue() + result.output


def _section(out: str) -> str:
    """The Research Interlinks section of a `show`'s output."""
    assert "Research Interlinks" in out, f"no interlinks section rendered: {out!r}"
    return out.split("Research Interlinks", 1)[1]


def _no_raw_control(out: str) -> None:
    assert "\x1b" not in out, f"raw ESC reached the terminal: {out!r}"
    assert not any(line.lstrip(" │┃|").startswith("FORGED") for line in out.splitlines()), (
        f"a newline forged an output line: {out!r}"
    )


GROUPS = pytest.mark.parametrize("group", ["epic", "voyage"])


# --- the fixture really is evil (so the escaped checks below prove something) -----------


def test_fixture_items_parse_with_the_raw_text(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(evil_repo)
    for item_id in ("VOY-001", "EXP-001", "LIT-130"):
        result = CliRunner().invoke(main, ["show", item_id, "--json"])
        assert result.exit_code == 0, (result.output, result.exception)
        assert json.loads(result.output)["title"] == TITLE


# --- show: header + linked-items table --------------------------------------------------


@GROUPS
def test_show_has_no_raw_control_characters(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    _no_raw_control(_run(evil_repo, [group, "show", "VOY-001"], monkeypatch))


@GROUPS
def test_show_header_renders_the_title_escaped(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    out = _run(evil_repo, [group, "show", "VOY-001"], monkeypatch)
    header = out.split("Progress:", 1)[0]
    assert TITLE_SHOWN in header, f"header title not shown literally: {header!r}"


@GROUPS
@pytest.mark.parametrize("shown", [TITLE_SHOWN, ASSIGNEE_SHOWN], ids=["title", "assignee"])
def test_show_table_renders_each_field_escaped(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch, group: str, shown: str
) -> None:
    out = _run(evil_repo, [group, "show", "VOY-001"], monkeypatch)
    table = out.split("Progress:", 1)[1].split("Research Interlinks", 1)[0]
    assert shown in table, f"{shown!r} not shown literally in the items table: {table!r}"


# --- show: Research Interlinks ----------------------------------------------------------


def test_interlinks_have_no_raw_control_characters(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_raw_control(_section(_run(evil_repo, ["voyage", "show", "VOY-001"], monkeypatch)))


@pytest.mark.parametrize(
    "heading",
    ["Papers:", "Hypotheses:", "Experiments:", "Measures:", "Literature:"],
    ids=["paper", "hypothesis", "experiment", "measure", "literature-title-fallback"],
)
def test_interlinks_render_each_label_escaped(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch, heading: str
) -> None:
    section = _section(_run(evil_repo, ["voyage", "show", "VOY-001"], monkeypatch))
    part = section.split(heading, 1)[1]
    # up to the next group heading
    for nxt in ("Papers:", "Hypotheses:", "Experiments:", "Measures:", "Literature:"):
        part = part.split(nxt, 1)[0]
    assert TITLE_SHOWN in part, f"{heading} label not shown literally: {part!r}"


@pytest.mark.parametrize(
    "shown",
    [TARGET_SHOWN, UNIT_SHOWN, CATEGORY_SHOWN],
    ids=["hyp-target", "measure-unit", "measure-category"],
)
def test_interlinks_render_each_triple_escaped(
    evil_repo: Path, monkeypatch: pytest.MonkeyPatch, shown: str
) -> None:
    section = _section(_run(evil_repo, ["voyage", "show", "VOY-001"], monkeypatch))
    assert shown in section, f"{shown!r} not shown literally: {section!r}"


# --- control: printable text is unchanged -----------------------------------------------


@pytest.fixture
def printable_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    link = {"status": "backlog", "related": "[VOY-001]"}
    _item(repo, "VOY-001", "voyage", {"title": _yaml(PRINTABLE), "status": "in_progress"})
    _item(repo, "EXP-001", "expedition", {"title": _yaml(PRINTABLE), **link})
    _item(
        repo,
        "PAPER-130",
        "paper",
        {"title": '"p"', **link},
        PREFIXES_TTL + f"<#PAPER-130> a paper:Paper ;\n    rdfs:label {_ttl(PRINTABLE)} .\n",
    )
    return repo


@GROUPS
def test_printable_text_renders_unchanged(
    printable_repo: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    out = _run(printable_repo, [group, "show", "VOY-001"], monkeypatch)
    header, rest = out.split("Progress:", 1)
    table, section = rest.split("Research Interlinks", 1)
    for where, text in (("header", header), ("table", table), ("interlinks", section)):
        assert PRINTABLE in text, f"{where}: {text!r}"
    assert "\\\\" not in out, f"a printable backslash was escaped: {out!r}"
