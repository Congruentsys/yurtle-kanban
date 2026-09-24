"""Tests for TurtleBlockBuilder — generates Turtle knowledge blocks for HDD items."""

import pytest

from yurtle_kanban.turtle_builder import TurtleBlockBuilder, _format_uri_list


@pytest.fixture
def builder():
    return TurtleBlockBuilder()


# ---------------------------------------------------------------------------
# _format_uri_list
# ---------------------------------------------------------------------------


class TestFormatUriList:
    def test_single_string(self):
        assert _format_uri_list("measure", "M-007") == "measure:M-007"

    def test_list_of_values(self):
        result = _format_uri_list("measure", ["M-007", "M-025"])
        assert result == "measure:M-007, measure:M-025"

    def test_single_element_list(self):
        assert _format_uri_list("lit", ["LIT-001"]) == "lit:LIT-001"


# ---------------------------------------------------------------------------
# Idea
# ---------------------------------------------------------------------------


class TestBuildIdea:
    def test_basic(self, builder):
        block = builder.build("idea", {"id": "IDEA-R-010", "title": "Test idea"})
        assert "```turtle" in block
        assert "```" in block
        assert '<#IDEA-R-010> a idea:Idea' in block
        assert 'rdfs:label "Test idea"' in block
        assert "@prefix idea:" in block
        assert "@prefix rdfs:" in block


# ---------------------------------------------------------------------------
# Literature
# ---------------------------------------------------------------------------


class TestBuildLiterature:
    def test_basic(self, builder):
        block = builder.build("literature", {"id": "LIT-001", "title": "Survey"})
        assert '<#LIT-001> a lit:Literature' in block
        assert 'rdfs:label "Survey"' in block
        assert "lit:explores" not in block

    def test_with_source_idea(self, builder):
        block = builder.build("literature", {
            "id": "LIT-001",
            "title": "Survey",
            "source_idea": "IDEA-R-010",
        })
        assert "lit:explores idea:IDEA-R-010" in block
        assert "@prefix idea:" in block


# ---------------------------------------------------------------------------
# Paper
# ---------------------------------------------------------------------------


class TestBuildPaper:
    def test_basic(self, builder):
        block = builder.build("paper", {"id": "PAPER-130", "title": "Brain Arch"})
        assert '<#PAPER-130> a paper:Paper' in block
        assert 'rdfs:label "Brain Arch"' in block


# ---------------------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------------------


class TestBuildHypothesis:
    def test_basic_with_paper(self, builder):
        block = builder.build("hypothesis", {
            "id": "H130.1",
            "title": "Accuracy improves",
            "paper": "130",
        })
        assert '<#H130.1> a hyp:Hypothesis' in block
        assert 'hyp:paper paper:PAPER-130' in block
        assert "@prefix paper:" in block

    def test_with_target(self, builder):
        block = builder.build("hypothesis", {
            "id": "H130.1",
            "title": "Accuracy improves",
            "paper": "130",
            "target": ">=85%",
        })
        assert 'hyp:target ">=85%"' in block

    def test_with_source_idea(self, builder):
        block = builder.build("hypothesis", {
            "id": "H130.1",
            "title": "Test",
            "paper": "130",
            "source_idea": "IDEA-R-010",
        })
        assert "hyp:sourceIdea idea:IDEA-R-010" in block
        assert "@prefix idea:" in block

    def test_with_measures(self, builder):
        block = builder.build("hypothesis", {
            "id": "H130.1",
            "title": "Test",
            "paper": "130",
            "measures": ["M-007", "M-025"],
        })
        assert "hyp:measuredBy measure:M-007, measure:M-025" in block
        assert "@prefix measure:" in block

    def test_with_literature(self, builder):
        block = builder.build("hypothesis", {
            "id": "H130.1",
            "title": "Test",
            "paper": "130",
            "literature": ["LIT-001", "LIT-003"],
        })
        assert "hyp:informedBy lit:LIT-001, lit:LIT-003" in block
        assert "@prefix lit:" in block

    def test_all_optional_fields(self, builder):
        block = builder.build("hypothesis", {
            "id": "H130.1",
            "title": "Full hypothesis",
            "paper": "130",
            "target": ">=85%",
            "source_idea": "IDEA-R-010",
            "measures": ["M-007"],
            "literature": ["LIT-001"],
        })
        assert "hyp:paper" in block
        assert "hyp:target" in block
        assert "hyp:sourceIdea" in block
        assert "hyp:measuredBy" in block
        assert "hyp:informedBy" in block


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------


class TestBuildExperiment:
    def test_basic(self, builder):
        block = builder.build("experiment", {
            "id": "EXPR-130",
            "title": "V12 accuracy test",
            "paper": "130",
            "hypothesis_id": "H130.1",
        })
        assert '<#EXPR-130> a expr:Experiment' in block
        assert 'expr:paper paper:PAPER-130' in block
        assert 'expr:hypothesis hyp:H130.1' in block

    def test_with_measures(self, builder):
        block = builder.build("experiment", {
            "id": "EXPR-130",
            "title": "Test",
            "paper": "130",
            "hypothesis_id": "H130.1",
            "measures": ["M-007", "M-025"],
        })
        assert "expr:measure measure:M-007, measure:M-025" in block


# ---------------------------------------------------------------------------
# Measure
# ---------------------------------------------------------------------------


class TestBuildMeasure:
    def test_basic(self, builder):
        block = builder.build("measure", {
            "id": "M-042",
            "title": "Response Latency",
            "unit": "ms",
            "category": "performance",
        })
        assert '<#M-042> a measure:Measure' in block
        assert 'measure:unit "ms"' in block
        assert 'measure:category "performance"' in block


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unknown_type_returns_empty(self, builder):
        assert builder.build("unknown_type", {"id": "X"}) == ""

    def test_title_with_quotes_escaped(self, builder):
        """Titles containing double quotes should be escaped in rdfs:label."""
        block = builder.build("idea", {
            "id": "IDEA-R-001",
            "title": 'Evaluating "Yurtle" Format',
        })
        assert r'rdfs:label "Evaluating \"Yurtle\" Format"' in block

    def test_title_with_backslash_escaped(self, builder):
        """Backslashes in titles should be escaped."""
        block = builder.build("idea", {
            "id": "IDEA-R-001",
            "title": r"Path C:\data\test",
        })
        assert r'rdfs:label "Path C:\\data\\test"' in block

    def test_only_needed_prefixes_included(self, builder):
        block = builder.build("idea", {"id": "IDEA-R-001", "title": "Test"})
        assert "@prefix idea:" in block
        assert "@prefix rdfs:" in block
        # Should NOT include unused prefixes
        assert "@prefix hyp:" not in block
        assert "@prefix expr:" not in block
        assert "@prefix measure:" not in block

    def test_block_starts_and_ends_with_fence(self, builder):
        block = builder.build("idea", {"id": "IDEA-R-001", "title": "Test"})
        lines = block.strip().split("\n")
        assert lines[0] == "```turtle"
        assert lines[-1] == "```"


# ---------------------------------------------------------------------------
# Issue #141 — every Turtle literal built from user input parses and round-trips
# ---------------------------------------------------------------------------

import rdflib  # noqa: E402
from rdflib import Literal, Namespace  # noqa: E402

from yurtle_kanban.turtle_builder import PREFIXES  # noqa: E402

_RDFS = Namespace(PREFIXES["rdfs"])
_HYP = Namespace(PREFIXES["hyp"])
_MEASURE = Namespace(PREFIXES["measure"])

_NASTY_VALUES = [
    pytest.param('say "hi" there', id="quote"),
    pytest.param("C:\\data\\x", id="backslash"),
    pytest.param("ends with backslash\\", id="trailing-backslash"),
    pytest.param("p\nq", id="newline"),
    pytest.param("p\r\nq", id="crlf"),
    pytest.param("p\rq", id="cr"),
    pytest.param("p\tq", id="tab"),
    pytest.param('x" .\n<#EVIL> a <#Injected> .\n<#Y> <#z> "', id="injection"),
    pytest.param("Explore transfer learning", id="plain"),
]


def _parse_block(block: str) -> rdflib.Graph:
    lines = block.split("\n")
    assert lines[0] == "```turtle" and lines[-1] == "```", block
    g = rdflib.Graph()
    g.parse(data="\n".join(lines[1:-1]), format="turtle", publicID="http://x/")
    return g


def _only_value(g: rdflib.Graph, predicate) -> Literal:
    values = list(g.objects(None, predicate))
    assert len(values) == 1, f"{predicate}: {values}"
    return values[0]


class TestTurtleLiteralsRoundTripIssue141:
    """Every string literal TurtleBlockBuilder writes from a user value parses
    with rdflib and reads back as exactly that value (#141)."""

    @pytest.mark.parametrize("value", _NASTY_VALUES)
    @pytest.mark.parametrize(
        "item_type", ["idea", "literature", "paper", "hypothesis", "experiment", "measure"]
    )
    def test_title_label_round_trips(self, builder, item_type, value):
        block = builder.build(item_type, {"id": "X-001", "title": value})
        g = _parse_block(block)
        assert str(_only_value(g, _RDFS.label)) == value

    @pytest.mark.parametrize("value", _NASTY_VALUES)
    def test_hypothesis_target_round_trips(self, builder, value):
        block = builder.build(
            "hypothesis", {"id": "H130.1", "title": "T", "paper": "130", "target": value}
        )
        g = _parse_block(block)
        assert str(_only_value(g, _HYP.target)) == value
        assert str(_only_value(g, _RDFS.label)) == "T"

    @pytest.mark.parametrize("value", _NASTY_VALUES)
    def test_measure_unit_round_trips(self, builder, value):
        block = builder.build(
            "measure", {"id": "M-001", "title": "T", "unit": value, "category": "c"}
        )
        g = _parse_block(block)
        assert str(_only_value(g, _MEASURE.unit)) == value
        assert str(_only_value(g, _MEASURE.category)) == "c"

    @pytest.mark.parametrize("value", _NASTY_VALUES)
    def test_measure_category_round_trips(self, builder, value):
        block = builder.build(
            "measure", {"id": "M-001", "title": "T", "unit": "u", "category": value}
        )
        g = _parse_block(block)
        assert str(_only_value(g, _MEASURE.category)) == value
        assert str(_only_value(g, _MEASURE.unit)) == "u"

    def test_injection_adds_no_triples(self, builder):
        """A title crafted to close the literal adds no subjects to the graph."""
        block = builder.build(
            "idea", {"id": "IDEA-R-001", "title": 'x" .\n<#EVIL> a <#Injected> .\n<#Y> <#z> "'}
        )
        g = _parse_block(block)
        assert set(g.subjects()) == {rdflib.URIRef("http://x/#IDEA-R-001")}
        assert len(g) == 2


class TestTurtlePlainValuesUnchangedIssue141:
    """Negative controls: plain values are written byte-identically to before (#141)."""

    def test_plain_idea_block_bytes(self, builder):
        block = builder.build("idea", {"id": "IDEA-R-010", "title": "Explore transfer learning"})
        assert block == (
            "```turtle\n"
            "@prefix idea: <https://nusy.dev/idea/> .\n"
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
            "\n"
            "<#IDEA-R-010> a idea:Idea ;\n"
            '    rdfs:label "Explore transfer learning" .\n'
            "```"
        )

    def test_plain_measure_lines(self, builder):
        block = builder.build(
            "measure",
            {"id": "M-042", "title": "Response Latency", "unit": "ms", "category": "performance"},
        )
        assert '    rdfs:label "Response Latency" ;\n' in block
        assert '    measure:unit "ms" ;\n' in block
        assert '    measure:category "performance" .\n' in block

    def test_plain_hypothesis_target_line(self, builder):
        block = builder.build(
            "hypothesis",
            {"id": "H130.1", "title": "Acc", "paper": "130", "target": ">= 95% accuracy"},
        )
        assert '    hyp:target ">= 95% accuracy" .\n' in block

    def test_quote_and_backslash_escaping_unchanged(self, builder):
        block = builder.build("idea", {"id": "IDEA-R-001", "title": 'a "b" C:\\d'})
        assert r'rdfs:label "a \"b\" C:\\d" .' in block
