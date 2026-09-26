"""#509: three path edge cases left over from the #504 (#494) review.

Decided behaviour ([steer] bucket 2):
a) `KanbanConfig._single_board_path()`: when the scan paths share only the
   filesystem root `/`, fall back to `root or "work/"` -- never `"//"` or `"/"`.
   `board-add`'s single -> multi-board upgrade then refuses with its normal
   "uncovered" message. Controls: a real common parent is still returned.
b) `init --path` quotes `root` the way it quotes `scan_paths`, so `--path '~'` (and a
   path with YAML-special characters) loads back as written, not as null.
c) The hook `log` action resolves its `path:` through `_under` (as #479 does): a
   leading `~` is $HOME, never `<repo>/~/...`. Controls: relative and absolute paths
   are unchanged.

HOME (and USERPROFILE) point at a tmp dir, so nothing touches the real home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_479_tilde_paths import _init_repo, _no_literal_tilde, _single_yaml
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.hooks import HookContext, HookEvent, _action_log


@pytest.fixture(autouse=True)
def _clear_theme_cache():
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A fake home: `Path("~").expanduser()` resolves here, never to the real one."""
    fake = (tmp_path / "home").resolve()
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    assert Path("~/b").expanduser() == fake / "b"
    return fake


def _load(repo: Path) -> KanbanConfig:
    return KanbanConfig.load(repo / ".kanban" / "config.yaml")


# --- a) scan paths sharing only `/` ------------------------------------------------

# (id, scans); "H" is replaced by the fake $HOME. Every case shares only `/`.
ONLY_SLASH = [
    ("abs-abs", ["/abs/c/", "/other/b/"]),
    ("tilde-abs-outside-home", ["~/b/", "/abs/c/"]),
    ("abs-home-abs-outside", ["H/b/", "/abs/c/"]),
]


def _spell(p: str, home: Path) -> str:
    return p.replace("H/", f"{home}/", 1) if p.startswith("H/") else p


@pytest.mark.parametrize("root", [None, "/elsewhere/r/"], ids=["no-root", "root"])
@pytest.mark.parametrize("scans", [c[1] for c in ONLY_SLASH], ids=[c[0] for c in ONLY_SLASH])
def test_single_board_path_only_slash_falls_back(home, root, scans):
    config = KanbanConfig()
    config.paths.root = root  # type: ignore[assignment]
    config.paths.scan_paths = [_spell(s, home) for s in scans]

    got = config._single_board_path()
    assert got not in ("//", "/"), f"scan paths share only '/', got {got!r}"
    assert got == (root or "work/"), got


@pytest.mark.parametrize("scans", [c[1] for c in ONLY_SLASH], ids=[c[0] for c in ONLY_SLASH])
def test_single_board_path_only_slash_from_yaml(tmp_path, home, scans):
    """The same through a loaded config: `root: work/` (init's default) is the fallback."""
    repo = _init_repo(tmp_path / "repo", _single_yaml("work/", [_spell(s, home) for s in scans], []))
    got = _load(repo)._single_board_path()
    assert got == "work/", got


@pytest.mark.parametrize("scans", [c[1] for c in ONLY_SLASH], ids=[c[0] for c in ONLY_SLASH])
def test_board_add_only_slash_gives_uncovered_message(tmp_path, home, monkeypatch, scans):
    repo = _init_repo(tmp_path / "repo", _single_yaml("work/", [_spell(s, home) for s in scans], []))
    before = (repo / ".kanban" / "config.yaml").read_text()

    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        main, ["board-add", "research", "--preset", "hdd", "--path", "research/"],
    )
    assert result.exit_code == 1, result.output
    assert "Can't upgrade to multi-board" in result.output, result.output
    assert "//" not in result.output, result.output
    assert (repo / ".kanban" / "config.yaml").read_text() == before
    _no_literal_tilde(repo)


@pytest.mark.parametrize(
    "scans,want",
    [
        (["/abs/x/a/", "/abs/x/b/"], "/abs/x/"),
        (["~/b/p/", "~/b/q/"], "~/b/"),
        (["H/b/p/", "~/b/q/"], "H/b/"),
        (["kanban-work/features/", "kanban-work/bugs/"], "kanban-work/"),
    ],
    ids=["abs", "tilde", "mixed-tilde-abs", "relative"],
)
def test_control_real_common_parent(home, scans, want):
    config = KanbanConfig()
    config.paths.root = None  # type: ignore[assignment]
    config.paths.scan_paths = [_spell(s, home) for s in scans]
    got = config._single_board_path()
    assert Path(got).expanduser() == Path(_spell(want, home)).expanduser(), got
    assert got.endswith("/") and got != "/", got


# --- b) init quotes root -------------------------------------------------------------


def _init(repo: Path, monkeypatch, path: str):
    repo.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, ["init", "--path", path])
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output, result.exception,
    )
    assert result.exit_code == 0, result.output
    return result


def _raw_yaml(repo: Path) -> dict:
    text = (repo / ".kanban" / "config.yaml").read_text()
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        pytest.fail(f"init wrote a config that is not valid YAML ({e}):\n{text}")


def _raw_root_line(repo: Path) -> str:
    text = (repo / ".kanban" / "config.yaml").read_text()
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("root:")]
    assert len(lines) == 1, text
    return lines[0].strip()


@pytest.mark.parametrize("path", ["~", "~/"], ids=["tilde", "tilde-slash"])
def test_init_tilde_root_loads_as_tilde(tmp_path, home, monkeypatch, path):
    repo = tmp_path / "repo"
    _init(repo, monkeypatch, path)

    raw = _raw_yaml(repo)
    assert raw["kanban"]["paths"]["root"] == path, raw["kanban"]["paths"]
    assert _raw_root_line(repo) == f'root: "{path}"'
    assert _load(repo).paths.root == path
    _no_literal_tilde(repo)


@pytest.mark.parametrize("path", ["a: b/", "x #y/"], ids=["colon-space", "hash"])
def test_init_yaml_special_root_loads_as_written(tmp_path, home, monkeypatch, path):
    repo = tmp_path / "repo"
    _init(repo, monkeypatch, path)

    raw = _raw_yaml(repo)
    assert raw["kanban"]["paths"]["root"] == path, raw["kanban"]["paths"]
    assert _raw_root_line(repo) == f'root: "{path}"'
    assert _load(repo).paths.root == path


def test_init_control_plain_root(tmp_path, home, monkeypatch):
    repo = tmp_path / "repo"
    _init(repo, monkeypatch, "work/")
    assert _load(repo).paths.root == "work/"
    assert _load(repo).paths.scan_paths == ["work/"]


# --- c) hook log path ----------------------------------------------------------------


def _ctx(repo_root: Path | None) -> HookContext:
    return HookContext(
        event=HookEvent.ITEM_CREATED,
        item_id="FEAT-001",
        item_type="feature",
        title="A title",
        new_status="backlog",
        repo_root=repo_root,
    )


def _entries(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


@pytest.mark.parametrize(
    "spelled,where",
    [("~/kanban.log", "kanban.log"), ("~/logs/{item_type}.log", "logs/feature.log")],
    ids=["plain", "templated"],
)
def test_log_action_tilde_path_is_home(tmp_path, home, spelled, where):
    repo = tmp_path / "repo"
    repo.mkdir()
    _action_log({"type": "log", "path": spelled}, _ctx(repo))

    target = home / where
    assert target.exists(), f"log not written to {target}"
    assert _entries(target)[0]["item_id"] == "FEAT-001"
    _no_literal_tilde(repo)


def test_log_action_tilde_path_no_repo_root(tmp_path, home, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    _action_log({"type": "log", "path": "~/kanban.log"}, _ctx(None))

    assert (home / "kanban.log").exists()
    assert not (cwd / "~").exists()


def test_log_action_control_relative(tmp_path, home):
    repo = tmp_path / "repo"
    repo.mkdir()
    _action_log({"type": "log", "path": ".kanban/hooks.log"}, _ctx(repo))
    assert _entries(repo / ".kanban" / "hooks.log")[0]["item_id"] == "FEAT-001"
    assert not (home / ".kanban").exists()


def test_log_action_control_default(tmp_path, home):
    repo = tmp_path / "repo"
    repo.mkdir()
    _action_log({"type": "log"}, _ctx(repo))
    assert (repo / ".kanban" / "hooks.log").exists()


def test_log_action_control_absolute(tmp_path, home):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "elsewhere" / "k.log"
    _action_log({"type": "log", "path": str(target)}, _ctx(repo))
    assert _entries(target)[0]["item_id"] == "FEAT-001"
