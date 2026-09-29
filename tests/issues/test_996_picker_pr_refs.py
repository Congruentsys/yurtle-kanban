# ruff: noqa: F811  (the borrowed `yk` fixture)
"""Issue #996 (per its [steer]): the picker skips an issue that an open PR names in
its TITLE (`(#967, part 1)`), even without `Fixes #N`; a body mention doesn't count;
`depends on #<open PR>` waits while that PR is open."""

from __future__ import annotations

from tests.test_yk_next_picker import PEER, issue, pr, run_main, yk  # noqa: F401 (fixture)


def _titled(number: int, title: str, author: str = PEER, body: str = "") -> dict:
    p = pr(number, author=author)
    p["title"], p["body"] = title, body
    return p


def test_a_title_reference_skips_the_issue(yk, monkeypatch, capsys) -> None:
    prs = [_titled(984, "test: pin _files_at's filters (#967, part 1)")]
    p = prs[0]
    p["comments"] = [{"body": f"reviewed-at-sha: {p['headRefOid']}\nverdict: approve"}]
    out = run_main(yk, monkeypatch, capsys, prs, [issue(967, labels=("bug",)), issue(970)])
    assert "in progress in PR #984" in out, out
    assert "CLAIMED ISSUE #970" in out or "WOULD CLAIM ISSUE #970" in out, out


def test_a_body_mention_does_not_skip(yk, monkeypatch, capsys) -> None:
    prs = [_titled(990, "fix: something else (#980)", body="per the #967 ruling")]
    prs[0]["comments"] = [{"body": f"reviewed-at-sha: {prs[0]['headRefOid']}\nverdict: approve"}]
    out = run_main(yk, monkeypatch, capsys, prs, [issue(967)])
    assert "CLAIMED ISSUE #967" in out or "WOULD CLAIM ISSUE #967" in out, out


def test_a_longer_number_is_not_a_reference(yk, monkeypatch, capsys) -> None:
    prs = [_titled(991, "fix: thing (#9670)")]
    prs[0]["comments"] = [{"body": f"reviewed-at-sha: {prs[0]['headRefOid']}\nverdict: approve"}]
    out = run_main(yk, monkeypatch, capsys, prs, [issue(967)])
    assert "CLAIMED ISSUE #967" in out or "WOULD CLAIM ISSUE #967" in out, out


def test_depends_on_an_open_pr_waits(yk, monkeypatch, capsys) -> None:
    prs = [_titled(984, "test: unrelated title")]
    prs[0]["comments"] = [{"body": f"reviewed-at-sha: {prs[0]['headRefOid']}\nverdict: approve"}]
    out = run_main(yk, monkeypatch, capsys, prs, [issue(967, body="depends on #984")])
    assert "waits on #984" in out, out
    assert "WOULD CLAIM ISSUE #967" not in out, out
