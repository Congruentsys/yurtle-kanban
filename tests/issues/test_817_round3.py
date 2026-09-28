# ruff: noqa: F811  -- the `repo` / `warnings_log` fixtures imported from #576 / #816 are re-bound
"""#817, round 3: one fold for every ID comparison (the PR #845 round-2 review).

Round 2 made the allocator key a prefix on NFC(upper(NFC(p))), but everything that
counts or finds existing ids still folds with a bare `.upper()`, which is not NFC
stable for six Greek letters, ΐ ΰ ῒ ῗ ῢ ῧ (U+0390, U+03B0, U+1FD2, U+1FD7, U+1FE2,
U+1FE7): `'ΐX-001'.upper()` is `Ι U+0308 U+0301 X-001`, while the allocator's head
is `U+03AA U+0301 X-`. The review reproduced it: with an item `id: ΐX-001` on the
board, `next-id ΐX` issued `Ϊ́X-001` again.

Decided ([steer] round 3 on #817): one fold, `fold_id(s) = NFC(upper(NFC(s)))`, used
by EVERY place that compares IDs or prefixes for a space (`_id_key`,
`_number_in_space`, `_stem_id`, `_stem_holds`, `_holder_at` / `_holders_at`, the
allocator, `_lookup`, `duplicate_ids`). Stems are matched on the folded stem. The
refusal message says "an ASCII digit".

Pinned here, for all six letters, and for any spelling of the id (lowercase, the NFC
upper case, the decomposed lowercase, the raw non-NFC upper case):
1. an existing `ΐX-001` (frontmatter `id:`, or a filename stem) makes `next-id ΐX`
   (service and CLI) issue `-002`;
2. a theme `id_prefix: ΐX`: `create` and `next-id` count in one space;
3. `get_item` / `show` find the NFC id by any spelling;
4. `_holder_at` / `_holders_at` at origin find `Ϊ́X-001` by any spelling;
5. the bad-trailing-dot refusal (and the theme warning) say "ASCII digit".

Controls: ASCII `e` (`EX`) behaves as before in every test above, and so does `é`
(`ÉX`) in every precomposed spelling. The decomposed `é` spelling (`E U+0301`) of an
EXISTING id is red too: a bare `.upper()` never composes it, so today it is neither
counted nor found. The review noted that gap as pre-existing; the steer's one fold
closes it.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_576_cli_update_deps import Repo, _git, repo  # noqa: F401
from tests.issues.test_802_prefix_grammar import _cli_refused
from tests.issues.test_816_theme_prefix_grammar import (  # noqa: F401  (fixtures)
    SINGLE_CFG,
    THEME,
    TYPE_PATHS,
    _clean_theme_cache,
    _create,
    _invoke,
    _no_crash,
    _prefix_warnings,
    _repo,
    _theme,
    _warnings,
    warnings_log,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused

GREEK = ["ΐ", "ΰ", "ῒ", "ῗ", "ῢ", "ῧ"]
LETTERS = [
    *(pytest.param(c, id=f"U+{ord(c):04X}") for c in GREEK),
    pytest.param("e", id="control-ascii"),
    pytest.param("é", id="e-acute"),
]
SPELLINGS = ["lower", "nfc-upper", "nfd-lower", "raw-upper"]
# the spellings an existing item may be written in (a raw upper is only a key)
WRITTEN = ["lower", "nfc-upper", "nfd-lower"]
EXPEDITIONS = Path("work") / "expeditions"


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def fold(s: str) -> str:
    """The one fold the steer names: NFC(upper(NFC(s)))."""
    return _nfc(_nfc(s).upper())


def spell(letter: str, how: str) -> str:
    """The prefix `<letter>X` spelled `how`."""
    base = f"{letter}X"
    return {
        "lower": base,
        "nfc-upper": fold(base),
        "nfd-lower": unicodedata.normalize("NFD", base),
        "raw-upper": base.upper(),  # upper-cased, not re-normalized
    }[how]


def _write_item(root: Path, item_id: str, *, as_stem: bool, name: str = "notes") -> Path:
    """An item holding `item_id`: in its frontmatter (a file whose name holds no id),
    or only in its filename stem (a file with no `id:`)."""
    folder = root / EXPEDITIONS
    folder.mkdir(parents=True, exist_ok=True)
    if as_stem:
        path = folder / f"{item_id}-{name}.md"
        path.write_text(f"# {name}\n\nNo frontmatter id.\n", encoding="utf-8")
    else:
        path = folder / f"{name}.md"
        path.write_text(
            f"---\nid: {item_id}\ntitle: \"{name}\"\ntype: expedition\nstatus: ready\n"
            f"priority: medium\ndepends_on: []\n---\n\n# {name}\n",
            encoding="utf-8",
        )
    return path


def test_fixture_fold_is_what_the_review_measured() -> None:
    for c in GREEK:
        base = f"{c}X"
        assert _nfc(base) == base
        assert base.upper() != fold(base), ascii(c)  # a bare upper is not the fold
        assert fold(fold(base)) == fold(base)
        assert fold(spell(c, "nfd-lower")) == fold(base)
        assert fold(spell(c, "raw-upper")) == fold(base)
    for c in ("e", "é"):
        assert f"{c}X".upper() == fold(f"{c}X")


# ---------------------------------------------------------------------------
# 1. an existing id is counted, whatever its spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asked", SPELLINGS)
@pytest.mark.parametrize("written", WRITTEN)
@pytest.mark.parametrize("source", ["frontmatter", "stem"])
@pytest.mark.parametrize("letter", LETTERS)
def test_service_next_id_counts_an_existing_item(
    repo: Repo, letter: str, source: str, written: str, asked: str
) -> None:
    _write_item(repo.root, f"{spell(letter, written)}-001", as_stem=source == "stem")
    repo.commit("an existing item")
    result = repo.service().allocate_next_id(
        spell(letter, asked), sync_remote=False, commit_allocation=False
    )
    assert result["success"], ascii(result)
    assert result["id"] == f"{fold(letter + 'X')}-002", ascii(result)


@pytest.mark.parametrize("written", WRITTEN)
@pytest.mark.parametrize("source", ["frontmatter", "stem"])
@pytest.mark.parametrize("letter", LETTERS)
def test_cli_next_id_counts_an_existing_item(
    repo: Repo, letter: str, source: str, written: str
) -> None:
    _write_item(repo.root, f"{spell(letter, written)}-001", as_stem=source == "stem")
    repo.commit("an existing item")
    result = CliRunner().invoke(main, ["next-id", spell(letter, "lower"), "--no-sync"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    want = f"{fold(letter + 'X')}-002"
    assert want in result.output, ascii(result.output)
    assert f"{fold(letter + 'X')}-001" not in result.output, ascii(result.output)


# ---------------------------------------------------------------------------
# 2. a theme prefix: `create` and `next-id` count in one space
# ---------------------------------------------------------------------------


def _created_number(path: Path, prefix: str) -> int | None:
    """The number of the id `create` wrote (frontmatter `id:`), in `prefix`'s folded
    space, else None."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^id:\s*(\S+)\s*$", text, re.M)
    assert match, text[:300]
    got = fold(match.group(1))
    head = fold(prefix) + "-"
    return int(got[len(head):]) if got.startswith(head) else None


@pytest.mark.parametrize("letter", LETTERS)
def test_theme_create_then_next_id_one_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, letter: str
) -> None:
    prefix = spell(letter, "lower")
    root = _repo(tmp_path, SINGLE_CFG, _theme("bug", prefix))
    new = _create(tmp_path, monkeypatch, root, "bug")
    assert len(new) == 1, new
    assert _created_number(new[0], prefix) == 1, ascii(new[0].read_text(encoding="utf-8"))
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "created")

    result = _invoke(root, monkeypatch, ["next-id", prefix, "--no-sync"])
    _no_crash(result)
    assert result.exit_code == 0, ascii(result.output)
    assert f"{fold(prefix)}-002" in result.output, ascii(result.output)


@pytest.mark.parametrize("letter", LETTERS)
def test_theme_next_id_then_create_one_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, letter: str
) -> None:
    prefix = spell(letter, "lower")
    root = _repo(tmp_path, SINGLE_CFG, _theme("bug", prefix))
    result = _invoke(root, monkeypatch, ["next-id", prefix, "--no-sync"])
    _no_crash(result)
    assert result.exit_code == 0 and f"{fold(prefix)}-001" in result.output, ascii(
        result.output
    )
    new = _create(tmp_path, monkeypatch, root, "bug")
    assert len(new) == 1, new
    assert _created_number(new[0], prefix) == 2, ascii(new[0].read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 3. lookup finds the NFC id by any spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asked", SPELLINGS)
@pytest.mark.parametrize("letter", LETTERS)
def test_get_item_finds_the_nfc_id_by_any_spelling(
    repo: Repo, letter: str, asked: str
) -> None:
    held = f"{fold(letter + 'X')}-001"
    _write_item(repo.root, held, as_stem=False)
    repo.commit("the allocated id")
    item = repo.service().get_item(f"{spell(letter, asked)}-001")
    assert item is not None, f"{ascii(spell(letter, asked))}-001 did not find {ascii(held)}"
    assert item.id == held, ascii(item.id)


@pytest.mark.parametrize("asked", ["lower", "nfd-lower"])
@pytest.mark.parametrize("letter", LETTERS)
def test_cli_show_finds_the_nfc_id_by_any_spelling(
    repo: Repo, letter: str, asked: str
) -> None:
    held = f"{fold(letter + 'X')}-001"
    _write_item(repo.root, held, as_stem=False, name="findme")
    repo.commit("the allocated id")
    result = CliRunner().invoke(main, ["show", f"{spell(letter, asked)}-001"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    assert "findme" in result.output, ascii(result.output)


# ---------------------------------------------------------------------------
# 4. holders at origin, by any spelling
# ---------------------------------------------------------------------------


def _push_to_origin(repo: Repo, tmp_path: Path) -> str:
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    _git(repo.root, "remote", "add", "origin", str(bare))
    _git(repo.root, "push", "-q", "origin", "main")
    _git(repo.root, "fetch", "-q", "origin")
    return "refs/remotes/origin/main"


@pytest.mark.parametrize("asked", SPELLINGS)
@pytest.mark.parametrize("letter", LETTERS)
def test_holders_at_origin_by_any_spelling(
    repo: Repo, tmp_path: Path, letter: str, asked: str
) -> None:
    held = f"{fold(letter + 'X')}-001"
    path = _write_item(repo.root, held, as_stem=False)
    repo.commit("the allocated id")
    rev = _push_to_origin(repo, tmp_path)
    rel = path.relative_to(repo.root).as_posix()
    svc = repo.service()
    wanted = f"{spell(letter, asked)}-001"
    assert svc._holders_at(rev, wanted) == [rel], ascii(wanted)
    assert svc._holder_at(rev, wanted) == rel, ascii(wanted)


@pytest.mark.parametrize("asked", SPELLINGS)
@pytest.mark.parametrize("letter", LETTERS)
def test_holder_at_origin_finds_a_filename_by_any_spelling(
    repo: Repo, tmp_path: Path, letter: str, asked: str
) -> None:
    """A file with no `id:` of its own holds the id its stem starts with (#788)."""
    held = f"{fold(letter + 'X')}-001"
    path = _write_item(repo.root, held, as_stem=True)
    repo.commit("the allocated id, as a filename")
    rev = _push_to_origin(repo, tmp_path)
    rel = _git(repo.root, "ls-tree", "-r", "--name-only", "-z", rev, "--",
               EXPEDITIONS.as_posix()).split("\0")
    rel = [r for r in rel if r.endswith("-notes.md")]
    assert len(rel) == 1, ascii(rel)
    assert fold(Path(rel[0]).name) == fold(path.name)
    wanted = f"{spell(letter, asked)}-001"
    assert repo.service()._holder_at(rev, wanted) == rel[0], ascii(wanted)


# ---------------------------------------------------------------------------
# 5. the bad-dot refusal names an ASCII digit
# ---------------------------------------------------------------------------

BAD_DOTS = ["H٣.", "H１３.", "H².", "H-1.", "EXP."]


@pytest.mark.parametrize("prefix", BAD_DOTS)
def test_refusal_says_ascii_digit(repo: Repo, prefix: str) -> None:
    with pytest.raises(InputRefused) as info:
        repo.service().allocate_next_id(prefix, sync_remote=False, commit_allocation=False)
    assert "ASCII digit" in str(info.value), str(info.value)
    out = _cli_refused(repo, ["next-id", "--no-sync", "--", prefix])
    assert "ASCII digit" in out, out


def test_theme_warning_says_ascii_digit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log
) -> None:
    bad = "H٣."
    root = _repo(tmp_path, SINGLE_CFG, _theme("bug", bad))
    monkeypatch.chdir(root)
    config_mod._load_builtin_theme(THEME, root)
    hits = _prefix_warnings(warnings_log, bad)
    assert len(hits) == 1, ascii(_warnings(warnings_log))
    assert "ASCII digit" in hits[0], hits[0]
