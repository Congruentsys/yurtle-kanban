# ruff: noqa: F811
"""Issue #721 — follow-ups from the review of PR #717 (#576).

Spec: the [steer] decision on the issue.

1. Dependency lists are compared upper-cased for change detection and the no-op check;
   an existing entry already equal ignoring case keeps its line and comment.
2. `--tag ""` or a blank tag is refused.
3. `update` on an ID that is itself duplicated across boards is refused, naming both files.
4. The `dependency_cycles` docstring says one cycle per start node.
5. `update_item` spells out its keyword arguments instead of `**edges: Any`.

Fixture: the two-board repo of `test_576_cli_update_deps.py`.
"""

from __future__ import annotations

import inspect

import pytest

from tests.issues.test_576_cli_update_deps import (
    Repo,
    _git,
    _item_text,
    _ok,
    _refused,
    repo,  # noqa: F401  (fixture)
)
from yurtle_kanban.service import KanbanService

# ---------------------------------------------------------------------------
# 1: lower-case dependency IDs
# ---------------------------------------------------------------------------


def _lower_dep(repo: Repo) -> None:
    """EXP-2 depends on `exp-3`, written by hand in lower case with a YAML comment."""
    p = repo.path("EXP-2")
    text = p.read_text(encoding="utf-8").replace(
        "depends_on: []\n", "depends_on: [exp-3]  # hand\n"
    )
    assert "depends_on: [exp-3]  # hand\n" in text
    p.write_text(text, encoding="utf-8")
    repo.commit("lower-case dependency")


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--add-dep", "EXP-3"], id="add-existing-other-case"),
        pytest.param(["--rm-dep", "EXP-9"], id="rm-absent"),
    ],
)
def test_lower_case_dependency_noop(repo: Repo, argv: list[str]) -> None:
    _lower_dep(repo)
    before, head = repo.snapshot(), repo.head()
    result = _ok(["update", "EXP-2", *argv])
    assert "no changes" in result.output, result.output
    assert repo.snapshot() == before, "file rewritten"
    assert repo.head() == head, "commit made"


def test_lower_case_dependency_add_other(repo: Repo) -> None:
    _lower_dep(repo)
    head = repo.head()
    _ok(["update", "EXP-2", "--add-dep", "EXP-4"])
    assert repo.head() != head, "no commit made"
    assert [d.upper() for d in repo.deps("EXP-2")] == ["EXP-3", "EXP-4"]
    subject = _git(repo.root, "log", "-1", "--format=%s").strip()
    assert "+EXP-4" in subject, subject
    assert "+EXP-3" not in subject, subject
    assert "-exp-3" not in subject.lower(), subject


# ---------------------------------------------------------------------------
# 2: blank tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["", "   "], ids=["empty", "blank"])
def test_blank_tag_is_refused(repo: Repo, tag: str) -> None:
    _refused(repo, ["update", "EXP-2", "--tag", tag])


# ---------------------------------------------------------------------------
# 3: the updated ID is itself duplicated across boards
# ---------------------------------------------------------------------------


def test_duplicated_item_id_is_refused(repo: Repo) -> None:
    dup_a = repo.root / "work" / "expeditions" / "EXP-9-a.md"
    dup_b = repo.root / "research" / "ideas" / "EXP-9-b.md"
    dup_b.parent.mkdir(parents=True, exist_ok=True)
    dup_a.write_text(_item_text("EXP-9", []), encoding="utf-8")
    dup_b.write_text(
        _item_text("EXP-9", []).replace("type: expedition", "type: idea"), encoding="utf-8"
    )
    repo.commit("duplicate EXP-9")
    _refused(
        repo,
        ["update", "EXP-9", "--title", "x"],
        "EXP-9",
        "work/expeditions/EXP-9-a.md",
        "research/ideas/EXP-9-b.md",
    )


# ---------------------------------------------------------------------------
# 4: dependency_cycles docstring
# ---------------------------------------------------------------------------


def test_dependency_cycles_docstring_says_one_cycle_per_start_node() -> None:
    doc = KanbanService.dependency_cycles.__doc__ or ""
    assert "one cycle per" in " ".join(doc.split()).lower(), doc


# ---------------------------------------------------------------------------
# 5: update_item keyword arguments are spelled out
# ---------------------------------------------------------------------------


def test_update_item_has_named_edge_parameters() -> None:
    params = inspect.signature(KanbanService.update_item).parameters
    kinds = {p.kind for p in params.values()}
    assert inspect.Parameter.VAR_KEYWORD not in kinds, list(params)
    for name in ("depends_on", "related", "allow_unknown"):
        assert name in params, (name, list(params))
