"""Issue #587 — `move` accepts other themes' status names, not only the item's theme.

When a status name is not canonical, `move` falls back to
`service._get_column_status_map()`: a hardcoded union of the nautical, spec and hdd
aliases plus every board's `status_mappings`. It isn't the item's theme, so on a
single nautical board `move EXP-001 active` (an hdd name) goes to in_progress and
`move EXP-001 accepted` (a spec name) goes to done.

Decided ([steer] on #587, bucket 2): a status name resolves through the **item's**
theme only — its native names plus the six canonical names. Anything else is
refused (non-zero exit, nothing written) and the message lists the item theme's
legal names. No users, so nothing to stay compatible with.

Name tables used here (what `move` accepts today for each theme's own items):
- canonical: the six `WorkItemStatus` values, on every theme;
- hdd: its `status_mappings` (pinned against themes/hdd.yaml below);
- nautical: harbor/provisioning/underway/approaching/arrived. nautical.yaml has no
  `status_mappings`; these names come only from the hardcoded alias table, and
  tests/issues/test_439 already relies on `move <exp> underway`. They stay legal
  for nautical items (and only for them);
- software: canonical only;
- spec: draft/proposed/implementing/accepted (themes/spec.yaml `status_aliases`).
  spec items are NOT a subject here — its native names don't work at all until
  #588 renames the key — but spec's names are still foreign names that the other
  themes must refuse (the issue's own `accepted` example).

Every move uses `--force --skip-gates --no-commit`: `--force` skips WIP and
workflow/transition validation, so the tests isolate *name resolution* from
legality (hdd's `transitions` would otherwise refuse e.g. draft -> complete, and
a refusal would be indistinguishable from a name refusal). That makes refusals
stronger, not weaker: `--force` must not bypass name resolution either.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban.cli import main

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"

CANONICAL: dict[str, str] = {
    s: s for s in ("backlog", "ready", "in_progress", "review", "done", "blocked")
}

# native name -> canonical, per theme (see module docstring)
NATIVE: dict[str, dict[str, str]] = {
    "software": {},
    "nautical": {
        "harbor": "backlog",
        "provisioning": "ready",
        "underway": "in_progress",
        "approaching": "review",
        "arrived": "done",
    },
    "hdd": {
        "draft": "backlog",
        "active": "in_progress",
        "complete": "done",
        "abandoned": "blocked",
    },
    # names only (spec items are not subjects until #588)
    "spec": {
        "draft": "backlog",
        "proposed": "ready",
        "implementing": "in_progress",
        "accepted": "done",
    },
}

# themes whose items the tests move (spec excluded until #588)
SUBJECT_THEMES = ("software", "nautical", "hdd")

# one type `create` accepts on each theme, landing on that theme's board
ITEM_TYPE: dict[str, str] = {"software": "feature", "nautical": "expedition", "hdd": "idea"}

# names distinctive enough that they can't appear in a refusal message as prose
DISTINCTIVE = {
    "harbor", "provisioning", "underway", "approaching", "arrived",
    "proposed", "implementing", "abandoned",
}


def _legal(theme: str) -> dict[str, str]:
    """Every name `move` must accept for an item of `theme` -> its canonical status."""
    return {**CANONICAL, **NATIVE[theme]}


def _foreign(theme: str) -> list[str]:
    """Other themes' native names that are neither native nor canonical in `theme`."""
    legal = _legal(theme)
    return sorted(
        {n for other, names in NATIVE.items() if other != theme for n in names} - set(legal)
    )


# ---------------------------------------------------------------------------
# Fixtures / helpers (after tests/issues/test_439_list_theme_status.py)
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(args, cwd=path, capture_output=True, check=True)


def _clear_theme_cache() -> None:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git_init(tmp_path)
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def buf(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Route cli's console to a wide, colourless buffer so nothing wraps."""
    out = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=out, width=300, color_system=None, force_terminal=False)
    )
    return out


def _run(buf: io.StringIO, args: list[str]) -> tuple[Result, str]:
    buf.seek(0)
    buf.truncate()
    result = CliRunner().invoke(main, args)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    return result, buf.getvalue() + result.output


def _ok(buf: io.StringIO, args: list[str]) -> str:
    result, text = _run(buf, args)
    assert result.exit_code == 0, text
    return text


def _create(buf: io.StringIO, theme: str) -> str:
    text = _ok(buf, ["create", ITEM_TYPE[theme], f"a {theme} item"])
    match = re.search(r"Created (\S+):", text)
    assert match, text
    return match.group(1)


def _item_file(repo: Path, item_id: str) -> Path:
    files = [p for p in repo.rglob(f"{item_id}-*.md") if ".git" not in p.parts]
    assert len(files) == 1, files
    return files[0]


def _move(buf: io.StringIO, item_id: str, name: str) -> tuple[Result, str]:
    return _run(buf, ["move", item_id, name, "--force", "--skip-gates", "--no-commit"])


def _status(buf: io.StringIO, item_id: str) -> str:
    return str(json.loads(_ok(buf, ["show", item_id, "--json"]))["status"])


def _single(buf: io.StringIO, theme: str) -> str:
    _ok(buf, ["init", "--theme", theme])
    return _create(buf, theme)


def _multiboard(buf: io.StringIO) -> dict[str, str]:
    """#98's layout: nautical default board + hdd research board; one item on each."""
    _ok(buf, ["init", "--theme", "nautical"])
    _ok(buf, ["board-add", "research", "--preset", "hdd", "--path", "research/"])
    _clear_theme_cache()
    return {"nautical": _create(buf, "nautical"), "hdd": _create(buf, "hdd")}


def _assert_accepted(buf: io.StringIO, item_id: str, name: str, canonical: str) -> None:
    result, text = _move(buf, item_id, name)
    assert result.exit_code == 0, f"`move {item_id} {name!r}` refused:\n{text}"
    assert _status(buf, item_id) == canonical


def _assert_refused(
    repo: Path, buf: io.StringIO, item_id: str, name: str, theme: str
) -> None:
    path = _item_file(repo, item_id)
    before = path.read_bytes()
    result, text = _move(buf, item_id, name)
    assert result.exit_code != 0, (
        f"`move {item_id} {name!r}` ({theme} item) was accepted:\n{text}"
    )
    assert path.read_bytes() == before, "a refused move changed the item file"
    # the message lists the item theme's legal names...
    words = set(re.findall(r"[a-z_]+", text.lower()))
    missing = sorted(set(_legal(theme)) - words)
    assert not missing, f"refusal doesn't list {theme}'s legal names {missing}:\n{text}"
    # ...and not other themes' (the old global union)
    leaked = sorted((DISTINCTIVE - set(_legal(theme)) - {name.strip().lower()}) & words)
    assert not leaked, f"refusal lists other themes' names {leaked}:\n{text}"


# ---------------------------------------------------------------------------
# 0. The tables the tests derive from, pinned against the theme files
# ---------------------------------------------------------------------------


def test_name_tables_match_theme_files() -> None:
    hdd = yaml.safe_load((THEMES_DIR / "hdd.yaml").read_text())
    assert hdd["status_mappings"] == NATIVE["hdd"]
    spec = yaml.safe_load((THEMES_DIR / "spec.yaml").read_text())
    assert spec["status_mappings"] == NATIVE["spec"]
    for theme in ("software", "nautical"):
        data = yaml.safe_load((THEMES_DIR / f"{theme}.yaml").read_text())
        assert not data.get("status_mappings"), theme
    # every subject theme has foreign names to refuse
    assert all(_foreign(t) for t in SUBJECT_THEMES)


# ---------------------------------------------------------------------------
# 1. Another theme's name is refused for an item of T (single board)
# ---------------------------------------------------------------------------

REFUSE_CASES = [(t, n) for t in SUBJECT_THEMES for n in _foreign(t)]


@pytest.mark.parametrize(("theme", "name"), REFUSE_CASES, ids=[f"{t}-{n}" for t, n in REFUSE_CASES])
def test_single_board_other_theme_name_refused(
    repo: Path, buf: io.StringIO, theme: str, name: str
) -> None:
    item_id = _single(buf, theme)
    _assert_refused(repo, buf, item_id, name, theme)


# ---------------------------------------------------------------------------
# 2. Every native and canonical name of T is accepted (single board)
# ---------------------------------------------------------------------------

ACCEPT_CASES = [(t, n, c) for t in SUBJECT_THEMES for n, c in sorted(_legal(t).items())]


@pytest.mark.parametrize(
    ("theme", "name", "canonical"), ACCEPT_CASES, ids=[f"{t}-{n}" for t, n, _ in ACCEPT_CASES]
)
def test_single_board_own_name_accepted(
    repo: Path, buf: io.StringIO, theme: str, name: str, canonical: str
) -> None:
    item_id = _single(buf, theme)
    _assert_accepted(buf, item_id, name, canonical)


# ---------------------------------------------------------------------------
# 3. Multi-board: a name resolves through the item's own board's theme
# ---------------------------------------------------------------------------

MULTI_CASES = [
    (owner, n)
    for owner in ("nautical", "hdd")
    for n in sorted(set(NATIVE[owner]) - set(CANONICAL))
] + [("spec", n) for n in sorted(set(NATIVE["spec"]) - set(NATIVE["hdd"]))]


@pytest.mark.parametrize(("owner", "name"), MULTI_CASES, ids=[f"{o}-{n}" for o, n in MULTI_CASES])
def test_multiboard_name_resolves_per_item_board(
    repo: Path, buf: io.StringIO, owner: str, name: str
) -> None:
    """An hdd name moves the hdd item and is refused for the nautical item, and vice
    versa; a spec-only name (no spec board here) is refused for both."""
    items = _multiboard(buf)
    for theme, item_id in items.items():
        if theme == owner:
            _assert_accepted(buf, item_id, name, NATIVE[owner][name])
        else:
            _assert_refused(repo, buf, item_id, name, theme)


@pytest.mark.parametrize("name", sorted(CANONICAL))
def test_multiboard_canonical_names_accepted_on_both_boards(
    repo: Path, buf: io.StringIO, name: str
) -> None:
    for item_id in _multiboard(buf).values():
        _assert_accepted(buf, item_id, name, name)


# ---------------------------------------------------------------------------
# 4. Case and separators, as today: case-insensitive; `-` and inner spaces fold
#    to `_`; surrounding whitespace is not stripped (refused)
# ---------------------------------------------------------------------------

FOLD_ACCEPT = [
    ("software", "In-Progress", "in_progress"),
    ("software", "IN PROGRESS", "in_progress"),
    ("software", "in progress", "in_progress"),
    ("software", "Review", "review"),
    ("nautical", "Underway", "in_progress"),
    ("nautical", "APPROACHING", "review"),
    ("nautical", "in-progress", "in_progress"),
    ("hdd", "Active", "in_progress"),
    ("hdd", "ABANDONED", "blocked"),
    ("hdd", "Done", "done"),
]


@pytest.mark.parametrize(
    ("theme", "name", "canonical"), FOLD_ACCEPT, ids=[f"{t}-{n}" for t, n, _ in FOLD_ACCEPT]
)
def test_case_and_separator_folding_accepted(
    repo: Path, buf: io.StringIO, theme: str, name: str, canonical: str
) -> None:
    item_id = _single(buf, theme)
    _assert_accepted(buf, item_id, name, canonical)


FOLD_REFUSE = [
    # another theme's name in another case is still another theme's name
    ("software", "Harbor"),
    ("nautical", "ACTIVE"),
    ("nautical", "Accepted"),
    ("hdd", "Underway"),
    ("hdd", "PROPOSED"),
    # surrounding whitespace is not stripped today
    ("software", " ready"),
    ("software", "ready "),
    ("hdd", " active"),
]


@pytest.mark.parametrize(
    ("theme", "name"), FOLD_REFUSE, ids=[f"{t}-{n!r}" for t, n in FOLD_REFUSE]
)
def test_case_and_whitespace_refused(
    repo: Path, buf: io.StringIO, theme: str, name: str
) -> None:
    item_id = _single(buf, theme)
    _assert_refused(repo, buf, item_id, name, theme)


# ---------------------------------------------------------------------------
# 5. Round 2 (review of PR #600): a custom theme whose `status_mappings` keys
#    contain `-`, a space and capitals. The input is folded (lower-case,
#    `-`/space -> `_`), so the keys must be folded the same way, and every name
#    the refusal lists must be one `move` accepts.
# ---------------------------------------------------------------------------

CUSTOM_MAPPINGS: dict[str, str] = {
    "on-hold": "blocked",
    "In Review": "review",
    "doing": "in_progress",
}

# typed name -> (native name written to the file, canonical status)
CUSTOM_ACCEPT: dict[str, tuple[str, str]] = {
    "on-hold": ("on-hold", "blocked"),
    "On_Hold": ("on-hold", "blocked"),
    "In Review": ("In Review", "review"),
    "in_review": ("In Review", "review"),
    "doing": ("doing", "in_progress"),
}


def _fold(name: str) -> str:
    return name.lower().replace("-", "_").replace(" ", "_")


def _write_custom_theme(repo: Path) -> None:
    """`.kanban/themes/custom.yaml`: the software theme plus CUSTOM_MAPPINGS."""
    theme = yaml.safe_load((THEMES_DIR / "software.yaml").read_text())
    theme["theme"]["name"] = "custom"
    theme["status_mappings"] = dict(CUSTOM_MAPPINGS)
    themes = repo / ".kanban" / "themes"
    themes.mkdir(parents=True, exist_ok=True)
    (themes / "custom.yaml").write_text(yaml.safe_dump(theme, sort_keys=False))
    _clear_theme_cache()


def _custom_item(repo: Path, buf: io.StringIO, layout: str) -> str:
    """A `feature` on a custom-theme board: the only board (single), or a `work`
    board beside the nautical default (multi)."""
    _write_custom_theme(repo)
    if layout == "single":
        _ok(buf, ["init", "--theme", "custom"])
    else:
        _ok(buf, ["init", "--theme", "nautical"])
        _ok(buf, ["board-add", "work", "--preset", "custom", "--path", "work/"])
        _clear_theme_cache()
    return _create(buf, "software")


def _file_status(repo: Path, item_id: str) -> str:
    front = _item_file(repo, item_id).read_text().split("---", 2)[1]
    return str(yaml.safe_load(front)["status"])


def _listed_names(text: str) -> list[str]:
    """The names a refusal lists: the comma-separated list after the colon on the
    line that names the canonical statuses."""
    line = next(
        (ln for ln in text.splitlines() if "backlog" in ln and "in_progress" in ln), None
    )
    assert line is not None, f"refusal lists no status names:\n{text}"
    return [n.strip() for n in line.rsplit(":", 1)[-1].split(",") if n.strip()]


LAYOUTS = ("single", "multi")
CUSTOM_CASES = [(lay, n) for lay in LAYOUTS for n in CUSTOM_ACCEPT]


@pytest.mark.parametrize(
    ("layout", "name"), CUSTOM_CASES, ids=[f"{lay}-{n!r}" for lay, n in CUSTOM_CASES]
)
def test_custom_theme_separator_names_accepted(
    repo: Path, buf: io.StringIO, layout: str, name: str
) -> None:
    item_id = _custom_item(repo, buf, layout)
    native, canonical = CUSTOM_ACCEPT[name]
    result, text = _move(buf, item_id, name)
    assert result.exit_code == 0, f"`move {item_id} {name!r}` refused:\n{text}"
    assert _file_status(repo, item_id) == native  # the theme's own name is written
    assert _status(buf, item_id) == canonical


@pytest.mark.parametrize("layout", LAYOUTS)
def test_custom_theme_refusal_lists_only_names_move_accepts(
    repo: Path, buf: io.StringIO, layout: str
) -> None:
    item_id = _custom_item(repo, buf, layout)
    path = _item_file(repo, item_id)
    before = path.read_bytes()
    result, text = _move(buf, item_id, "no-such-status")
    assert result.exit_code != 0, text
    assert path.read_bytes() == before

    listed = _listed_names(text)
    # every custom native name and every canonical name is offered, in some spelling
    offered = {_fold(n) for n in listed}
    missing = sorted(
        n for n in [*CUSTOM_MAPPINGS, *CANONICAL] if _fold(n) not in offered
    )
    assert not missing, f"refusal doesn't offer {missing}: {listed}"
    # and each offered name, fed back to `move`, is accepted
    refused = []
    for name in listed:
        res, out = _move(buf, item_id, name)
        if res.exit_code != 0:
            refused.append(name)
    assert not refused, f"refusal lists {refused}, which `move` then refuses; listed {listed}"


def test_multiboard_custom_names_refused_for_nautical_item(
    repo: Path, buf: io.StringIO
) -> None:
    """The custom board's names stay on the custom board."""
    _custom_item(repo, buf, "multi")
    exp_id = _create(buf, "nautical")
    for name in ("doing", "on-hold", "In Review"):
        path = _item_file(repo, exp_id)
        before = path.read_bytes()
        result, text = _move(buf, exp_id, name)
        assert result.exit_code != 0, f"`move {exp_id} {name!r}` accepted:\n{text}"
        assert path.read_bytes() == before
