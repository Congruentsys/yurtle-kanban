"""Issue #812 — a parent link is appended, the block left as written.

`KanbanService._modify_turtle_block` parsed the parent's turtle block with rdflib and
SERIALIZED it back: relative `<#H-012>` IRIs came back as `<urn:yurtle:block#H-012>`,
and predicates/objects were reordered, so every child create rewrote the paper's
hand-written block.

Decided behaviour ([steer] on #812):

- rdflib still parses the block (to find the subject, and to see whether the triple
  is already there), but the new inverse triple is APPENDED as one line at the end
  of the block's inner text: `<subject> <predicate> <child> .`
- the subject is written `<#ID>` when it is `urn:yurtle:block#ID`, in full `<...>`
  otherwise;
- the predicate and the child use a prefix the block already declares
  (`paper:hasHypothesis`, `hyp:H130.1`), else the full IRI;
- every original line of the block stays byte-identical (CRLF files included);
- the result still parses and holds the new triple;
- a second link of the same child changes nothing ('linked');
- #737 unchanged: an unparseable or subjectless block is 'unparseable';
- the local path (`link_parent` / `update_parent_turtle_block`) and the `--push`
  path (`_parent_link_blob`) both behave so.

The block below is modelled on nusy-product-team's research/papers/PAPER-102.md
and PAPER-108.md: prefixes, a `<#PAPER-...>` subject, several predicates, relative
object IRIs, blank-node lists, comments and blank lines.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from rdflib import Graph, Namespace, URIRef

from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.service import KanbanService

PAPER_NS = Namespace("https://nusy.dev/paper/")
HYP_NS = Namespace("https://nusy.dev/hypothesis/")
EXPR_NS = Namespace("https://nusy.dev/experiment/")
BASE = "urn:yurtle:block"

PAPER_REL = Path("research/papers/PAPER-130-Brain-Architecture.md")
HYP_REL = Path("research/hypotheses/H130.1-Accuracy-improves.md")

SUBJECT_LINE = "<#PAPER-130> a paper:Paper ;"


def paper_inner(subject_line: str = SUBJECT_LINE, *, hyp_prefix: bool = True) -> str:
    """A hand-written paper block (inner text, LF), in the shape of the real fleet papers."""
    return (
        "@prefix paper: <https://nusy.dev/paper/> .\n"
        + ("@prefix hyp: <https://nusy.dev/hypothesis/> .\n" if hyp_prefix else "")
        + "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "\n"
        "# PAPER-130 - hand-written; keep it as it is\n"
        f"{subject_line}\n"
        '    rdfs:label "Brain Architecture: A Hybrid Pipeline" ;\n'
        '    paper:status "draft" ;\n'
        '    paper:venue "arXiv" ;\n'
        "    paper:hypothesis <#H130.0>, <#H130.9> ;\n"
        '    paper:folder "research/Paper130-Brain/" ;\n'
        "\n"
        "    # === Version History ===\n"
        "    paper:version [\n"
        '        paper:stage "outline" ;\n'
        '        paper:file "OUTLINE.md"\n'
        "    ]\n"
        "    , [\n"
        '        paper:stage "current" ;\n'
        '        paper:file "PAPER-130.md"\n'
        "    ]\n"
        "    ;\n"
        "\n"
        '    paper:pdf "PAPER-130.pdf" .\n'
    )


def hypothesis_inner() -> str:
    """A hypothesis block that declares hyp: and paper: but not expr:."""
    return (
        "@prefix hyp: <https://nusy.dev/hypothesis/> .\n"
        "@prefix paper: <https://nusy.dev/paper/> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "\n"
        "<#H130.1> a hyp:Hypothesis ;\n"
        '    rdfs:label "Accuracy improves" ;   # the claim\n'
        "    hyp:paper paper:PAPER-130 ;\n"
        '    hyp:target ">=85%" .\n'
    )


def paper_file(inner: str) -> str:
    return (
        "---\n"
        "id: PAPER-130\n"
        'title: "Brain Architecture"\n'
        "type: paper\n"
        "status: draft\n"
        "created: 2026-01-01\n"
        "tags: []\n"
        "---\n"
        "\n"
        "# PAPER-130: Brain Architecture\n"
        "\n"
        "Some prose before the block.\n"
        "\n"
        "```turtle\n" + inner + "```\n"
        "\n"
        "## Content\n"
        "\n"
        "More prose after the block.\n"
    )


def hypothesis_file(inner: str) -> str:
    return (
        "---\n"
        "id: H130.1\n"
        'title: "Accuracy improves"\n'
        "type: hypothesis\n"
        "status: backlog\n"
        "created: 2026-01-01\n"
        "paper: Paper130\n"
        "tags: []\n"
        "---\n"
        "\n"
        "# H130.1: Accuracy improves\n"
        "\n"
        "```turtle\n" + inner + "```\n"
    )


def with_lines(text: str, *lines: str, eol: str = "\n") -> str:
    """`text` with `lines` inserted just before the closing fence of its first turtle
    block: the only change a link may make."""
    start = text.index("```turtle")
    close = text.index(eol + "```", start + 1) + len(eol)
    return text[:close] + "".join(line + eol for line in lines) + text[close:]


# --- repo harness --------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@test.com")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / ".kanban").mkdir()
    for d in ("ideas", "literature", "papers", "hypotheses", "experiments", "measures"):
        (tmp_path / "research" / d).mkdir(parents=True)
    KanbanConfig(
        theme="hdd",
        paths=PathConfig(
            root="research/",
            scan_paths=[
                f"research/{d}/"
                for d in ("ideas", "literature", "papers", "hypotheses", "experiments", "measures")
            ],
        ),
    ).save(tmp_path / ".kanban" / "config.yaml")
    return tmp_path


def service(repo: Path) -> KanbanService:
    svc = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    svc.scan()
    return svc


def write(repo: Path, rel: Path, text: str) -> Path:
    path = repo / rel
    path.write_bytes(text.encode("utf-8"))
    return path


def commit_all(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")


def parse(text_or_inner: str) -> Graph:
    """The first turtle block of a file (or an inner text), parsed as the service does."""
    inner = text_or_inner
    if "```turtle" in inner:
        inner = inner.replace("\r\n", "\n").split("```turtle\n", 1)[1].split("```", 1)[0]
    g = Graph()
    g.parse(data=inner, format="turtle", publicID=BASE)
    return g


# --- local path: link_parent / update_parent_turtle_block --------------------------

REL_SUBJECT = URIRef(f"{BASE}#PAPER-130")
ABS_SUBJECT = PAPER_NS["PAPER-130"]

SUBJECTS = [
    pytest.param(SUBJECT_LINE, REL_SUBJECT, "<#PAPER-130>", id="relative"),
    pytest.param("paper:PAPER-130 a paper:Paper ;", ABS_SUBJECT,
                 "<https://nusy.dev/paper/PAPER-130>", id="prefixed"),
    pytest.param("<https://nusy.dev/paper/PAPER-130> a paper:Paper ;", ABS_SUBJECT,
                 "<https://nusy.dev/paper/PAPER-130>", id="absolute"),
]


@pytest.mark.parametrize("subject_line, subject, written", SUBJECTS)
def test_link_appends_one_line_and_keeps_the_block(repo, subject_line, subject, written):
    before = paper_file(paper_inner(subject_line))
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_bytes().decode("utf-8")
    assert after == with_lines(before, f"{written} paper:hasHypothesis hyp:H130.1 .")
    assert (subject, PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(after)


def test_relative_iris_stay_relative(repo):
    """The issue's symptom: `<#H130.0>` must not come back as `<urn:yurtle:block#H130.0>`."""
    path = write(repo, PAPER_REL, paper_file(paper_inner()))
    assert service(repo).update_parent_turtle_block("PAPER-130", "hypothesis", "H130.1")
    after = path.read_text(encoding="utf-8")
    assert "urn:yurtle:block" not in after
    assert "    paper:hypothesis <#H130.0>, <#H130.9> ;\n" in after
    assert "@base" not in after


def test_undeclared_child_prefix_is_written_in_full(repo):
    """The real fleet papers declare paper: and rdfs: only: hyp: is not there to use."""
    before = paper_file(paper_inner(hyp_prefix=False))
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_text(encoding="utf-8")
    assert after == with_lines(
        before, "<#PAPER-130> paper:hasHypothesis <https://nusy.dev/hypothesis/H130.1> ."
    )
    assert "@prefix hyp:" not in after  # no prefix declaration is added either
    assert (REL_SUBJECT, PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(after)


def test_undeclared_child_prefix_experiment_into_hypothesis(repo):
    """hyp: is declared (predicate prefixed), expr: is not (child in full)."""
    write(repo, PAPER_REL, paper_file(paper_inner()))
    before = hypothesis_file(hypothesis_inner())
    path = write(repo, HYP_REL, before)

    assert service(repo).link_parent("H130.1", "experiment", "EXPR-130") == "added"

    after = path.read_text(encoding="utf-8")
    assert after == with_lines(
        before, "<#H130.1> hyp:hasExperiment <https://nusy.dev/experiment/EXPR-130> ."
    )
    subject = URIRef(f"{BASE}#H130.1")
    assert (subject, HYP_NS.hasExperiment, EXPR_NS["EXPR-130"]) in parse(after)


def test_namespace_under_another_prefix_name(repo):
    """The paper namespace declared as `p:`: whatever the line says, the block keeps
    every line, gains one, and parses to the new triple."""
    inner = paper_inner().replace("@prefix paper:", "@prefix p:").replace("paper:", "p:")
    before = paper_file(inner)
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_text(encoding="utf-8")
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    assert len(new) == len(old) + 1, after
    fence = next(i for i, line in enumerate(old) if line == "```\n")
    assert new[:fence] + new[fence + 1:] == old, after
    assert (REL_SUBJECT, PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(after)


def test_crlf_file_keeps_every_byte(repo):
    before = paper_file(paper_inner()).replace("\n", "\r\n")
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_bytes().decode("utf-8")
    assert after == with_lines(
        before, "<#PAPER-130> paper:hasHypothesis hyp:H130.1 .", eol="\r\n"
    )


def test_file_ending_on_the_fence_without_newline(repo):
    """The closing fence is the file's last line and has no trailing newline."""
    before = paper_file(paper_inner()).split("```\n\n## Content", 1)[0] + "```"
    assert before.endswith("\n```")
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_text(encoding="utf-8")
    assert after == with_lines(before, "<#PAPER-130> paper:hasHypothesis hyp:H130.1 .")


def test_second_link_of_the_same_child_changes_nothing(repo):
    path = write(repo, PAPER_REL, paper_file(paper_inner()))
    svc = service(repo)
    assert svc.link_parent("PAPER-130", "hypothesis", "H130.1") == "added"
    once = path.read_bytes()
    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "linked"
    assert path.read_bytes() == once


def test_two_children_append_two_lines(repo):
    before = paper_file(paper_inner())
    path = write(repo, PAPER_REL, before)
    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"
    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.2") == "added"
    assert path.read_text(encoding="utf-8") == with_lines(
        before,
        "<#PAPER-130> paper:hasHypothesis hyp:H130.1 .",
        "<#PAPER-130> paper:hasHypothesis hyp:H130.2 .",
    )


def test_hand_written_link_is_linked(repo):
    """A link already written by hand, in the block's own shape, is 'linked'."""
    inner = paper_inner().replace(
        '    paper:pdf "PAPER-130.pdf" .\n',
        '    paper:pdf "PAPER-130.pdf" ;\n    paper:hasHypothesis hyp:H130.1 .\n',
    )
    path = write(repo, PAPER_REL, paper_file(inner))
    before = path.read_bytes()
    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "linked"
    assert path.read_bytes() == before


BROKEN_INNER = (
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "\n"
    "<#PAPER-130> a paper:Paper ;\n"
    '    rdfs:label "A paper" ;;; broken\n'
)
NO_SUBJECT_INNER = (
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "\n"
    '[] rdfs:label "x" .\n'
)


@pytest.mark.parametrize("inner", [BROKEN_INNER, NO_SUBJECT_INNER], ids=["broken", "no-subject"])
def test_737_unparseable_is_unchanged(repo, inner):
    path = write(repo, PAPER_REL, paper_file(inner))
    before = path.read_bytes()
    svc = service(repo)
    assert svc.link_parent("PAPER-130", "hypothesis", "H130.1") == "unparseable"
    assert svc.parent_link_state("PAPER-130", "hypothesis", "H130.1") == "unparseable"
    assert path.read_bytes() == before


# --- --push path: _parent_link_blob ----------------------------------------------


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_push_blob_appends_one_line(repo, eol):
    before = paper_file(paper_inner()).replace("\n", eol)
    write(repo, PAPER_REL, before)
    commit_all(repo)

    blobs, state = service(repo)._parent_link_blob("HEAD", "PAPER-130", "hypothesis", "H130.1")

    assert state is None
    assert blobs == {
        PAPER_REL: with_lines(before, "<#PAPER-130> paper:hasHypothesis hyp:H130.1 .", eol=eol)
    }


def test_push_blob_undeclared_prefix(repo):
    before = paper_file(paper_inner(hyp_prefix=False))
    write(repo, PAPER_REL, before)
    commit_all(repo)

    blobs, state = service(repo)._parent_link_blob("HEAD", "PAPER-130", "hypothesis", "H130.1")

    assert state is None
    assert blobs == {
        PAPER_REL: with_lines(
            before, "<#PAPER-130> paper:hasHypothesis <https://nusy.dev/hypothesis/H130.1> ."
        )
    }


def test_push_blob_already_linked(repo):
    write(repo, PAPER_REL, with_lines(
        paper_file(paper_inner()), "<#PAPER-130> paper:hasHypothesis hyp:H130.1 ."
    ))
    commit_all(repo)
    blobs, state = service(repo)._parent_link_blob("HEAD", "PAPER-130", "hypothesis", "H130.1")
    assert (blobs, state) == ({}, "linked")


# --- unit: _modify_turtle_block ----------------------------------------------------


def _unit(svc: KanbanService, inner: str) -> tuple[str, str | None]:
    return svc._modify_turtle_block(inner, PAPER_NS.hasHypothesis, HYP_NS["H130.1"])


def _assert_one_line_appended(inner: str, new: str, line: str) -> None:
    old_lines, new_lines = inner.splitlines(), new.splitlines()
    assert new_lines[: len(old_lines)] == old_lines, new
    assert [x for x in new_lines[len(old_lines):] if x.strip()] == [line], new


@pytest.mark.parametrize(
    "inner",
    [paper_inner(), paper_inner().rstrip("\n")],
    ids=["trailing-newline", "no-trailing-newline"],
)
def test_modify_turtle_block_appends(repo, inner):
    new, state = _unit(service(repo), inner)
    assert state is None
    _assert_one_line_appended(inner, new, "<#PAPER-130> paper:hasHypothesis hyp:H130.1 .")
    assert (REL_SUBJECT, PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(new)


def test_modify_turtle_block_linked_and_unparseable(repo):
    svc = service(repo)
    new, _ = _unit(svc, paper_inner())
    assert _unit(svc, new)[1] == "linked"
    assert _unit(svc, BROKEN_INNER)[1] == "unparseable"
    assert _unit(svc, NO_SUBJECT_INNER)[1] == "unparseable"
    assert _unit(svc, "")[1] == "unparseable"
