"""Issue #574, PR C round 2: the shipped skills pass ``--agent`` to ``move``.

PR C's holder guard refuses a ``move`` of an in-progress item whose assignee is not
``resolve_actor(--agent)``. The ``work`` skill claims with ``claim ID --agent
<your-agent-name>`` and never sets ``YURTLE_AGENT``. So a follow-on ``move`` without
``--agent`` falls back to git ``user.name``, which is the machine's account and not
the agent's name, and it is refused "held by <agent>". The PR #821 review found this
in the ``done``, ``blocked`` and ``handoff`` skills of both themes.

1. Structural: every ``yurtle-kanban move`` a SKILL.md prints passes ``--agent``
   (or a ``YURTLE_AGENT=`` prefix on the same command). Commands are extracted with
   the same INVOCATION / BACKTICK_INVOCATION patterns as
   tests/test_skill_commands_execute.py.
2. Behavioural: in a scratch repo whose git ``user.name`` is ``hankh95``, run the work
   skill's literal ``claim`` line, then each follow-on skill's literal ``move`` lines,
   with every ``<placeholder>`` replaced by ``Claude-M5``. Each must exit 0.

Allow-list: the ``review`` skills' ``move ... done``. The reviewer is by design NOT
the holder (reviewer != author) and never claims, so there is no claimed identity
to pass. The move is ``review -> done``, which §4 leaves unguarded
(``test_cli_reviewer_review_to_done_is_not_guarded``). It succeeds as whoever runs
it, so it has no holder to match. A control below runs that literal line as a
different git user after the holder moved the item to review.

Test partner's readings (the driver may challenge them):

a. Every ``move`` except the reviewer's must pass an agent, including those that are
   not guarded today. That is the blocked skill's unblock (``blocked ->
   in_progress``) and the done skill's ``done`` alternative from in-progress (which IS
   guarded). They run in the holder's flow after the claim, and ``--agent`` also
   makes ``kb:by`` record the agent, not the machine account (#580).
b. The fix may be ``--agent <name>`` or a ``YURTLE_AGENT=<name>`` prefix on the
   command. A session-wide ``export YURTLE_AGENT`` in the work skill alone does not
   pass the structural test: the follow-on skills are separate documents, run in
   separate shells.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.test_skill_commands_execute import BACKTICK_INVOCATION, INVOCATION
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

SKILLS = Path(__file__).resolve().parents[2] / "skills"
AGENT = "Claude-M5"
MACHINE = "hankh95"
REVIEWER = "reviewer-git-user"
THEMES = {
    # theme: (item id, item dir, item type)
    "software": ("FEAT-001", "kanban-work/features", "feature"),
    "nautical": ("EXP-001", "kanban-work/expeditions", "expedition"),
}
SHELL_COMMENT = re.compile(r"\s+#\s.*$")


def commands(path: Path) -> list[tuple[int, str]]:
    """(line number, command text) for each yurtle-kanban use in a SKILL.md."""
    found: list[tuple[int, str]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if INVOCATION.match(line):
            found.append((n, SHELL_COMMENT.sub("", line).strip().lstrip("$").strip()))
            continue
        for span in BACKTICK_INVOCATION.findall(line):
            found.append((n, span.strip()))
    return found


def split(command: str) -> tuple[dict[str, str], list[str]]:
    """The env prefix and the argv after `yurtle-kanban`."""
    words = shlex.split(command)
    env: dict[str, str] = {}
    while words and re.match(r"^[A-Z_][A-Z0-9_]*=", words[0]):
        key, _, value = words.pop(0).partition("=")
        env[key] = value
    assert words and words[0] == "yurtle-kanban", command
    return env, words[1:]


def is_move(command: str) -> bool:
    _, argv = split(command)
    return bool(argv) and argv[0] == "move"


def passes_agent(command: str) -> bool:
    env, argv = split(command)
    return "--agent" in argv or any(a.startswith("--agent=") for a in argv) or (
        "YURTLE_AGENT" in env
    )


def allowed_without_agent(path: Path, command: str) -> bool:
    """The reviewer's `move X done` (see the module docstring)."""
    _, argv = split(command)
    return path.parent.name == "review" and argv[:1] == ["move"] and argv[-1:] == ["done"]


MOVES = [
    (path, n, cmd)
    for path in sorted(SKILLS.rglob("SKILL.md"))
    for n, cmd in commands(path)
    if is_move(cmd)
]


def test_moves_are_found() -> None:
    """Guard on the extraction: the done, blocked, handoff and review skills of both
    themes all print a move, so this test can't pass vacuously."""
    where = {(p.parent.parent.name, p.parent.name) for p, _, _ in MOVES}
    for theme in ("software", "nautical"):
        for skill in ("done", "blocked", "handoff", "review"):
            assert (theme, skill) in where, f"no move found in {theme}/{skill}: {where}"


@pytest.mark.parametrize(
    ("path", "n", "cmd"), MOVES, ids=[f"{p.parent.parent.name}/{p.parent.name}:{n}" for p, n, _ in MOVES]
)
def test_skill_move_passes_agent(path: Path, n: int, cmd: str) -> None:
    if allowed_without_agent(path, cmd):
        pytest.skip("the reviewer's review -> done: not guarded, the reviewer never claims")
    assert passes_agent(cmd), (
        f"{path.relative_to(SKILLS.parent)}:{n}: `{cmd}` runs after `claim --agent` but "
        "passes no --agent (nor a YURTLE_AGENT= prefix), so the holder guard refuses "
        "it as git user.name"
    )


# --- behavioural: run the literal lines ------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"
    return result.stdout


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, theme: str) -> Path:
    """A repo with no remote, git user.name `hankh95`, and one ready item; cwd."""
    item_id, item_dir, item_type = THEMES[theme]
    work = tmp_path / "work"
    (work / item_dir).mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    for key, value in (
        ("user.name", MACHINE), ("user.email", "m@test.com"), ("commit.gpgsign", "false"),
    ):
        _git(work, "config", key, value)
    KanbanConfig(
        theme=theme, paths=PathConfig(root="kanban-work/", scan_paths=[f"{item_dir}/"]),
    ).save(work / ".kanban" / "config.yaml")
    (work / item_dir / f"{item_id}-x.md").write_text(
        f"---\nid: {item_id}\ntitle: \"X\"\ntype: {item_type}\nstatus: ready\n---\n\n"
        "# X\n\nA description long enough.\n"
    )
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    monkeypatch.chdir(work)
    return work


def literal(theme: str, skill: str, verb: str) -> list[tuple[int, str]]:
    path = SKILLS / theme / skill / "SKILL.md"
    return [(n, c) for n, c in commands(path) if split(c)[1][:1] == [verb]]


def concrete(command: str, item_id: str) -> str:
    """The command with the item's real ID and `Claude-M5` for any placeholder."""
    number = item_id.split("-")[1]
    command = re.sub(r"\b(?:FEAT|EXP)-(?:XXX|\$ARGUMENTS)\b", item_id, command)
    command = command.replace("-$ARGUMENTS", f"-{number}").replace("$ARGUMENTS", item_id)
    return re.sub(r"<[^<>\s]+>", AGENT, command)


def run(command: str, item_id: str) -> tuple[int, str]:
    env, argv = split(concrete(command, item_id))
    result = CliRunner().invoke(main, argv, env=env or None)
    return result.exit_code, " ".join((result.output or "").split())


def claim_as_the_work_skill(theme: str, item_id: str) -> None:
    lines = literal(theme, "work", "claim")
    assert lines, f"{theme}/work prints no claim"
    code, out = run(lines[0][1], item_id)
    assert code == 0, f"{theme}/work claim `{lines[0][1]}` exited {code}: {out}"


def move_lines(theme: str, skill: str) -> list[tuple[int, str]]:
    lines = literal(theme, skill, "move")
    assert lines, f"{theme}/{skill} prints no move"
    return lines


# Each scenario is (skill, which of its move lines, in order) after the claim. The
# done skill's review and done lines are alternatives, so each gets its own board.
SCENARIOS = [
    ("done", "review"),
    ("done", "done"),
    ("blocked", "blocked"),
    ("blocked", "blocked, then unblock"),
    ("handoff", "blocked"),
]


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize(("skill", "which"), SCENARIOS, ids=[f"{s}-{w}" for s, w in SCENARIOS])
def test_skill_lines_run_after_the_work_skills_claim(
    tmp_path, monkeypatch, theme, skill, which
) -> None:
    item_id = THEMES[theme][0]
    board(tmp_path, monkeypatch, theme)
    claim_as_the_work_skill(theme, item_id)

    lines = move_lines(theme, skill)
    if which == "blocked, then unblock":
        chosen = lines  # the block, then the unblock, in document order
        assert len(chosen) >= 2, lines
    else:
        chosen = [(n, c) for n, c in lines if split(c)[1][2:3] == [which]][:1]
        assert chosen, f"{theme}/{skill} prints no `move ... {which}`: {lines}"

    for n, command in chosen:
        code, out = run(command, item_id)
        assert code == 0, (
            f"skills/{theme}/{skill}/SKILL.md:{n} `{concrete(command, item_id)}` "
            f"(git user.name {MACHINE}) exited {code}: {out}"
        )


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_reviewers_literal_done_runs_as_another_user(tmp_path, monkeypatch, theme) -> None:
    """Control for the allow-list: the reviewer's own line, with no agent."""
    item_id = THEMES[theme][0]
    work = board(tmp_path, monkeypatch, theme)
    claim_as_the_work_skill(theme, item_id)
    code, out = run(f"yurtle-kanban move {item_id} review --agent {AGENT}", item_id)
    assert code == 0, out
    _git(work, "config", "user.name", REVIEWER)

    (n, command), = move_lines(theme, "review")
    code, out = run(command, item_id)

    assert code == 0, f"skills/{theme}/review/SKILL.md:{n} `{command}` exited {code}: {out}"
