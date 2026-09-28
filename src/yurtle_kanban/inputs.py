"""Who is acting, and the free text they pass (#580).

- **Actor** — who performed an action (a comment's author, `kb:by` on a status
  change). One resolver, `resolve_actor`: the explicit `--agent` value, then
  `$YURTLE_AGENT`, then git `user.name`, then an error. There is no `"cli"`,
  `"agent"` or `"unknown"` default.
- **Assignee** — who holds an item. Never defaulted: it changes only through an
  explicit `--assign`. `check_identity` validates it like any identity.
- **Free text** — every free-text `--X` has a `--X-file PATH|-` twin, and both
  are read by `read_text_option`, in the CLI layer, before any subprocess runs.

Every refusal is an `InputRefused` (a `ValueError`) naming the flag or variable;
the CLI prints it and exits 1 (#666).
"""

from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import click

from .models import InputRefused

AGENT_ENV = "YURTLE_AGENT"


# every git call the code parses: no credential prompt, and git's messages in
# English whatever the user's locale, since the code matches them (#806)
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "LANGUAGE": ""}


def check_identity(value: str, name: str) -> str:
    """`value` stripped; refused (naming `name`) when empty, whitespace-only, or
    holding a control character (Unicode category `Cc`: `\\n`, `\\r`, `\\t`, NUL,
    DEL, NEL…), which would break a `### author` heading or a TTL literal."""
    stripped = value.strip()
    if not stripped:
        raise InputRefused(f"{name} is empty: give a name")
    bad = next((ch for ch in stripped if unicodedata.category(ch) == "Cc"), None)
    if bad is not None:
        raise InputRefused(f"{name} contains a control character ({bad!r}): give a plain name")
    return stripped


def _git_user_name(cwd: Path | str | None) -> str | None:
    """git `user.name` as seen from `cwd`, or None when unset, empty or unreadable."""
    try:
        done = subprocess.run(
            ["git", "config", "user.name"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **GIT_ENV},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = done.stdout.strip() if done.returncode == 0 else ""
    return name or None


def resolve_actor(
    explicit: str | None,
    *,
    allow_git_fallback: bool = True,
    cwd: Path | str | None = None,
    flag: str = "--agent",
) -> str:
    """Who is acting: `explicit` (the `--agent` flag), then `$YURTLE_AGENT`, then
    git `user.name` (only when `allow_git_fallback`), else an `InputRefused`.

    `$YURTLE_AGENT` set but blank is an error, not "unset". Every session on one
    machine shares git `user.name`, so coordination verbs pass
    `allow_git_fallback=False`. `cwd` is where git looks for its config (default:
    the current directory). `flag` names the option in an error (#660)."""
    if explicit is not None:
        return check_identity(explicit, flag)
    env = os.environ.get(AGENT_ENV)
    if env is not None:
        return check_identity(env, f"${AGENT_ENV}")
    if allow_git_fallback:
        name = _git_user_name(cwd)
        if name is not None:
            return check_identity(name, "git user.name")
        raise InputRefused(
            f"No actor: set --agent or {AGENT_ENV} (git user.name is not set either)"
        )
    raise InputRefused(f"No actor: set --agent or {AGENT_ENV}")


def same_actor(a: str, b: str) -> bool:
    """Whether two identities name the same actor (surrounding space and case ignored)."""
    return a.strip().casefold() == b.strip().casefold()


def _read_source(file: str, name: str) -> bytes:
    """The raw bytes of `--{name}-file` (`-` = stdin, refused on a terminal)."""
    if file == "-":
        stdin = sys.stdin
        if stdin is None or stdin.isatty():
            raise InputRefused(
                f"--{name}-file -: stdin is a terminal; pipe the text "
                "(a quoted heredoc: <<'EOF') or pass a path"
            )
        buffer = getattr(stdin, "buffer", None)
        return buffer.read() if buffer is not None else stdin.read().encode("utf-8")
    try:
        return Path(file).read_bytes()
    except OSError as e:
        raise InputRefused(f"--{name}-file: can't read {file}: {e.strerror or e}") from None


def read_text_option(
    text: str | None,
    file: str | None,
    name: str,
    *,
    required: bool = False,
) -> str | None:
    """The text of a `--{name}` / `--{name}-file PATH|-` pair.

    Both given, or (when `required`) neither, is a click usage error. The file or
    stdin is read whole, as strict UTF-8; CRLF/CR become LF and trailing newlines
    are dropped; text that is then empty or whitespace-only is refused. Nothing
    else changes: no shell, no expansion. Returns None when neither was given."""
    if text is not None and file is not None:
        raise click.UsageError(f"--{name} and --{name}-file are mutually exclusive: give one")
    if text is None and file is None:
        if required:
            raise click.UsageError(f"Give the text with --{name} TEXT or --{name}-file PATH|-")
        return None
    if file is not None:
        raw = _read_source(file, name)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            source = "stdin" if file == "-" else file
            raise InputRefused(
                f"--{name}-file: {source} is not valid UTF-8 (byte {e.start})"
            ) from None
        source_name = f"--{name}-file"
    else:
        source_name = f"--{name}"
    assert text is not None
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if not text.strip():
        raise InputRefused(f"{source_name} is empty: give some text")
    return text
