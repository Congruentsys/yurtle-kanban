# ruff: noqa: F811  -- the `repo` / `warnings_log` fixtures imported from #576 / #816 are re-bound
"""#817: the #802 prefix grammar's edges — NFC, combining marks, the paper-scoped dot.

Found reviewing PR #815 (#802):
- a decomposed `ÉXP` (E + U+0301) and a Devanagari word with a vowel sign are
  refused, while the precomposed forms pass;
- `next-id H-1.` is accepted and allocates `H-1.1`, in the DASHED `H-` space that
  `hypothesis create --paper=-1` is refused to keep out of.

Decided ([steer] on #817, bucket 1):
1. A prefix is NFC-normalized before the grammar check and before it is used: the
   decomposed `ÉXP` allocates as the NFC `ÉXP`, the same ID space as the precomposed
   one. Combining marks (`unicodedata.category` M*) are allowed inside a segment, so
   `कार्य` (vowel signs Mc, virama Mn) is accepted.
2. A trailing `.` is allowed only after a segment ending in a digit, with no dash in
   the prefix: `H130.` and `P2.` are accepted; `H-1.`, `IDEA-R.`, `EXP.` and `H.` are
   refused.
3. The `next-id` refusal and the theme warning (#816) mention the optional trailing
   `.` (the substring "trailing '.'").

Paths: the service (`allocate_next_id`), the CLI `next-id`, and theme loading
(`config._load_builtin_theme`, the #816 path).

Controls: every #802 control (`EXP`, `IDEA-R`, `H130.`, `ÉXP`, every shipped theme
and HDD prefix) stays accepted.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401
from tests.issues.test_802_prefix_grammar import (
    ALLOCATIONS,
    _cli_refused,
    _names_prefix_grammar,
    _State,
    test_control_every_shipped_prefix_is_accepted,  # noqa: F401  (reused control, #802)
)
from tests.issues.test_816_theme_prefix_grammar import (  # noqa: F401  (fixtures)
    SINGLE_CFG,
    THEME,
    TYPE_PATHS,
    _clean_theme_cache,
    _create,
    _prefix_warnings,
    _repo,
    _theme,
    _warnings,
    warnings_log,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused

PRECOMPOSED = "ÉXP"  # ÉXP, one code point for É
DECOMPOSED = "ÉXP"  # E + COMBINING ACUTE ACCENT, then XP
DEVANAGARI = "कार्य"  # कार्य: ka, aa-sign (Mc), ra, virama (Mn), ya

MESSAGE_DOT = "trailing '.'"

# a trailing `.` outside the paper-scoped form (a digit-ending segment, no dash)
BAD_DOTS = [
    pytest.param("H-1.", id="H-1."),
    pytest.param("IDEA-R.", id="IDEA-R."),
    pytest.param("EXP.", id="EXP."),
    pytest.param("H.", id="H."),
]
# prefix -> (id, number) on #576's repo (EXP-1..EXP-5, H1.1)
GOOD_DOTS = {
    "H130.": ("H130.1", 1),
    "P2.": ("P2.1", 1),
}


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def test_fixture_strings_are_what_they_claim() -> None:
    """Guard the fixture itself: the decomposed form is really decomposed, and NFC
    folds it to the precomposed one; Devanagari holds combining marks and is NFC."""
    assert DECOMPOSED != PRECOMPOSED and _nfc(DECOMPOSED) == PRECOMPOSED
    assert _nfc(DEVANAGARI) == DEVANAGARI
    cats = {unicodedata.category(c) for c in DEVANAGARI}
    assert {"Mc", "Mn"} <= cats, cats


# ---------------------------------------------------------------------------
# 1. NFC normalization + combining marks — RED before the fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("commit_allocation", [True, False], ids=["commit", "no-commit"])
def test_service_decomposed_prefix_allocates_as_nfc(repo: Repo, commit_allocation) -> None:
    result = repo.service().allocate_next_id(
        DECOMPOSED, sync_remote=False, commit_allocation=commit_allocation
    )
    assert result["success"], result
    assert result["id"] == f"{PRECOMPOSED}-001", ascii(result)
    assert result["prefix"] == PRECOMPOSED, ascii(result)
    assert result["number"] == 1, result


def test_service_decomposed_and_precomposed_share_one_id_space(repo: Repo) -> None:
    """Either spelling, one space: allocating one then the other counts on."""
    svc = repo.service()
    first = svc.allocate_next_id(PRECOMPOSED, sync_remote=False, commit_allocation=True)
    assert first["success"] and first["id"] == f"{PRECOMPOSED}-001", ascii(first)
    second = svc.allocate_next_id(DECOMPOSED, sync_remote=False, commit_allocation=True)
    assert second["success"], ascii(second)
    assert second["id"] == f"{PRECOMPOSED}-002", ascii(second)
    third = svc.allocate_next_id(PRECOMPOSED, sync_remote=False, commit_allocation=True)
    assert third["id"] == f"{PRECOMPOSED}-003", ascii(third)
    # the record holds only the NFC spelling
    record = (repo.root / ALLOCATIONS).read_text(encoding="utf-8")
    assert DECOMPOSED not in record and "E\\u0301" not in record, ascii(record)


def test_service_combining_marks_inside_a_segment_allocate(repo: Repo) -> None:
    result = repo.service().allocate_next_id(
        DEVANAGARI, sync_remote=False, commit_allocation=True
    )
    assert result["success"], ascii(result)
    assert result["id"] == f"{DEVANAGARI}-001", ascii(result)


@pytest.mark.parametrize("prefix", [DECOMPOSED, DEVANAGARI], ids=["decomposed", "devanagari"])
def test_cli_next_id_accepts_nfc_and_combining_marks(repo: Repo, prefix: str) -> None:
    result = CliRunner().invoke(main, ["next-id", prefix, "--no-sync"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    want = f"{_nfc(prefix).upper()}-001"
    assert want in result.output, ascii(result.output)
    if prefix == DECOMPOSED:
        assert "É" not in result.output, ascii(result.output)


def test_cli_next_id_decomposed_after_precomposed_counts_on(repo: Repo) -> None:
    first = CliRunner().invoke(main, ["next-id", PRECOMPOSED, "--no-sync"])
    assert first.exit_code == 0 and f"{PRECOMPOSED}-001" in first.output, ascii(first.output)
    second = CliRunner().invoke(main, ["next-id", DECOMPOSED, "--no-sync"])
    assert second.exit_code == 0, (ascii(second.output), repr(second.exception))
    assert f"{PRECOMPOSED}-002" in second.output, ascii(second.output)


@pytest.mark.parametrize("item_type", ["bug", "feature"])
def test_theme_decomposed_prefix_loads_as_nfc_without_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, item_type: str
) -> None:
    repo_root = _repo(tmp_path, SINGLE_CFG, _theme(item_type, DECOMPOSED))
    monkeypatch.chdir(repo_root)
    theme = config_mod._load_builtin_theme(THEME, repo_root)
    assert theme is not None
    kept = theme["item_types"][item_type].get("id_prefix")
    assert kept == PRECOMPOSED, f"id_prefix after load: {ascii(kept)}"
    assert not _warnings(warnings_log), ascii(_warnings(warnings_log))

    new = _create(tmp_path, monkeypatch, repo_root, item_type)
    assert len(new) == 1, new
    assert new[0].resolve().parent == (repo_root / TYPE_PATHS[item_type]).resolve(), new
    name = _nfc(new[0].name)  # the filesystem may hand back either spelling
    assert name.upper().startswith(f"{PRECOMPOSED}-"), ascii(new[0].name)
    body = new[0].read_text(encoding="utf-8")
    assert DECOMPOSED not in body, "the item was written with the decomposed prefix"


def test_theme_combining_marks_prefix_loads_without_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log
) -> None:
    repo_root = _repo(tmp_path, SINGLE_CFG, _theme("bug", DEVANAGARI))
    monkeypatch.chdir(repo_root)
    theme = config_mod._load_builtin_theme(THEME, repo_root)
    assert theme is not None
    assert theme["item_types"]["bug"].get("id_prefix") == DEVANAGARI
    assert not _warnings(warnings_log), ascii(_warnings(warnings_log))


# ---------------------------------------------------------------------------
# 2. the trailing dot is paper-scoped only — RED before the fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("commit_allocation", [True, False], ids=["commit", "no-commit"])
@pytest.mark.parametrize("prefix", BAD_DOTS)
def test_service_refuses_trailing_dot_outside_paper_form(
    repo: Repo, prefix: str, commit_allocation
) -> None:
    state = _State(repo)
    with pytest.raises(InputRefused) as info:
        repo.service().allocate_next_id(
            prefix, sync_remote=False, commit_allocation=commit_allocation
        )
    _names_prefix_grammar(str(info.value))
    state.assert_untouched(f"allocate_next_id({prefix!r})")


@pytest.mark.parametrize("flags", [["--no-sync"], ["--no-commit"]], ids=["no-sync", "no-commit"])
@pytest.mark.parametrize("prefix", BAD_DOTS)
def test_cli_next_id_refuses_trailing_dot_outside_paper_form(
    repo: Repo, prefix: str, flags
) -> None:
    out = _cli_refused(repo, ["next-id", *flags, "--", prefix])
    _names_prefix_grammar(out)


@pytest.mark.parametrize("item_type", ["bug", "feature"])
@pytest.mark.parametrize("bad", BAD_DOTS)
def test_theme_drops_trailing_dot_outside_paper_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, bad: str, item_type: str
) -> None:
    repo_root = _repo(tmp_path, SINGLE_CFG, _theme(item_type, bad))
    monkeypatch.chdir(repo_root)
    theme = config_mod._load_builtin_theme(THEME, repo_root)
    assert theme is not None
    type_def = theme["item_types"][item_type]
    assert "id_prefix" not in type_def, f"kept {type_def['id_prefix']!r}"
    hits = _prefix_warnings(warnings_log, bad)
    assert len(hits) == 1, _warnings(warnings_log)


# --- controls: the paper-scoped dot, and a leading mark (GREEN) ---------------------


@pytest.mark.parametrize("prefix", list(GOOD_DOTS))
def test_control_service_paper_scoped_dot_allocates(repo: Repo, prefix: str) -> None:
    result = repo.service().allocate_next_id(prefix, sync_remote=False, commit_allocation=True)
    assert result["success"], result
    assert (result["id"], result["number"]) == GOOD_DOTS[prefix], result


@pytest.mark.parametrize("prefix", list(GOOD_DOTS))
def test_control_cli_paper_scoped_dot_allocates(repo: Repo, prefix: str) -> None:
    result = CliRunner().invoke(main, ["next-id", prefix, "--no-sync"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert GOOD_DOTS[prefix][0] in result.output, result.output


@pytest.mark.parametrize("prefix", list(GOOD_DOTS))
def test_control_theme_paper_scoped_dot_loads_without_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, prefix: str
) -> None:
    repo_root = _repo(tmp_path, SINGLE_CFG, _theme("bug", prefix))
    monkeypatch.chdir(repo_root)
    theme = config_mod._load_builtin_theme(THEME, repo_root)
    assert theme is not None and theme["item_types"]["bug"].get("id_prefix") == prefix
    assert not _warnings(warnings_log), _warnings(warnings_log)


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("́EXP", id="leading-mark"),
        pytest.param("EXP-́R", id="mark-starts-segment"),
    ],
)
def test_control_mark_cannot_start_a_segment(repo: Repo, prefix: str) -> None:
    """A combining mark is allowed INSIDE a segment, never as the prefix's first
    character (a prefix starts with a letter). Also: a mark after a dash has no
    base, so it stays refused."""
    state = _State(repo)
    with pytest.raises(InputRefused):
        repo.service().allocate_next_id(prefix, sync_remote=False, commit_allocation=True)
    state.assert_untouched(ascii(prefix))


# ---------------------------------------------------------------------------
# 3. the messages name the optional trailing '.'
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [pytest.param("a b", id="space"), *BAD_DOTS])
def test_refusal_mentions_trailing_dot(repo: Repo, prefix: str) -> None:
    with pytest.raises(InputRefused) as info:
        repo.service().allocate_next_id(prefix, sync_remote=False, commit_allocation=False)
    assert MESSAGE_DOT in str(info.value), str(info.value)
    out = _cli_refused(repo, ["next-id", "--no-sync", "--", prefix])
    assert MESSAGE_DOT in out, out


@pytest.mark.parametrize("bad", [pytest.param("a b", id="space"), *BAD_DOTS])
def test_theme_warning_mentions_trailing_dot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, bad: str
) -> None:
    repo_root = _repo(tmp_path, SINGLE_CFG, _theme("bug", bad))
    monkeypatch.chdir(repo_root)
    config_mod._load_builtin_theme(THEME, repo_root)
    hits = _prefix_warnings(warnings_log, bad)
    assert len(hits) == 1, _warnings(warnings_log)
    assert MESSAGE_DOT in hits[0], hits[0]
