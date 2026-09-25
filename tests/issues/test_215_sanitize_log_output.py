"""#215: log output never puts raw control characters on the terminal.

Decided behaviour: every logger the package creates ("yurtle-kanban",
"yurtle-kanban.hooks", "yurtle-kanban.gates", "yurtle-kanban.workflow",
"yurtle-kanban-mcp") carries a filter so that a record at WARNING or above has its
final message (after %-formatting or an f-string) rendered with every non-printable
character escaped repr-style inside the text (ESC -> `\\x1b`, newline -> `\\n`,
CR -> `\\r`, U+2028 -> `\\u2028`). Printable text, including non-ASCII such as
"café", is unchanged. DEBUG and INFO records are untouched (the SPARQL debug log is
intentionally multi-line).

End to end: a cloned repo's hooks file (`.kanban/hooks/kanban-hooks.yurtle.md`) whose
`create_item` action or action `type` carries `\\e[2J\\n...` must not clear the screen
or forge a log line on stderr when running `yurtle-kanban create expedition probe`.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import yurtle_kanban
import yurtle_kanban.gates
import yurtle_kanban.hooks
import yurtle_kanban.query
import yurtle_kanban.service
import yurtle_kanban.workflow

LOGGER_NAMES = [
    "yurtle-kanban",
    "yurtle-kanban.hooks",
    "yurtle-kanban.gates",
    "yurtle-kanban.workflow",
    "yurtle-kanban-mcp",
]


@pytest.fixture(autouse=True, scope="module")
def _import_mcp_server() -> None:
    """The "yurtle-kanban-mcp" logger is created when the MCP server module loads."""
    pytest.importorskip("yurtle_kanban.mcp.server")


@pytest.fixture(params=LOGGER_NAMES)
def captured(request: pytest.FixtureRequest) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """Attach a StringIO handler (format: bare message) to one package logger."""
    logger = logging.getLogger(request.param)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.DEBUG)
    old_level, old_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield logger, stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def _message(stream: io.StringIO) -> str:
    """The captured text with only the handler's own terminator newline removed."""
    text = stream.getvalue()
    assert text.endswith("\n"), repr(text)
    return text[:-1]


class TestWarningMessagesEscaped:
    def test_percent_args_escaped(self, captured: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = captured
        logger.warning("title %s", "a\x1b[2J\nFAKE")
        msg = _message(stream)
        assert "\x1b" not in msg and "\n" not in msg, repr(msg)
        assert msg == "title a\\x1b[2J\\nFAKE", repr(msg)

    def test_fstring_escaped(self, captured: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = captured
        value = "a\x1bb"
        logger.warning(f"title {value}")
        msg = _message(stream)
        assert "\x1b" not in msg, repr(msg)
        assert msg == "title a\\x1bb", repr(msg)

    def test_cr_and_line_separator_escaped(
        self, captured: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = captured
        logger.error("x\ry z")
        msg = _message(stream)
        assert "\r" not in msg and " " not in msg, repr(msg)
        assert msg == "x\\ry\\u2028z", repr(msg)


class TestControls:
    def test_debug_untouched(self, captured: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = captured
        logger.debug("q\n%s", "SELECT\n")
        assert stream.getvalue() == "q\nSELECT\n\n"

    def test_info_untouched(self, captured: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = captured
        logger.info("line1\nline2")
        assert stream.getvalue() == "line1\nline2\n"

    def test_printable_non_ascii_unchanged(
        self, captured: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = captured
        logger.warning("café ☕ ok")
        assert _message(stream) == "café ☕ ok"


# ─── End to end: a hooks file in a cloned repo ─────────────────────────────


_HOOKS = """\
---
type: kanban-hooks
version: 1
hooks:
  on_create:
    - item_types: [expedition]
      actions:
        - type: create_item
          item_type: "bogus\\e[2J\\nWARNING: FAKE ITEM TYPE"
          title: "t\\e[2J\\nWARNING: FAKE TITLE"
        - type: "zap\\e[2J\\nWARNING: FAKE ACTION TYPE"
---
# hooks
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)


@pytest.fixture(scope="module")
def e2e_stderr(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    root = tmp_path_factory.mktemp("repo215")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    (root / ".kanban" / "hooks").mkdir(parents=True)
    (root / "kanban-work" / "expeditions").mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(
        "theme: nautical\npaths:\n  root: kanban-work/\n"
        "  scan_paths: [kanban-work/expeditions/]\n"
    )
    (root / ".kanban" / "hooks" / "kanban-hooks.yurtle.md").write_text(_HOOKS)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")

    src = str(Path(yurtle_kanban.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
    r = subprocess.run(
        [sys.executable, "-m", "yurtle_kanban.cli", "create", "expedition", "probe"],
        cwd=root, capture_output=True, env=env, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    # The hook actually ran and warned (guards against a vacuous pass).
    assert b"Hook create_item failed" in r.stderr, r.stderr
    assert b"Unknown action type" in r.stderr, r.stderr
    return r.stderr


class TestHooksEndToEnd:
    def test_no_raw_escape_on_stderr(self, e2e_stderr: bytes) -> None:
        assert b"\x1b" not in e2e_stderr, e2e_stderr
        assert b"\\x1b[2J" in e2e_stderr, e2e_stderr

    def test_no_forged_log_line(self, e2e_stderr: bytes) -> None:
        lines = e2e_stderr.decode("utf-8", "replace").splitlines()
        forged = [line for line in lines if line.startswith("WARNING: FAKE")]
        assert forged == [], e2e_stderr

    @pytest.mark.parametrize(
        ("prefix", "tail"),
        [
            ("Hook create_item failed", "WARNING: FAKE ITEM TYPE"),
            ("Unknown action type", "WARNING: FAKE ACTION TYPE"),
        ],
    )
    def test_each_warning_is_one_line(self, e2e_stderr: bytes, prefix: str, tail: str) -> None:
        lines = e2e_stderr.decode("utf-8", "replace").splitlines()
        hits = [line for line in lines if prefix in line]
        assert len(hits) == 1, e2e_stderr
        assert tail in hits[0], e2e_stderr
