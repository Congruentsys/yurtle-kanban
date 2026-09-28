"""Issue #861: the README's Quick Start runs, top to bottom, and every command exits 0.

The static checks (tests/test_skill_commands_execute.py) prove each flag exists. They
cannot see an order that the board refuses: before #857 the Quick Start moved a
freshly created item, which `create` puts in backlog, straight to ``in_progress``,
and asked for a ``research`` board that ``init --theme software`` never makes. Every
flag was real; the commands failed. So this runs the block itself.

The block is taken verbatim from README.md's "## Quick Start" ```bash fence, with
``cd my-project`` replaced by ``:`` so script lines keep README's numbering. It runs
under bash in a scratch repo whose ``origin`` is a local bare repo holding one pushed
commit (``create --push`` pushes). An ERR trap records every command that exits
non-zero, with its README line, and the run carries on so one failure hides no other.

Hermetic: HOME is a scratch dir, global and system git config are off, YURTLE_AGENT
is unset, and the repo has its own user.name. ``.kanban/hooks`` is deleted after
``init`` if it ever creates one, so no hook reaches the network.

Allow-list (commands skipped, each with its reason): SKIPPED, below. It is empty
today; a command belongs there only if it needs the network or is otherwise
inherently non-hermetic.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from tests.test_skill_commands_execute import _shell_code

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"
SRC = REPO / "src"

# README command text -> why it is not run here. Keep this minimal.
SKIPPED: dict[str, str] = {}

FAIL = re.compile(r"^QUICKSTART-FAIL rc=(\d+) line=(\d+): (.*)$", re.MULTILINE)


def quick_start(text: str) -> tuple[int, list[str]]:
    """(README line of the first body line, body lines) of the Quick Start bash block."""
    lines = text.splitlines()
    heading = lines.index("## Quick Start")
    for i in range(heading + 1, len(lines)):
        if lines[i].startswith("## "):
            break
        if lines[i].strip() == "```bash":
            end = next(j for j in range(i + 1, len(lines)) if lines[j].strip() == "```")
            return i + 2, lines[i + 1 : end]
    raise AssertionError("README.md: no ```bash block under ## Quick Start")


def script(body: list[str]) -> str:
    """The bash script: a header, then the block with README's line numbering kept."""
    # Three header lines, so README body line k is script line HEADER + k.
    header = [
        "HEADER=3",
        "trap 'echo \"QUICKSTART-FAIL rc=$? line=$((LINENO - HEADER)): $BASH_COMMAND\" >&2' ERR",
        "clean_hooks() { rm -rf .kanban/hooks; }",
    ]
    lines = []
    for line in body:
        stripped = line.strip()
        if stripped == "cd my-project":
            line = ":"
        elif stripped in SKIPPED:
            line = ":  # skipped: " + SKIPPED[stripped].replace("\n", " ")
        elif stripped.startswith("yurtle-kanban init"):
            line = _shell_code(line)[0] + "; clean_hooks"  # before any `# comment`
        lines.append(line)
    return "\n".join([*header, *lines]) + "\n"


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "YURTLE_AGENT"}
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        PYTHONPATH=os.pathsep.join(filter(None, [str(SRC), env.get("PYTHONPATH")])),
        PATH=os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")]),
        NO_COLOR="1",
    )
    return env


def run_quick_start(tmp_path: Path, readme_text: str) -> tuple[subprocess.CompletedProcess, int]:
    first, body = quick_start(readme_text)
    home = tmp_path / "home"
    home.mkdir()
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    env = _env(home)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=env)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True, env=env)
    for key, value in (
        ("user.name", "quickstart-user"),
        ("user.email", "q@test.com"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "config", key, value], cwd=work, check=True, env=env)
    for args in (
        ("commit", "-q", "--allow-empty", "-m", "initial"),
        ("remote", "add", "origin", str(origin)),
        ("push", "-q", "-u", "origin", "main"),
    ):
        subprocess.run(["git", *args], cwd=work, check=True, env=env, capture_output=True)
    text = script(body)
    result = subprocess.run(
        ["bash", "-c", text],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=300,
    )
    return result, first


def failures(result: subprocess.CompletedProcess, first: int) -> list[str]:
    return [
        f"README.md:{first + int(line) - 1} exited {rc}: {cmd}"
        for rc, line, cmd in FAIL.findall(result.stderr)
    ]


def test_quick_start_is_found() -> None:
    first, body = quick_start(README.read_text(encoding="utf-8"))
    commands = [line for line in body if line.startswith("yurtle-kanban ")]
    assert commands[0].startswith("yurtle-kanban init"), commands[:1]
    assert any(" create " in f" {c} " for c in commands), commands
    assert any(c.startswith("yurtle-kanban move ") for c in commands), commands
    assert len(commands) >= 20, commands


def test_the_trap_reports_a_failing_command(tmp_path: Path) -> None:
    """Proof the harness has teeth: a bogus line in the block is reported, by README
    line, and the run continues past it."""
    readme = README.read_text(encoding="utf-8")
    first, body = quick_start(readme)
    board = body.index("yurtle-kanban board")
    planted = body[:board] + ["yurtle-kanban move FEAT-999 done"] + body[board + 1 :]
    result, first = run_quick_start(tmp_path, readme.replace("\n".join(body), "\n".join(planted)))
    found = failures(result, first)
    assert found == [f"README.md:{first + board} exited 1: yurtle-kanban move FEAT-999 done"], (
        result.stderr
    )


def test_readme_quick_start_runs(tmp_path: Path) -> None:
    result, first = run_quick_start(tmp_path, README.read_text(encoding="utf-8"))
    found = failures(result, first)
    assert not found, (
        "README Quick Start commands failed when run top to bottom:\n"
        + "\n".join(found)
        + f"\n--- stderr ---\n{result.stderr[-4000:]}"
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "work" / ".kanban" / "hooks").exists()
