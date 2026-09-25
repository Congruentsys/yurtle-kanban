"""#184: structured_query must pass tag/assignee needles as bound Literals.

rdflib expands ``\\uXXXX`` / ``\\UXXXXXXXX`` across the whole query text before
parsing (SPARQL 19.2), so a literal backslash-u sequence in a needle is rewritten
after ``turtle_string`` escaping. Results are wrong: the tag ``\\U00000022`` (ten
literal characters) matches the item tagged ``a"b``, and ``a\\u0041b`` raises a
parse error. The needle must reach rdflib via ``initBindings`` so it never passes
through the query text; ``UnifiedGraph.sparql`` gains a ``bindings`` parameter.

Also: the #162 CHANGELOG entry must not claim the NL query was affected (only the
structured API could carry these values).

Controls: ordinary case-insensitive substring matching, and #162's quote /
backslash needles, keep working.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from rdflib import Literal

from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.query import ParsedQuery, QueryEngine, UnifiedGraph

_CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

# Literal backslash text, built by concatenation so no editor or writer can
# decode the escape: these are NOT the characters they would encode.
_BACKSLASH = "\\"
_BIG_U_QUOTE = _BACKSLASH + "U00000022"  # 10 chars: backslash, U, 0000 0022
_SMALL_U_A = _BACKSLASH + "u0041"  # 6 chars: backslash, u, 0041
_A_SMALL_U_A_B = "a" + _SMALL_U_A + "b"  # 8 chars
assert len(_BACKSLASH) == 1
assert len(_BIG_U_QUOTE) == 10
assert len(_SMALL_U_A) == 6
assert len(_A_SMALL_U_A_B) == 8


def _item(item_id: str, field: str, value: str | None) -> WorkItem:
    return WorkItem(
        id=item_id,
        title=f"Item {item_id}",
        item_type=WorkItemType.EXPEDITION,
        status=WorkItemStatus.BACKLOG,
        file_path=Path(f"/tmp/{item_id}.md"),
        priority="medium",
        assignee=value if field == "assignee" else None,
        created=date(2026, 1, 1),
        tags=[value] if field == "tag" and value is not None else [],
    )


def _engine(field: str, values: dict[str, str]) -> QueryEngine:
    ug = UnifiedGraph()
    ug.add_items([_item(item_id, field, v) for item_id, v in values.items()])
    return QueryEngine(unified_graph=ug, embedding_index=None)


def _run(engine: QueryEngine, field: str, needle: str) -> set[str]:
    parsed = ParsedQuery(**{field: needle})
    try:
        items = engine.structured_query(parsed)
    except Exception as exc:  # noqa: BLE001 - any raise here is the bug
        pytest.fail(f"structured_query raised for {field}={needle!r}: {exc!r}")
    return {i.id for i in items}


_FIELDS = ["tag", "assignee"]


# ---------------------------------------------------------------------------
# New behaviour (RED before the fix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", _FIELDS)
def test_big_u_escape_needle_does_not_match_quote(field: str) -> None:
    engine = _engine(field, {
        "EXP-101": 'a"b',
        "EXP-102": "x" + _BIG_U_QUOTE + "y",
        "EXP-103": "plain",
    })
    ids = _run(engine, field, _BIG_U_QUOTE)
    assert ids == {"EXP-102"}, f"{field} {_BIG_U_QUOTE!r} matched {ids}"


@pytest.mark.parametrize("field", _FIELDS)
def test_small_u_escape_needle_matches_literal_text_only(field: str) -> None:
    engine = _engine(field, {
        "EXP-101": "xAy",
        "EXP-102": "x" + _SMALL_U_A + "y",
        "EXP-103": "plain",
    })
    ids = _run(engine, field, _SMALL_U_A)
    assert ids == {"EXP-102"}, f"{field} {_SMALL_U_A!r} matched {ids}"


@pytest.mark.parametrize("field", _FIELDS)
def test_embedded_small_u_escape_needle(field: str) -> None:
    engine = _engine(field, {
        "EXP-101": "aAb",
        "EXP-102": _A_SMALL_U_A_B,
        "EXP-103": "plain",
    })
    ids = _run(engine, field, _A_SMALL_U_A_B)
    assert ids == {"EXP-102"}, f"{field} {_A_SMALL_U_A_B!r} matched {ids}"


def test_unified_graph_sparql_accepts_bindings() -> None:
    ug = UnifiedGraph()
    rows = ug.sparql(
        "SELECT ?x WHERE { BIND(?v AS ?x) }", bindings={"v": Literal("hi")},
    )
    assert rows == [{"x": "hi"}]


def _changelog_162_entry() -> str:
    text = _CHANGELOG.read_text(encoding="utf-8")
    bullets = re.split(r"\n(?=- )", text)
    hits = [b for b in bullets if "(#162)" in b]
    assert len(hits) == 1, f"expected one #162 CHANGELOG bullet, found {len(hits)}"
    return " ".join(hits[0].split())


def test_changelog_162_entry_does_not_claim_nl_query() -> None:
    entry = _changelog_162_entry()
    assert "structured and NL query" not in entry
    assert not re.search(r"\bNL\b", entry), f"#162 entry still mentions NL: {entry}"


# ---------------------------------------------------------------------------
# Controls (green before and after)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", _FIELDS)
def test_control_plain_substring_case_insensitive(field: str) -> None:
    engine = _engine(field, {
        "EXP-101": "BrainWork",
        "EXP-102": "other",
    })
    assert _run(engine, field, "brain") == {"EXP-101"}
    assert _run(engine, field, "WORK") == {"EXP-101"}
    assert _run(engine, field, "zzz") == set()


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("needle", ['say "hi"', "back\\slash", 'zz") || contains("", "'])
def test_control_162_quote_backslash_literal(field: str, needle: str) -> None:
    engine = _engine(field, {
        "EXP-101": needle,
        "EXP-102": "bob",
    })
    assert _run(engine, field, needle) == {"EXP-101"}


def test_control_changelog_162_entry_exists() -> None:
    assert "escap" in _changelog_162_entry().lower()


def test_control_sparql_without_bindings() -> None:
    ug = UnifiedGraph()
    assert ug.sparql('SELECT ?x WHERE { BIND("hi" AS ?x) }') == [{"x": "hi"}]
