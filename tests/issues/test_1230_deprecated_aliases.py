"""#1230 — the 2.x forms #580 removed are accepted again, with a deprecation warning.

Captain's ruling (2026-10-02, on #1230): B partially reverses #580's "no aliases
kept". The 3.x forms stay canonical; each removed 2.x form maps to its 3.x
equivalent, behaves exactly as it, and prints ONE line on stderr:

    yurtle-kanban: <command> <old> is deprecated, use <command> <new> (removed in 4.0)

| 2.x form                          | maps to                              |
|-----------------------------------|--------------------------------------|
| `move ID STATUS -a A`             | `move --assign A`                    |
| `create --assignee/-a A`          | `create --assign A`                  |
| `create --description/-d TEXT`    | `create --body TEXT`                 |
| `comment ID TEXT`                 | `comment ID --body TEXT`             |
| `comment --author/-a A`           | `comment --agent A`                  |
| `next --assignee/-a A`            | `next --agent A`                     |
| `list -a A`                       | `list --assignee A`                  |

Pinned here: each row writes the same file and prints the same stdout as its new
form, with exactly one deprecation line on stderr (and stderr otherwise the
same); `--json` stdout stays one parseable JSON value; giving an old form
together with its new form (or two old spellings of it) is a usage error
(exit 2), never a silent pick; the old forms are hidden from `--help`.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import cli
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

DEPRECATION = re.compile(r"^yurtle-kanban: .* is deprecated, use .* \(removed in 4\.0\)$")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def _make_repo(root: Path) -> Path:
    """A git repo with a software-theme board and one item, FEAT-001 (ready,
    held by carol)."""
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@test.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".kanban").mkdir()
    (root / "kanban-work" / "features").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/features/"]),
    ).save(root / ".kanban" / "config.yaml")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _invoke(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> Result:
    monkeypatch.chdir(repo)
    # no wrapping: a printed path is compared whole, with its repo masked
    monkeypatch.setattr(cli.console, "_width", 10_000)
    return CliRunner().invoke(main, args)


def _seed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for args in (
        ["create", "feature", "probe"],
        ["move", "FEAT-001", "ready", "--assign", "carol", "--agent", "Test", "--no-commit"],
    ):
        r = _invoke(repo, args, monkeypatch)
        assert r.exit_code == 0, (args, r.output)


_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")


def _tree(repo: Path) -> dict[str, str]:
    """The board's files, timestamps normalised (two runs differ by seconds)."""
    return {
        str(p.relative_to(repo)): _STAMP.sub("<T>", p.read_text())
        for p in sorted((repo / "kanban-work").rglob("*.md"))
    }


def _deprecations(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if DEPRECATION.match(line)]


def _line(command: str, old: str, new: str) -> str:
    return f"yurtle-kanban: {command} {old} is deprecated, use {command} {new} (removed in 4.0)"


# (id, old args, new args, the expected deprecation line)
ROWS = [
    ("move -a",
     ["move", "FEAT-001", "in_progress", "-a", "dave", "--agent", "Test", "--no-commit"],
     ["move", "FEAT-001", "in_progress", "--assign", "dave", "--agent", "Test", "--no-commit"],
     _line("move", "-a", "--assign")),
    ("create -a",
     ["create", "bug", "crash", "-a", "dave"],
     ["create", "bug", "crash", "--assign", "dave"],
     _line("create", "-a", "--assign")),
    ("create --assignee",
     ["create", "bug", "crash", "--assignee", "dave"],
     ["create", "bug", "crash", "--assign", "dave"],
     _line("create", "--assignee", "--assign")),
    ("create -d",
     ["create", "feature", "dark", "-d", "the body text"],
     ["create", "feature", "dark", "--body", "the body text"],
     _line("create", "-d", "--body")),
    ("create --description",
     ["create", "feature", "dark", "--description", "the body text"],
     ["create", "feature", "dark", "--body", "the body text"],
     _line("create", "--description", "--body")),
    ("comment ID TEXT",
     ["comment", "FEAT-001", "shipped it", "--agent", "dave"],
     ["comment", "FEAT-001", "--body", "shipped it", "--agent", "dave"],
     _line("comment", "ID TEXT", "ID --body TEXT")),
    ("comment --author",
     ["comment", "FEAT-001", "--body", "shipped it", "--author", "dave"],
     ["comment", "FEAT-001", "--body", "shipped it", "--agent", "dave"],
     _line("comment", "--author", "--agent")),
    ("comment -a",
     ["comment", "FEAT-001", "--body", "shipped it", "-a", "dave"],
     ["comment", "FEAT-001", "--body", "shipped it", "--agent", "dave"],
     _line("comment", "-a", "--agent")),
    ("next -a",
     ["next", "-a", "carol"],
     ["next", "--agent", "carol"],
     _line("next", "-a", "--agent")),
    ("next --assignee",
     ["next", "--assignee", "carol"],
     ["next", "--agent", "carol"],
     _line("next", "--assignee", "--agent")),
    ("list -a",
     ["list", "-a", "carol"],
     ["list", "--assignee", "carol"],
     _line("list", "-a", "--assignee")),
]

JSON_ROWS = [
    ("next -a --json", ["next", "-a", "carol", "--json"], ["next", "--agent", "carol", "--json"],
     _line("next", "-a", "--agent")),
    ("next --assignee --json", ["next", "--assignee", "carol", "--json"],
     ["next", "--agent", "carol", "--json"], _line("next", "--assignee", "--agent")),
    ("list -a --json", ["list", "-a", "carol", "--json"],
     ["list", "--assignee", "carol", "--json"], _line("list", "-a", "--assignee")),
]


def _twin(tmp_path_factory, monkeypatch, old: list[str], new: list[str]):
    """Run `old` and `new` on two identical boards: (old result, new result,
    old tree, new tree)."""
    a = _make_repo(tmp_path_factory.mktemp("old"))
    b = _make_repo(tmp_path_factory.mktemp("new"))
    _seed(a, monkeypatch)
    _seed(b, monkeypatch)
    r_old = _invoke(a, old, monkeypatch)
    r_new = _invoke(b, new, monkeypatch)
    return _Run(r_old, a), _Run(r_new, b), _tree(a), _tree(b)


class _Run:
    """A result whose stdout/stderr name its repo as `<REPO>` (paths are printed)
    and its timestamps as `<T>` (two runs differ by seconds)."""

    def __init__(self, result: Result, repo: Path) -> None:
        def norm(text: str) -> str:
            return _STAMP.sub("<T>", text.replace(str(repo), "<REPO>"))

        self.exit_code = result.exit_code
        self.output = result.output
        self.stdout = norm(result.stdout)
        self.stderr = norm(result.stderr)


@pytest.mark.parametrize(
    "old, new, line", [pytest.param(o, n, ln, id=i) for i, o, n, ln in ROWS + JSON_ROWS]
)
def test_old_form_behaves_as_the_new_form_and_warns_once(
    tmp_path_factory, monkeypatch, old, new, line
):
    r_old, r_new, tree_old, tree_new = _twin(tmp_path_factory, monkeypatch, old, new)
    assert r_new.exit_code == 0, r_new.output
    assert r_old.exit_code == r_new.exit_code, (r_old.exit_code, r_old.output)
    assert r_old.stdout == r_new.stdout
    assert tree_old == tree_new
    assert _deprecations(r_new.stderr) == []
    assert _deprecations(r_old.stderr) == [line], r_old.stderr
    # stderr is otherwise the new form's: the deprecation is the only addition
    rest = [ln for ln in r_old.stderr.splitlines() if not DEPRECATION.match(ln)]
    assert rest == r_new.stderr.splitlines()


@pytest.mark.parametrize(
    "old, new, line", [pytest.param(o, n, ln, id=i) for i, o, n, ln in JSON_ROWS]
)
def test_json_stdout_stays_pure_json(tmp_path_factory, monkeypatch, old, new, line):
    r_old, _, _, _ = _twin(tmp_path_factory, monkeypatch, old, new)
    assert r_old.exit_code == 0, r_old.output
    json.loads(r_old.stdout)  # nothing else on stdout
    assert "deprecated" not in r_old.stdout


def test_the_effect_is_the_new_forms(tmp_path_factory, monkeypatch):
    """Belt and braces on the row comparisons: the mapped values land."""
    repo = _make_repo(tmp_path_factory.mktemp("effect"))
    _seed(repo, monkeypatch)
    for args in (
        ["create", "bug", "crash", "-a", "dave", "-d", "the body text"],
        ["comment", "FEAT-001", "shipped it", "-a", "erin"],
        ["move", "FEAT-001", "in_progress", "-a", "frank", "--agent", "Test", "--no-commit"],
    ):
        r = _invoke(repo, args, monkeypatch)
        assert r.exit_code == 0, (args, r.output)
    bug = json.loads(_invoke(repo, ["show", "BUG-001", "--json"], monkeypatch).stdout)
    assert bug["assignee"] == "dave"
    assert "the body text" in bug["description"]
    feat = json.loads(_invoke(repo, ["show", "FEAT-001", "--json"], monkeypatch).stdout)
    assert feat["assignee"] == "frank"
    assert [(c["author"], c["content"]) for c in feat["comments"]] == [("erin", "shipped it")]


def test_two_deprecated_forms_warn_once_each(tmp_path_factory, monkeypatch):
    repo = _make_repo(tmp_path_factory.mktemp("two"))
    _seed(repo, monkeypatch)
    r = _invoke(repo, ["comment", "FEAT-001", "shipped it", "--author", "dave"], monkeypatch)
    assert r.exit_code == 0, r.output
    assert _deprecations(r.stderr) == [
        _line("comment", "ID TEXT", "ID --body TEXT"),
        _line("comment", "--author", "--agent"),
    ]


# -- old and new together: a usage error, nothing written ---------------------

BOTH = [
    ("move -a + --assign",
     ["move", "FEAT-001", "in_progress", "-a", "x", "--assign", "y", "--no-commit"]),
    ("create -a + --assign", ["create", "bug", "c", "-a", "x", "--assign", "y"]),
    ("create --assignee + --assign", ["create", "bug", "c", "--assignee", "x", "--assign", "y"]),
    ("create -a + --assignee", ["create", "bug", "c", "-a", "x", "--assignee", "y"]),
    ("create -d + --body", ["create", "bug", "c", "-d", "x", "--body", "y"]),
    ("create --description + --body-file",
     ["create", "bug", "c", "--description", "x", "--body-file", "-"]),
    ("comment TEXT + --body", ["comment", "FEAT-001", "x", "--body", "y"]),
    ("comment TEXT + --body-file", ["comment", "FEAT-001", "x", "--body-file", "-"]),
    ("comment --author + --agent", ["comment", "FEAT-001", "--body", "x", "--author", "a",
                                    "--agent", "b"]),
    ("comment -a + --agent", ["comment", "FEAT-001", "--body", "x", "-a", "a", "--agent", "b"]),
    ("next -a + --agent", ["next", "-a", "x", "--agent", "y"]),
    ("next --assignee + --agent", ["next", "--assignee", "x", "--agent", "y"]),
    ("list -a + --assignee", ["list", "-a", "x", "--assignee", "y"]),
]


@pytest.mark.parametrize("args", [pytest.param(a, id=i) for i, a in BOTH])
def test_old_and_new_together_is_a_usage_error(tmp_path_factory, monkeypatch, args):
    repo = _make_repo(tmp_path_factory.mktemp("both"))
    _seed(repo, monkeypatch)
    before = _tree(repo)
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(main, args, input="from stdin\n")
    assert r.exit_code == 2, (r.exit_code, r.output)
    assert "both given" in " ".join(r.stderr.split()), r.stderr
    assert _deprecations(r.stderr) == []
    assert _tree(repo) == before


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["list", "-a", "x", "--assignee", "y", "--json"], id="list"),
        pytest.param(["next", "-a", "x", "--agent", "y", "--json"], id="next"),
    ],
)
def test_old_and_new_together_under_json_is_one_json_refusal(
    tmp_path_factory, monkeypatch, args
):
    repo = _make_repo(tmp_path_factory.mktemp("bothjson"))
    _seed(repo, monkeypatch)
    r = _invoke(repo, args, monkeypatch)
    assert r.exit_code == 2, r.output
    doc = json.loads(r.stdout)
    assert doc["success"] is False and "both given" in doc["error"], doc


# -- hidden from --help -------------------------------------------------------


@pytest.mark.parametrize(
    "command, hidden",
    [
        ("move", ["-a"]),
        ("create", ["-a", "--assignee", "-d", "--description"]),
        ("comment", ["-a", "--author"]),
        ("next", ["-a", "--assignee"]),
        ("list", ["-a"]),
    ],
)
def test_deprecated_forms_are_hidden_from_help(command, hidden):
    out = CliRunner().invoke(main, [command, "--help"]).output
    for flag in hidden:
        assert not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", out), (flag, out)


def test_comment_usage_line_hides_the_positional_text():
    out = CliRunner().invoke(main, ["comment", "--help"]).output
    usage = next(line for line in out.splitlines() if line.startswith("Usage:"))
    assert usage.split() == ["Usage:", "main", "comment", "[OPTIONS]", "ITEM_ID"], usage
