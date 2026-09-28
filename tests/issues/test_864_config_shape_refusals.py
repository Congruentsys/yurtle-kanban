"""Issue #864: a structurally wrong kanban config crashes with a traceback.

Found in the review of PR #862 (#831). Under ``version: "2.0"``, ``boards: 5`` or
``boards: [7]`` raise TypeError/AttributeError. Neither ``KanbanConfig.from_text``
nor the local loader catches them, so ``claim`` prints a traceback, and one bad
config pushed to origin breaks ``claim`` in every clone.

Decided spec ([steer] on #864, bucket 1):

- Every structurally wrong config is refused with an ``InputRefused`` that names the
  field. That holds both locally (``load``) and when read from origin (``from_text``,
  #831). ``boards`` must be a list of mappings, each board a mapping with a string
  ``name`` and ``path``. ``kanban``, ``paths`` and the other sections must be
  mappings.
- Through the CLI it gives exit 1 and a one-line refusal, never a Python traceback.

Readings made here (the test partner's; the driver may challenge them):

a. "Names the offending field": the refusal message contains the field's key
   (``boards``, ``name``, ``path``, ``kanban``, ``wip_limits`` …). The expected
   shape is not pinned word for word.
b. The CLI refusal line: the config loader already prints ``Invalid <path>: …``
   for a refused config (#220, #338, pinned by test_338), and the sync commands
   print ``Error: …``. Either line is accepted, so #338's test keeps passing.
c. "Wip-limit overrides of the wrong type" means the whole ``wip_limits`` value:
   ``wip_limits: 5`` or ``wip_limits: [1]``, which today is silently dropped to
   "no overrides". A single bad limit *inside* the mapping (``in_progress: [1]``)
   is still dropped with a warning, as #411 decided. That is kept as a control.
d. ``root``, ``workflows`` and ``gates`` are covered as "other sections/fields"
   under the steer. They load silently today and crash or misbehave later.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import ITEM_ID, A, claim, item_text, output_of
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import InputRefused

V2 = 'version: "2.0"\n'
CONFIG = ".kanban/config.yaml"
ITEM = f"{EXP_DIR}/EXP-001-x.md"


def _board(extra: str) -> str:
    return V2 + f"boards:\n  - name: a\n    path: work/\n    {extra}\n"


# (id, config text, a field name the refusal must mention)
# Shapes that crash (or load silently) today: each must be refused.
RED_CASES: list[tuple[str, str, str]] = [
    # --- version 2.0, boards ---
    ("v2-boards-int", V2 + "boards: 5\n", "boards"),
    ("v2-boards-str", V2 + "boards: work\n", "boards"),
    ("v2-boards-mapping", V2 + "boards: {a: 1}\n", "boards"),
    ("v2-boards-list-of-int", V2 + "boards: [7]\n", "boards"),
    ("v2-boards-list-of-str", V2 + 'boards: ["x"]\n', "boards"),
    ("v2-board-name-int", V2 + "boards: [{name: 5, path: x}]\n", "name"),
    ("v2-board-path-list", V2 + "boards: [{name: a, path: [1]}]\n", "path"),
    ("v2-board-wip-int", _board("wip_limits: 5"), "wip_limits"),
    ("v2-board-wip-list", _board("wip_limits: [1]"), "wip_limits"),
    ("v2-board-gates-int", _board("gates: 5"), "gates"),
    # --- version 2.0 with no boards falls back to v1: same checks there ---
    ("v2-noboards-kanban-int", V2 + "kanban: 5\n", "kanban"),
    # --- v1 ---
    ("v1-kanban-int", "kanban: 5\n", "kanban"),
    ("v1-kanban-list", "kanban: [1]\n", "kanban"),
    ("v1-kanban-str", "kanban: hello\n", "kanban"),
    ("v1-root-list", "kanban:\n  paths: {root: [1]}\n", "root"),
    ("v1-workflows-int", "kanban:\n  workflows: 5\n", "workflows"),
    ("v1-gates-int", "kanban:\n  gates: 5\n", "gates"),
]

# Shapes already refused with an InputRefused today (controls: they stay refused).
REFUSED_TODAY: list[tuple[str, str, str]] = [
    ("v1-paths-int", "kanban:\n  paths: 5\n", "paths"),
    ("v1-toplevel-paths-int", "paths: 5\n", "paths"),
    ("v1-scan_paths-int", "kanban:\n  paths: {scan_paths: 5}\n", "scan_paths"),
    ("v1-ignore-int", "kanban:\n  paths: {ignore: 5}\n", "ignore"),
    ("v1-theme-list", "kanban:\n  theme: [software]\n", "theme"),
    ("v2-noboards-paths-int", V2 + "kanban:\n  paths: 5\n", "paths"),
    ("v2-board-preset-list", _board("preset: [software]"), "preset"),
    ("v2-board-scan_paths-int", _board("scan_paths: 5"), "scan_paths"),
    ("v2-board-ignore-int", _board("ignore: 5"), "ignore"),
]

# Added by the driver: the other typed v2 fields, per the implementer brief (#864).
MORE_FIELDS: list[tuple[str, str, str]] = [
    ("v2-namespace-list", _board("") + "namespace: [1]\n", "namespace"),
    ("v2-default_board-int", _board("") + "default_board: 5\n", "default_board"),
    ("v2-board-wip_exempt_types-str", _board("wip_exempt_types: expedite"), "wip_exempt_types"),
    ("v2-board-wip_exempt_types-int-entry", _board("wip_exempt_types: [1]"), "wip_exempt_types"),
]

ALL_CASES = RED_CASES + REFUSED_TODAY + MORE_FIELDS


def _ids(cases: list[tuple[str, str, str]]) -> list[str]:
    return [c[0] for c in cases]


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _config_file(root: Path, text: str) -> Path:
    kanban = root / ".kanban"
    kanban.mkdir(parents=True, exist_ok=True)
    path = kanban / "config.yaml"
    path.write_text(text)
    return path


def _repo(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _config_file(root, text)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t.com"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    (root / "work").mkdir()
    (root / "work" / ".keep").write_text("")
    return root


def _assert_refused(fn: Any, field: str) -> None:
    """`fn()` raises InputRefused (never TypeError/AttributeError/KeyError) naming
    `field`."""
    try:
        fn()
    except InputRefused as e:
        assert field in str(e), f"the refusal must name `{field}`: {e}"
        return
    except Exception as e:  # noqa: BLE001 — a crash is the bug under test
        pytest.fail(f"crashed with {type(e).__name__}: {e} (expected InputRefused)")
    pytest.fail(f"loaded without a refusal (expected InputRefused naming `{field}`)")


def _assert_cli_refusal(result: Any) -> None:
    out = result.output or ""
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(
            f"CLI crashed: {type(result.exception).__name__}: {result.exception}"
        ) from result.exception
    assert result.exit_code == 1, out
    flat = " ".join(out.split())
    assert "Error:" in flat or ("Invalid" in flat and "config.yaml" in flat), out


# --- 1+2: from_text and load refuse every structurally wrong config ----------------


@pytest.mark.parametrize(("case", "text", "field"), ALL_CASES, ids=_ids(ALL_CASES))
def test_from_text_refuses(tmp_path: Path, case: str, text: str, field: str) -> None:
    _assert_refused(lambda: KanbanConfig.from_text(text, tmp_path), field)


@pytest.mark.parametrize(("case", "text", "field"), ALL_CASES, ids=_ids(ALL_CASES))
def test_load_refuses(tmp_path: Path, case: str, text: str, field: str) -> None:
    path = _config_file(tmp_path, text)
    _assert_refused(lambda: KanbanConfig.load(path), field)


# --- 3: the CLI refuses in one line, never a traceback ---------------------------------


@pytest.mark.parametrize(
    "text",
    [V2 + "boards: 5\n", "kanban: 5\n"],
    ids=["v2-boards-int", "v1-kanban-int"],
)
def test_cli_list_refuses_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    repo = _repo(tmp_path / "repo", text)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, ["list"])
    _assert_cli_refusal(result)


# --- 4: origin's config is structurally wrong (#831 path) ------------------------------


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned."""
    w = World(tmp_path)
    (w.a / ITEM).parent.mkdir(parents=True, exist_ok=True)
    (w.a / ITEM).write_text(item_text("ready"))
    git(w.a, "add", "-A")
    git(w.a, "commit", "-m", "seed EXP-001")
    git(w.a, "push", "origin", f"HEAD:refs/heads/{w.default}")
    return w


def test_claim_with_origin_boards_int_is_refused(world: World) -> None:
    b_push(world, {CONFIG: V2 + "boards: 5\n"})
    base = world.remote_sha()

    try:
        out = claim(world.a, A)
    except Exception as e:  # noqa: BLE001 — a crash is the bug under test
        pytest.fail(f"claim crashed with {type(e).__name__}: {e}")

    assert out.kind in ("refused", "push_refused"), f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    assert "config" in out.message.lower(), out.message
    assert world.remote_sha() == base, "nothing may be pushed"


def test_cli_claim_with_origin_boards_int_is_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    b_push(world, {CONFIG: V2 + "boards: 5\n"})
    base = world.remote_sha()

    monkeypatch.chdir(world.a)
    result = CliRunner().invoke(main, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(
            f"claim crashed: {type(result.exception).__name__}: {result.exception}"
        ) from result.exception
    assert result.exit_code == 1, out
    assert world.remote_sha() == base, "nothing may be pushed"


def test_control_claim_with_origin_paths_int_is_refused(world: World) -> None:
    """An already-refused shape on origin: refused, nothing pushed (today too)."""
    b_push(world, {CONFIG: "kanban:\n  paths: 5\n"})
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind in ("refused", "push_refused"), f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    assert world.remote_sha() == base


# --- 5: controls — valid configs still load --------------------------------------------

VALID_V1 = """\
kanban:
  theme: software
  paths:
    root: work/
    scan_paths: [work/]
    ignore: ["**/archive/**"]
  workflows: {}
  gates: {}
"""
VALID_V2 = V2 + """\
boards:
  - name: dev
    preset: software
    path: work/
    scan_paths: [work/]
    wip_limits:
      in_progress: 2
      review: {feature: 1, _default: 3}
    gates: {}
  - name: research
    path: research/
    wip_limits: null
    wip_exempt_types: [expedite]
namespace: https://example.org/kanban/
default_board: dev
"""


@pytest.mark.parametrize("loader", ["from_text", "load"])
def test_control_valid_v1_loads(tmp_path: Path, loader: str) -> None:
    config = (
        KanbanConfig.from_text(VALID_V1, tmp_path)
        if loader == "from_text"
        else KanbanConfig.load(_config_file(tmp_path, VALID_V1))
    )
    assert not config.is_multi_board
    assert config.theme == "software"
    assert config.paths.root == "work/"
    assert config.paths.scan_paths == ["work/"]


@pytest.mark.parametrize("loader", ["from_text", "load"])
def test_control_valid_v2_loads(tmp_path: Path, loader: str) -> None:
    config = (
        KanbanConfig.from_text(VALID_V2, tmp_path)
        if loader == "from_text"
        else KanbanConfig.load(_config_file(tmp_path, VALID_V2))
    )
    assert config.is_multi_board
    assert [b.name for b in config.boards] == ["dev", "research"]
    assert config.boards[0].wip_limits == {
        "in_progress": 2, "review": {"feature": 1, "_default": 3},
    }
    assert config.boards[1].wip_limits is None
    assert config.default_board == "dev"
    assert config.namespace == "https://example.org/kanban/"
    assert config.boards[1].wip_exempt_types == ["expedite"]


def test_control_empty_and_bare_sections_load(tmp_path: Path) -> None:
    """Null means default (#194, #204, #220): a bare `boards:`, `kanban:` or
    `paths:` is not a wrong shape."""
    for text in ("", V2 + "boards:\n", "kanban:\n", "kanban:\n  paths:\n"):
        config = KanbanConfig.from_text(text, tmp_path)
        assert not config.is_multi_board, text


def test_control_null_board_entry_skipped(tmp_path: Path) -> None:
    """A bare `- ` list entry is skipped, not refused (#220)."""
    text = V2 + "boards:\n  -\n  - name: a\n    path: work/\n"
    config = KanbanConfig.from_text(text, tmp_path)
    assert [b.name for b in config.boards] == ["a"]


def test_control_bad_single_wip_limit_still_dropped(tmp_path: Path) -> None:
    """One bad limit inside `wip_limits` is dropped with a warning (#411), not
    refused."""
    text = _board("wip_limits: {in_progress: [1], review: 2}")
    config = KanbanConfig.from_text(text, tmp_path)
    assert config.boards[0].wip_limits == {"review": 2}


def test_control_cli_list_valid_config_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo", VALID_V2)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, ["list"])
    assert "Traceback" not in (result.output or "")
    assert result.exit_code == 0, result.output


# --- round 2 (PR #898 review): a type folder under paths must be a path string ---


@pytest.mark.parametrize("key", ["features", "bugs", "epics", "tasks"])
@pytest.mark.parametrize("bad", ["5", "[a]"])
def test_type_folder_path_shapes_refused(tmp_path: Path, key: str, bad: str) -> None:
    text = f"kanban:\n  paths:\n    {key}: {bad}\n"
    with pytest.raises(InputRefused, match=key):
        KanbanConfig.from_text(text, tmp_path)


def test_claim_with_origin_type_folder_int_is_refused(world: World) -> None:
    """Round 2: `paths.features: 5` on origin crashed later in get_work_paths()."""
    b_push(world, {CONFIG: "kanban:\n  paths:\n    features: 5\n"})
    base = world.remote_sha()

    try:
        out = claim(world.a, A)
    except Exception as e:  # noqa: BLE001 — a crash is the bug under test
        pytest.fail(f"claim crashed with {type(e).__name__}: {e}")

    assert out.kind in ("refused", "push_refused"), f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    assert world.remote_sha() == base, "nothing may be pushed"
