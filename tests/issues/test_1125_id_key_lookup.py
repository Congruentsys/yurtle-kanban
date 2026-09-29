# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""#1125: `get_item` and the dependency "on no board" check look ids up by `_id_key`.

#641 ruled that `EXP-3` is `EXP-003`; #795 applied it to `duplicate_ids`. [steer] on
#1125: `get_item` and `_check_new_dependencies`'s "on no board" check look ids up the
same way, and an exact match wins. When both spellings exist the board already
reports a duplicate (#795) and the duplicate refusals apply.

On a board holding `EXP-009` (and no `EXP-9`):

- `show EXP-9` shows EXP-009; `get_item("EXP-9")` / `get_item("exp-9")` find it;
- `move EXP-9 …` moves EXP-009;
- `update EXP-5 --add-dep EXP-9` is accepted and writes the board's spelling,
  `EXP-009`, as `--add-dep exp-3` writes `EXP-3` (#576, #817): a stored id is the
  canonical one, so the dependency graph, cycles and `validate` see the edge;
- a cycle closed through the other spelling is refused.

Controls: `EXP9` is not `EXP-9` (#661), so it is still unknown; with both `EXP-3` and
`EXP-003` on the board, each exact spelling finds its own file, and a third spelling
(`EXP-03`) is refused as a duplicate, as a dependency target and as an update.

Fixture: #576's two-board repo (EXP-1..EXP-5, H1.1), plus `EXP-009`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _flat,
    _item_text,
    _ok,
    _refused,
    invoke,
    repo,
)
from tests.issues.test_795_duplicate_ids_id_key import NEEDLES, padded  # noqa: F401


def _write(repo: Repo, rel: str, item_id: str, deps: list[str] | None = None) -> Path:
    path = repo.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_item_text(item_id, deps or []), encoding="utf-8")
    return path


@pytest.fixture
def nine(repo: Repo) -> Path:
    """EXP-009, padded, with no EXP-9 on the board; committed."""
    path = _write(repo, "work/expeditions/EXP-009-padded.md", "EXP-009")
    repo.commit("EXP-009")
    return path


def _deps_of(path: Path) -> list[str]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])["depends_on"]


# --- get_item ----------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["EXP-9", "exp-9", "EXP-09", "EXP-009"])
def test_get_item_reads_unpadded_as_padded(repo: Repo, nine: Path, spelling: str) -> None:
    item = repo.service().get_item(spelling)
    assert item is not None and item.id == "EXP-009", item


def test_show_unpadded_shows_padded(repo: Repo, nine: Path) -> None:
    result = invoke(["show", "EXP-9"])
    assert result.exit_code == 0, result.output
    assert "EXP-009" in _flat(result.output), result.output


def test_move_unpadded_moves_padded(repo: Repo, nine: Path) -> None:
    result = invoke(["move", "EXP-9", "in_progress", "--no-commit"])
    assert result.exit_code == 0, result.output
    assert "status: underway" in nine.read_text(encoding="utf-8")  # nautical in_progress


# --- dependencies ------------------------------------------------------------------------


def test_add_dep_unpadded_is_accepted_as_padded(repo: Repo, nine: Path) -> None:
    _ok(["update", "EXP-5", "--add-dep", "EXP-9"])
    assert repo.deps("EXP-5") == ["EXP-2", "EXP-009"]


def test_add_dep_unpadded_when_padded_is_held_is_no_change(repo: Repo, nine: Path) -> None:
    _ok(["update", "EXP-5", "--add-dep", "EXP-009"])
    result = _ok(["update", "EXP-5", "--add-dep", "exp-9"])
    assert "no changes" in _flat(result.output).lower(), result.output
    assert repo.deps("EXP-5") == ["EXP-2", "EXP-009"]


def test_cycle_through_unpadded_spelling_is_refused(repo: Repo) -> None:
    """EXP-009 depends on EXP-5: `EXP-5 --add-dep EXP-9` closes a cycle."""
    _write(repo, "work/expeditions/EXP-009-padded.md", "EXP-009", ["EXP-5"])
    repo.commit("EXP-009 -> EXP-5")
    _refused(repo, ["update", "EXP-5", "--add-dep", "EXP-9"], "cycle")


# --- controls ----------------------------------------------------------------------------


def test_control_no_separator_is_still_unknown(repo: Repo, nine: Path) -> None:
    """`EXP9` is not `EXP-9` (#661)."""
    assert repo.service().get_item("EXP9") is None
    assert invoke(["show", "EXP9"]).exit_code != 0
    _refused(repo, ["update", "EXP-5", "--add-dep", "EXP9"], "EXP9", "is on no board")


def test_control_unknown_number_still_unknown(repo: Repo, nine: Path) -> None:
    assert repo.service().get_item("EXP-90") is None
    _refused(repo, ["update", "EXP-5", "--add-dep", "EXP-90"], "is on no board")


@pytest.mark.parametrize("spelling", ["EXP-3", "EXP-003"])
def test_control_exact_match_wins_on_a_duplicate(
    repo: Repo, padded: Path, spelling: str
) -> None:
    item = repo.service().get_item(spelling)
    assert item is not None and item.id == spelling, item


def test_control_third_spelling_of_duplicate_is_refused_as_update(
    repo: Repo, padded: Path
) -> None:
    _refused(repo, ["update", "EXP-03", "--priority", "high"], *NEEDLES)


def test_control_third_spelling_of_duplicate_is_refused_as_dependency(
    repo: Repo, padded: Path
) -> None:
    _refused(
        repo, ["update", "EXP-5", "--add-dep", "EXP-03"],
        "is on more than one board", "a dependency on it is ambiguous",
    )
