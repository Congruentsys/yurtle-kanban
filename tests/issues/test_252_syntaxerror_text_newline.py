"""#252: a real SyntaxError's source line shows no literal ``\\n`` in the log.

Decided behaviour:

1. A real compile-time SyntaxError (its ``text`` ends in a newline) logged via a
   package logger's ``.exception(...)`` renders exactly as the standard traceback
   would: the logged traceback equals ``"".join(traceback.format_exception(...))``
   of the same exception when everything is printable -- no literal ``\\n`` at the
   end of the source line.
2. A SyntaxError whose ``text`` has a raw ESC in the middle and a trailing newline:
   the ESC is escaped (a literal ``\\x1b``), and the trailing newline is NOT shown
   as a literal ``\\n``.
"""

from __future__ import annotations

import io
import logging
import traceback
from collections.abc import Iterator

import pytest

import yurtle_kanban._logging  # noqa: F401  (installs the escaping record factory)

LOGGER_NAME = "yurtle-kanban.zz_test_252"


@pytest.fixture
def captured() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """A package logger with a capturing ``%(message)s`` handler; propagation off."""
    logger = logging.getLogger(LOGGER_NAME)
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


def _standard(exc: BaseException) -> str:
    # three-argument form: works on Python 3.10 as well
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _source_lines(text: str, start: str) -> list[str]:
    """The traceback's SyntaxError source line(s): indented, starting with ``start``
    (so the frame line quoting the test's own ``raise`` / ``compile`` is excluded)."""
    return [line for line in text.splitlines() if line.lstrip().startswith(start)]


class TestRealSyntaxError:
    def test_matches_standard_traceback(self, captured: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = captured
        try:
            compile("x = (", "f.py", "exec")
        except SyntaxError as exc:
            assert isinstance(exc.text, str) and exc.text.endswith("\n")
            expected = _standard(exc)
            logger.exception("failed")
        else:  # pragma: no cover
            pytest.fail("compile did not raise")
        assert stream.getvalue() == "failed\n" + expected, repr(stream.getvalue())

    def test_no_literal_newline_escape_on_source_line(
        self, captured: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = captured
        try:
            compile("x = (", "f.py", "exec")
        except SyntaxError:
            logger.exception("failed")
        text = stream.getvalue()
        lines = _source_lines(text, "x = (")
        assert lines, repr(text)
        for line in lines:
            assert "\\n" not in line, repr(line)


class TestEscAndTrailingNewline:
    def test_esc_escaped_trailing_newline_not_literal(
        self, captured: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = captured
        try:
            raise SyntaxError("bad", ("f.py", 1, 1, "src\x1bline\n"))
        except SyntaxError:
            logger.exception("failed")
        text = stream.getvalue()
        assert "\x1b" not in text, repr(text)
        lines = _source_lines(text, "src")
        assert lines, repr(text)
        for line in lines:
            assert not line.rstrip().endswith("\\n"), repr(line)
            assert "\\n" not in line, repr(line)
        assert "SyntaxError: bad" in text
