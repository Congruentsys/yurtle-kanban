"""Issue #899 — gaps in the doc/skill command checker and the Quick Start runner.

Found in the #897 (#861) review:

1. ``_flags`` does not see a bracketed optional flag (``[--priority <p>]``), so a
   misspelt ``[--bogus <x>]`` gets through — in docs and skills alike.
2. The ``HEREDOC`` regex also matches a here-string (``<<<"word"``) or a ``<<EOF``
   inside quotes, which silently skips the rest of the block.
3. The Quick Start runner's ``_env`` passes through an inherited ``GIT_DIR`` /
   ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` (e.g. under a git hook). It should drop
   every ``GIT_*`` variable it does not set itself.
"""

from __future__ import annotations

import os

import pytest

from tests.issues.test_861_readme_quick_start_runs import _env
from tests.test_skill_commands_execute import (
    _block_commands,
    _commands_in,
    _flags,
    _rejection,
)

BOGUS_LINE = 'yurtle-kanban create feature "T" --push [--priority <p>] [--bogus <x>]'
GOOD_BRACKETED = 'yurtle-kanban create feature "T" --push [--priority <p>]'
README_LINE = 'yurtle-kanban create <type> "<title>" --push [--priority <p>] [--assign <name>]'


def _only_command(line):
    commands = _commands_in(line)
    assert len(commands) == 1, commands
    return commands[0]


def _doc_rejections(block):
    """(text, rejection) for every yurtle-kanban command the bash-block path sees."""
    return [
        (text, _rejection(*words))
        for _, text in _block_commands(block, 1)
        for words in _commands_in(text)
    ]


# --- 1. bracketed optional flags ---------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        (BOGUS_LINE, {"--push", "--priority", "--bogus"}),
        (GOOD_BRACKETED, {"--push", "--priority"}),
        (README_LINE, {"--push", "--priority", "--assign"}),
    ],
)
def test_bracketed_optional_flags_are_seen(line, expected):
    flags = set(_flags(_only_command(line)))
    assert expected <= flags, f"missing {sorted(expected - flags)} from {sorted(flags)}"


def test_bracketed_bogus_flag_is_rejected():
    problem = _rejection(*_only_command(BOGUS_LINE))
    assert problem is not None, "`[--bogus <x>]` on `create` was accepted"
    assert "--bogus" in problem, problem


def test_bracketed_bogus_flag_is_rejected_in_a_skill_backtick_span():
    line = f"Run `{BOGUS_LINE}` to file it."
    problems = [_rejection(*words) for words in _commands_in(line)]
    assert problems and all(p is not None and "--bogus" in p for p in problems), problems


def test_bracketed_bogus_flag_is_rejected_in_a_doc_bash_block():
    results = _doc_rejections(["# file it", BOGUS_LINE])
    assert results, "the bash-block path extracted nothing"
    assert all(p is not None and "--bogus" in p for _, p in results), results


@pytest.mark.parametrize("line", [GOOD_BRACKETED, README_LINE])
def test_bracketed_real_flags_are_accepted(line):
    """Negative control: README's real synopsis line names only real `create` flags."""
    assert _rejection(*_only_command(line)) is None
    assert all(p is None for _, p in _doc_rejections([line]))


# --- 2. here-strings and quoted `<<EOF` are not heredocs ---------------------------


@pytest.mark.parametrize(
    "opener",
    [
        'cat <<<"word"',
        "cat <<< word",
        'cmd <<< "$x"',
        'echo "a <<EOF b"',
        "echo 'a <<EOF b'",
    ],
)
def test_not_a_heredoc_keeps_the_following_lines(opener):
    block = [opener, "yurtle-kanban --bogus", "EOF", "yurtle-kanban board"]
    texts = [text for _, text in _block_commands(block, 1)]
    assert "yurtle-kanban --bogus" in texts, texts
    assert "yurtle-kanban board" in texts, texts


@pytest.mark.parametrize("opener", ['cat <<<"word"', 'echo "a <<EOF b"'])
def test_bad_command_after_a_non_heredoc_is_still_checked(opener):
    results = _doc_rejections([opener, "yurtle-kanban list --bogus"])
    assert any(p is not None and "--bogus" in p for _, p in results), results


@pytest.mark.parametrize("opener", ["cat <<EOF", "cat <<'EOF'", 'cat <<"EOF"', "cat << EOF"])
def test_real_heredoc_body_is_still_skipped(opener):
    """Negative control: a real heredoc body is data, not commands."""
    block = [opener, "yurtle-kanban --bogus", "EOF", "yurtle-kanban board"]
    texts = [text for _, text in _block_commands(block, 1)]
    assert texts == [opener, "yurtle-kanban board"], texts


# --- 3. the Quick Start runner drops inherited GIT_* --------------------------------

INHERITED = {
    "GIT_DIR": "/elsewhere/.git",
    "GIT_WORK_TREE": "/elsewhere",
    "GIT_INDEX_FILE": "/elsewhere/.git/index",
    "GIT_FOO": "bar",
    "GIT_AUTHOR_NAME": "hook-author",
}


def test_env_drops_inherited_git_variables(monkeypatch, tmp_path):
    for key, value in INHERITED.items():
        monkeypatch.setenv(key, value)
    env = _env(tmp_path)
    leaked = sorted(k for k in INHERITED if k in env)
    assert leaked == [], f"inherited GIT_* passed through: {leaked}"


def test_env_keeps_the_git_variables_it_sets(monkeypatch, tmp_path):
    for key, value in INHERITED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/inherited/gitconfig")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    env = _env(tmp_path)
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["HOME"] == str(tmp_path)


def test_env_keeps_non_git_variables(monkeypatch, tmp_path):
    """Control: only GIT_* is dropped — the rest of the environment passes through."""
    monkeypatch.setenv("YK899_UNRELATED", "kept")
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    assert _env(tmp_path)["YK899_UNRELATED"] == "kept"
