"""Package loggers whose warnings can't smuggle control characters (#215).

Warnings interpolate text from repo files (hook definitions, item titles). With no
handler configured, Python's last-resort handler writes them straight to stderr,
so an ESC sequence or newline in a cloned repo's file could clear the screen or
forge log lines. Every logger here escapes non-printable characters in WARNING
and higher records; DEBUG and INFO are left alone (the SPARQL debug log is
multi-line on purpose).
"""

from __future__ import annotations

import logging


def escape_nonprintable(text: str) -> str:
    """Printable text as is; any other character as its repr escape (`\\x1b`)."""
    if text.isprintable():
        return text
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


class _EscapeControlChars(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            message = record.getMessage()
            if not message.isprintable():
                record.msg, record.args = escape_nonprintable(message), None
        return True


def get_logger(name: str) -> logging.Logger:
    """`logging.getLogger(name)`, with the control-character filter attached once."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, _EscapeControlChars) for f in logger.filters):
        logger.addFilter(_EscapeControlChars())
    return logger
