# ruff: noqa: F811  -- the `repo` / `warnings_log` fixtures imported from #576 / #816 are re-bound
"""#817, round 2: the findings of PR #845's review.

1. `allocate_next_id` NFC-normalizes and THEN upper-cases, and upper-casing can undo
   NFC. Six Greek letters do it: ΐ ΰ ῒ ῗ ῢ ῧ (U+0390, U+03B0, U+1FD2, U+1FD7, U+1FE2,
   U+1FE7). `'ΐ'.upper()` is `Ι + U+0308 + U+0301`, whose NFC form is `Ϊ + U+0301`
   (U+03AA U+0301). So `next-id ΐX` recorded a non-NFC `…X-001`, and `next-id` with
   the NFC upper-case spelling issued a second `…X-001`.
2. The paper-scoped dot took any digit (`str.isdigit`), so `H٣.` (Arabic-Indic),
   `H１３.` (fullwidth) and `H².` were accepted. `hypothesis create` reads the paper
   with `int(...)`, so `H٣.` names paper 3 while numbering apart from `H3.`.

Decided ([steer] round 2 on #817):
1. The allocator re-normalizes after upper-casing, NFC(upper(NFC(p))): the lowercase
   form and the NFC form of its upper case are ONE ID space, and the allocation
   record holds one NFC spelling.
2. A trailing `.` is allowed only after an ASCII digit: `H٣.`, `H１３.` and `H².` are
   refused (service, CLI `next-id`, theme loading); `H3.` and `H130.` still pass.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401
from tests.issues.test_802_prefix_grammar import (
    ALLOCATIONS,
    _cli_refused,
    _names_prefix_grammar,
    _State,
)
from tests.issues.test_816_theme_prefix_grammar import (  # noqa: F401  (fixtures)
    SINGLE_CFG,
    THEME,
    _clean_theme_cache,
    _prefix_warnings,
    _repo,
    _theme,
    _warnings,
    warnings_log,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused

# lowercase, NFC, and upper-casing leaves them non-NFC
GREEK = [
    pytest.param("ΐ", id="U+0390"),
    pytest.param("ΰ", id="U+03B0"),
    pytest.param("ῒ", id="U+1FD2"),
    pytest.param("ῗ", id="U+1FD7"),
    pytest.param("ῢ", id="U+1FE2"),
    pytest.param("ῧ", id="U+1FE7"),
]

NON_ASCII_DIGIT_DOTS = [
    pytest.param("H٣.", id="arabic-indic-3"),
    pytest.param("H１３.", id="fullwidth-13"),
    pytest.param("H².", id="superscript-2"),
]
# prefix -> (id, number) on #576's repo (EXP-1..EXP-5, H1.1)
ASCII_DIGIT_DOTS = {"H3.": ("H3.1", 1), "H130.": ("H130.1", 1)}


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _spellings(letter: str) -> tuple[str, str]:
    """(the lowercase prefix, the NFC form of its upper case)."""
    lower = f"{letter}X"
    return lower, _nfc(lower.upper())


def _strings(value: Any) -> list[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _assert_record_is_nfc(repo: Repo, letter: str) -> None:
    lower, upper = _spellings(letter)
    raw_upper = lower.upper()  # the non-NFC spelling the review found in the record
    assert raw_upper != upper  # the fixture is what it claims
    record = json.loads((repo.root / ALLOCATIONS).read_text(encoding="utf-8"))
    strings = _strings(record)
    not_nfc = [s for s in strings if _nfc(s) != s]
    assert not not_nfc, f"non-NFC strings in the allocation record: {ascii(not_nfc)}"
    assert any(upper in s for s in strings), ascii(record)


def test_fixture_letters_lose_nfc_when_upper_cased() -> None:
    for param in GREEK:
        letter = param.values[0]
        assert _nfc(letter) == letter, ascii(letter)
        assert _nfc(letter.upper()) != letter.upper(), ascii(letter)


# ---------------------------------------------------------------------------
# 1. NFC survives upper-casing — RED before the fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lower_first", [True, False], ids=["lower-first", "upper-first"])
@pytest.mark.parametrize("letter", GREEK)
def test_service_upper_casing_keeps_one_nfc_space(
    repo: Repo, letter: str, lower_first: bool
) -> None:
    lower, upper = _spellings(letter)
    order = [lower, upper] if lower_first else [upper, lower]
    svc = repo.service()
    got = []
    for n, prefix in enumerate(order, 1):
        result = svc.allocate_next_id(prefix, sync_remote=False, commit_allocation=True)
        assert result["success"], ascii(result)
        got.append(result["id"])
        assert result["id"] == f"{upper}-{n:03d}", ascii(result)
        assert result["prefix"] == upper, ascii(result)
    assert all(_nfc(i) == i for i in got), ascii(got)
    _assert_record_is_nfc(repo, letter)


@pytest.mark.parametrize("letter", GREEK)
def test_cli_next_id_upper_casing_keeps_one_nfc_space(repo: Repo, letter: str) -> None:
    lower, upper = _spellings(letter)
    raw_upper = lower.upper()
    for n, prefix in enumerate([lower, upper], 1):
        result = CliRunner().invoke(main, ["next-id", prefix, "--no-sync"])
        assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
        assert f"{upper}-{n:03d}" in result.output, ascii(result.output)
        assert raw_upper not in result.output, ascii(result.output)
    _assert_record_is_nfc(repo, letter)


# ---------------------------------------------------------------------------
# 2. the paper dot needs an ASCII digit — RED before the fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("commit_allocation", [True, False], ids=["commit", "no-commit"])
@pytest.mark.parametrize("prefix", NON_ASCII_DIGIT_DOTS)
def test_service_refuses_paper_dot_after_non_ascii_digit(
    repo: Repo, prefix: str, commit_allocation: bool
) -> None:
    state = _State(repo)
    with pytest.raises(InputRefused) as info:
        repo.service().allocate_next_id(
            prefix, sync_remote=False, commit_allocation=commit_allocation
        )
    _names_prefix_grammar(str(info.value))
    state.assert_untouched(ascii(prefix))


@pytest.mark.parametrize("flags", [["--no-sync"], ["--no-commit"]], ids=["no-sync", "no-commit"])
@pytest.mark.parametrize("prefix", NON_ASCII_DIGIT_DOTS)
def test_cli_next_id_refuses_paper_dot_after_non_ascii_digit(
    repo: Repo, prefix: str, flags: list[str]
) -> None:
    out = _cli_refused(repo, ["next-id", *flags, "--", prefix])
    _names_prefix_grammar(out)


@pytest.mark.parametrize("prefix", NON_ASCII_DIGIT_DOTS)
def test_theme_drops_paper_dot_after_non_ascii_digit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, prefix: str
) -> None:
    repo_root = _repo(tmp_path, SINGLE_CFG, _theme("bug", prefix))
    monkeypatch.chdir(repo_root)
    theme = config_mod._load_builtin_theme(THEME, repo_root)
    assert theme is not None
    type_def = theme["item_types"]["bug"]
    assert "id_prefix" not in type_def, f"kept {ascii(type_def['id_prefix'])}"
    hits = _prefix_warnings(warnings_log, prefix)
    assert len(hits) == 1, ascii(_warnings(warnings_log))


# --- controls (GREEN) --------------------------------------------------------------


@pytest.mark.parametrize("prefix", list(ASCII_DIGIT_DOTS))
def test_control_ascii_digit_paper_dot_allocates(repo: Repo, prefix: str) -> None:
    result = repo.service().allocate_next_id(prefix, sync_remote=False, commit_allocation=True)
    assert result["success"], result
    assert (result["id"], result["number"]) == ASCII_DIGIT_DOTS[prefix], result
    cli = CliRunner().invoke(main, ["next-id", prefix, "--no-sync"])
    assert cli.exit_code == 0, (cli.output, repr(cli.exception))
    assert f"{prefix}2" in cli.output, cli.output


@pytest.mark.parametrize("prefix", list(ASCII_DIGIT_DOTS))
def test_control_ascii_digit_paper_dot_theme_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, prefix: str
) -> None:
    repo_root = _repo(tmp_path, SINGLE_CFG, _theme("bug", prefix))
    monkeypatch.chdir(repo_root)
    theme = config_mod._load_builtin_theme(THEME, repo_root)
    assert theme is not None and theme["item_types"]["bug"].get("id_prefix") == prefix
    assert not _warnings(warnings_log), _warnings(warnings_log)
