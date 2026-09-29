# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""#1136: `--rm-dep` reads `EXP-3` as `EXP-003` (#641), as `--add-dep` does (#1125).

[steer] on #1136: `--rm-dep` resolves each target against the dependency list by
`_dup_key`, an exact match first, and is applied after `--add-dep`'s
canonicalisation. The self-dependency wording is pinned.

On a board holding `EXP-009` (and no `EXP-9`):

- EXP-5 depends on `EXP-009`: `--rm-dep EXP-9` / `exp-9` / `EXP-09` removes it;
- `--add-dep EXP-9 --rm-dep EXP-009` in one call adds nothing: the add is written
  `EXP-009`, which the rm names (and the reverse spellings, and with `EXP-009` held);
- `update EXP-009 --add-dep EXP-9` is refused as "EXP-009 can't depend on itself",
  not as the cycle it also is.

Control: `EXP9` is not `EXP-9` (#661): `--rm-dep EXP9` removes nothing, as today.

Fixture: #576's two-board repo (EXP-1..EXP-5, H1.1), plus `EXP-009`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _flat,
    _ok,
    _refused,
    repo,
)
from tests.issues.test_1125_id_key_lookup import nine  # noqa: F401


@pytest.fixture
def held(repo: Repo, nine: Path) -> Path:
    """EXP-5 depends on EXP-2 and EXP-009; committed."""
    repo.write("EXP-5", ["EXP-2", "EXP-009"])
    repo.commit("EXP-5 -> EXP-009")
    return repo.path("EXP-5")


@pytest.mark.parametrize("spelling", ["EXP-9", "exp-9", "EXP-09", "EXP-009"])
def test_rm_dep_other_spelling_removes_stored(repo: Repo, held: Path, spelling: str) -> None:
    _ok(["update", "EXP-5", "--rm-dep", spelling])
    assert repo.deps("EXP-5") == ["EXP-2"]


@pytest.mark.parametrize(
    ("add", "rm"),
    [
        pytest.param("EXP-9", "EXP-009", id="add-unpadded-rm-padded"),
        pytest.param("EXP-009", "EXP-9", id="add-padded-rm-unpadded"),
        pytest.param("exp-09", "EXP-9", id="add-third-rm-unpadded"),
    ],
)
def test_add_and_rm_same_id_in_one_call_adds_nothing(
    repo: Repo, nine: Path, add: str, rm: str
) -> None:
    result = _ok(["update", "EXP-5", "--add-dep", add, "--rm-dep", rm])
    assert "no changes" in _flat(result.output).lower(), result.output
    assert repo.deps("EXP-5") == ["EXP-2"]


@pytest.mark.parametrize(
    ("add", "rm"),
    [
        pytest.param("EXP-9", "EXP-009", id="add-unpadded-rm-padded"),
        pytest.param("EXP-009", "EXP-9", id="add-padded-rm-unpadded"),
    ],
)
def test_add_and_rm_same_id_when_held_removes_it(
    repo: Repo, held: Path, add: str, rm: str
) -> None:
    """With `EXP-009` already held, the rm wins, as `--add-dep X --rm-dep X` does."""
    _ok(["update", "EXP-5", "--add-dep", add, "--rm-dep", rm])
    assert repo.deps("EXP-5") == ["EXP-2"]


def test_self_dependency_through_other_spelling_is_refused_as_self(
    repo: Repo, nine: Path
) -> None:
    """Pins the `_dup_key` self-check: `target == me` would fall through to the
    cycle check and name a cycle instead (#1136 review)."""
    out = _refused(
        repo, ["update", "EXP-009", "--add-dep", "EXP-9"], "EXP-009 can't depend on itself"
    )
    assert "cycle" not in out, out


def test_control_no_separator_removes_nothing(repo: Repo, held: Path) -> None:
    """`EXP9` is not `EXP-9` (#661): nothing to remove, so no change."""
    result = _ok(["update", "EXP-5", "--rm-dep", "EXP9"])
    assert "no changes" in _flat(result.output).lower(), result.output
    assert repo.deps("EXP-5") == ["EXP-2", "EXP-009"]


def test_rm_dep_exact_spelling_keeps_other_spelling(repo: Repo, nine: Path) -> None:
    """Stored `[EXP-9, EXP-009]`: `--rm-dep EXP-9` drops only the entry spelled
    exactly so; `EXP-009`, the same `_dup_key`, stays (the exact match wins; #1136 r1)."""
    repo.write("EXP-5", ["EXP-9", "EXP-009"])
    repo.commit("EXP-5 -> EXP-9, EXP-009")
    _ok(["update", "EXP-5", "--rm-dep", "EXP-9"])
    assert repo.deps("EXP-5") == ["EXP-009"]
