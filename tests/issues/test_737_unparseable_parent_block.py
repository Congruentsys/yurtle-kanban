# ruff: noqa: F811  (the `world` fixture is imported, then used as a parameter)
"""Issue #737 — an unparseable parent turtle block is not 'already links'.

`_modify_turtle_block` returned changed=False both when the link was already there
and when the block could not be parsed (or had no URI subject), so
`parent_link_state` and `_parent_link_blob` reported 'linked' and the CLI printed
'<PARENT> already links to <CHILD>', plus the rdflib parse warning twice.

Decided behaviour ([steer] on #737):

- A new parent-link state, 'unparseable': rdflib can't parse the block, OR the block
  has no URIRef subject.
- `service.parent_link_state(...)` and a `--push` create's `result["parent_state"]`
  (read from origin's copy, `_parent_link_blob`) both report it.
- The CLI prints '<PARENT> has a turtle block that can't be parsed: no inverse
  reference written' and never 'already links'.
- The parse failure is no longer a WARNING (debug at most): the CLI line says it.
- Control: a genuinely linked parent still says 'linked' / 'already links'.
- #724 round 2: origin's parent has NO turtle block while the local copy is linked;
  a `--push` create reports 'no-block' from origin's copy.

Reuses the #645/#674/#705 real-git harness.
"""

from __future__ import annotations

import logging
import re

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import HYP, PAPER, seed_on_origin
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    drop_remote,
    flat,
    world,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

CHILD = "H130.1"
UNPARSEABLE_LINE = "PAPER-130 has a turtle block that can't be parsed: no inverse reference written"
ALREADY = "PAPER-130 already links to H130.1"
BLOCK_RE = re.compile(r"```turtle\n.*?```\n", re.S)

# the issue's repro: `;;; broken` — rdflib raises BadSyntax
BROKEN = (
    "```turtle\n"
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "\n"
    "<#PAPER-130> a paper:Paper ;\n"
    '    rdfs:label "A paper" ;;; broken\n'
    "```\n"
)
# parses, but only blank-node subjects: nothing to hang the inverse link on
NO_SUBJECT = (
    "```turtle\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "\n"
    '[] rdfs:label "x" .\n'
    "```\n"
)
# the child is already linked
LINKED = (
    "```turtle\n"
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix hyp: <https://nusy.dev/hypothesis/> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "\n"
    "<#PAPER-130> a paper:Paper ;\n"
    '    rdfs:label "A paper" ;\n'
    f"    paper:hasHypothesis hyp:{CHILD} .\n"
    "```\n"
)
NO_BLOCK = ""

BAD_BLOCKS = [pytest.param(BROKEN, id="broken"), pytest.param(NO_SUBJECT, id="no-subject")]


def set_block(path, block: str) -> None:
    text = path.read_text()
    assert BLOCK_RE.search(text), text
    path.write_text(BLOCK_RE.sub(lambda _m: block, text, count=1))


def local_parent(world: World, monkeypatch, block: str) -> None:
    """Seed PAPER-130, drop the remote, and commit the paper with `block`."""
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    set_block(world.a / PAPER, block)
    git(world.a, "commit", "-qam", "paper: rewrite the turtle block")
    config_mod._theme_cache.clear()


def service(world: World) -> KanbanService:
    config_mod._theme_cache.clear()
    return KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)


def origin_parent(world: World, monkeypatch, origin_block: str, local_block: str) -> None:
    """#724 round 2 shape: origin's paper holds `origin_block` (pushed by rival B);
    A is on a feature branch whose paper holds `local_block`, so a `--push` create
    builds the link against origin's copy, not the local one."""
    seed_on_origin(world, monkeypatch, HYP)
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", "origin/main")
    set_block(world.b / PAPER, origin_block)
    git(world.b, "commit", "-qam", "origin: rewrite the paper's turtle block")
    git(world.b, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(world.a, "checkout", "-q", "-b", "feat")
    set_block(world.a / PAPER, local_block)
    git(world.a, "commit", "-qam", "feat: rewrite the paper's turtle block locally")
    config_mod._theme_cache.clear()


class ParseWarnings:
    """Capture WARNING+ records from the yurtle-kanban logger (it may not propagate)."""

    def __init__(self, caplog: pytest.LogCaptureFixture) -> None:
        self.caplog = caplog

    def __enter__(self) -> ParseWarnings:
        logging.getLogger("yurtle-kanban").addHandler(self.caplog.handler)
        self.caplog.set_level(logging.DEBUG)
        return self

    def __exit__(self, *exc) -> None:
        logging.getLogger("yurtle-kanban").removeHandler(self.caplog.handler)

    def about_parsing(self) -> list[str]:
        return [
            r.getMessage()
            for r in self.caplog.records
            if r.levelno >= logging.WARNING and "pars" in r.getMessage().lower()
        ]


# --- the service's state, local copy ---------------------------------------------


@pytest.mark.parametrize("block", BAD_BLOCKS)
def test_parent_link_state_is_unparseable(world, monkeypatch, block) -> None:
    local_parent(world, monkeypatch, block)
    assert service(world).parent_link_state("PAPER-130", "hypothesis", CHILD) == "unparseable"


def test_control_parent_link_state_linked(world, monkeypatch) -> None:
    local_parent(world, monkeypatch, LINKED)
    assert service(world).parent_link_state("PAPER-130", "hypothesis", CHILD) == "linked"


def test_control_parent_link_state_no_block(world, monkeypatch) -> None:
    local_parent(world, monkeypatch, NO_BLOCK)
    assert service(world).parent_link_state("PAPER-130", "hypothesis", CHILD) == "no-block"


# --- the CLI line, local create (no remote) --------------------------------------


@pytest.mark.parametrize("block", BAD_BLOCKS)
def test_local_create_says_unparseable(world, monkeypatch, caplog, block) -> None:
    local_parent(world, monkeypatch, block)
    with ParseWarnings(caplog) as warnings:
        result = invoke(world, monkeypatch, [*HYP.argv, "--id", CHILD])
    out = flat(result)
    assert result.exit_code == 0, out
    assert UNPARSEABLE_LINE in out, out
    assert "already links" not in out, out
    assert out.count("can't be parsed") == 1, out
    assert not warnings.about_parsing(), warnings.about_parsing()


def test_control_local_create_already_links(world, monkeypatch) -> None:
    local_parent(world, monkeypatch, LINKED)
    result = invoke(world, monkeypatch, [*HYP.argv, "--id", CHILD])
    out = flat(result)
    assert result.exit_code == 0, out
    assert ALREADY in out, out
    assert "can't be parsed" not in out, out


# --- `--push`: the state comes from origin's copy --------------------------------


@pytest.mark.parametrize("block", BAD_BLOCKS)
def test_push_create_reports_origins_unparseable_block(
    world, monkeypatch, caplog, block
) -> None:
    """Origin's paper is unparseable; the local copy already links the child. The
    line must describe origin's copy (the link was built against it)."""
    origin_parent(world, monkeypatch, block, LINKED)
    with ParseWarnings(caplog) as warnings:
        result = invoke(world, monkeypatch, [*HYP.argv, "--id", CHILD])
    out = flat(result)
    assert result.exit_code == 0, out
    assert UNPARSEABLE_LINE in out, out
    assert "already links" not in out and "Updated PAPER-130" not in out, out
    assert out.count("can't be parsed") == 1, out
    assert not warnings.about_parsing(), warnings.about_parsing()


@pytest.mark.parametrize(
    ("origin_block", "expected"),
    [
        pytest.param(BROKEN, "unparseable", id="broken"),
        pytest.param(NO_SUBJECT, "unparseable", id="no-subject"),
        pytest.param(NO_BLOCK, "no-block", id="control-no-block"),
        pytest.param(LINKED, "linked", id="control-linked"),
    ],
)
def test_push_parent_state_read_from_origins_copy(
    world, monkeypatch, origin_block, expected
) -> None:
    """The `--push` parent_state (`_parent_link_blob`) describes origin's copy,
    whatever the local copy (linked here) says."""
    origin_parent(world, monkeypatch, origin_block, LINKED)
    git(world.a, "fetch", "-q", "origin")
    linked, state = service(world)._parent_link_blob(
        "origin/main", "PAPER-130", "hypothesis", CHILD
    )
    assert linked == {}, linked
    assert state == expected, state


@pytest.mark.parametrize(
    ("origin_block", "expected"),
    [
        pytest.param(NO_BLOCK, "no turtle block", id="origin-no-block"),
        pytest.param(LINKED, ALREADY, id="origin-linked"),
    ],
)
def test_push_create_origin_state_with_local_linked(
    world, monkeypatch, origin_block, expected
) -> None:
    """#724 round 2 (controls): with the local copy linked, the line still comes
    from origin's copy — 'no turtle block' when origin has none, 'already links'
    when origin links it too."""
    origin_parent(world, monkeypatch, origin_block, LINKED)
    result = invoke(world, monkeypatch, [*HYP.argv, "--id", CHILD])
    out = flat(result)
    assert result.exit_code == 0, out
    assert expected in out, out
    assert "can't be parsed" not in out, out
    if origin_block == NO_BLOCK:
        assert "already" not in out, out
