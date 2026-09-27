# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#767: the MCP `kanban_add_comment` pre-scan text check (#755) calls the public
`models.check_encodable`, as the CLI does, not the service's private `_check_text`.

Spec ([steer] on #767, bucket-1): the observable is that the pre-scan check does not
lean on `KanbanService._check_text`. With `_check_text` no-op'd, a lone-surrogate
comment or author is still refused with ZERO `scan()` calls, the error still names
the field, and nothing is written or committed. Messages and behaviour are otherwise
unchanged: a valid comment still makes exactly one scan.

Scans are counted with #755's `_served` helper.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401
from tests.issues.test_755_comment_checks_before_scan import ITEM, SURROGATE, _served
from yurtle_kanban.service import KanbanService


def _no_private_check(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """No-op `KanbanService._check_text` on the class; record what it was asked."""
    asked: list[dict[str, object]] = []

    def noop(**fields: object) -> None:
        asked.append(fields)

    monkeypatch.setattr(KanbanService, "_check_text", staticmethod(noop))
    return asked


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"comment": f"bad {SURROGATE} bytes", "author": "tester"}, "comment"),
        ({"comment": "hello", "author": f"tes{SURROGATE}ter"}, "author"),
    ],
    ids=["bad-comment", "bad-author"],
)
def test_the_pre_scan_refusal_does_not_need_the_private_check(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, args: dict[str, Any], needle: str
) -> None:
    mcp, calls = _served(repo, monkeypatch)
    _no_private_check(monkeypatch)
    before, head = repo.snapshot(), repo.head()
    out = mcp.handle_tool_call("kanban_add_comment", {"item_id": ITEM, **args})
    assert len(calls) == 0, (
        f"with _check_text no-op'd, a bad {needle} scanned {len(calls)} time(s) first: "
        f"the MCP pre-scan check still leans on the private service method ({out})"
    )
    assert "error" in out, out
    assert needle in out["error"] and "invalid UTF-8" in out["error"], out
    assert repo.snapshot() == before, "a refused comment wrote a file"
    assert repo.head() == head, "a refused comment committed"


def test_a_valid_comment_still_scans_once_without_the_private_check(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: the #742 freshness scan stays for a comment that is written."""
    mcp, calls = _served(repo, monkeypatch)
    _no_private_check(monkeypatch)
    head = repo.head()
    out = mcp.handle_tool_call(
        "kanban_add_comment", {"item_id": ITEM, "comment": "hello there", "author": "tester"}
    )
    assert out.get("success") is True, out
    assert "hello there" in (repo.root / repo.rel(ITEM)).read_text(encoding="utf-8")
    assert repo.head() != head, "a valid comment was not committed"
    assert len(calls) == 1, f"a valid comment scanned {len(calls)} times"
