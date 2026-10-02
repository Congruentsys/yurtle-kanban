"""Issue #1237 — upgrade-check: a trailing shell comment and a quoted heredoc's
body are prose, not code.

From the #1236 r1 review and Mini's review (nusy-product-team
``scripts/tests/test_ready_by_priority.sh``): the shell rule was applied per line,
so ``x=1  # see `yurtle-kanban move …` `` read the backtick as command
substitution (high), and every line of a ``cat <<'EOF'`` usage block or a
``<<'JEOF'`` JSON fixture was scanned as code.

[steer] ruling: an unquoted `` #`` (whitespace, then ``#``) outside quotes starts
a comment; ``<<'TAG'`` / ``<<"TAG"`` / ``<<\\TAG`` heredoc bodies are literal, so
their lines are prose (low), JSON status fixtures included. An UNQUOTED ``<<TAG``
body still expands ``$(…)`` and backticks: code. A ``#`` inside quotes is not a
comment.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from yurtle_kanban.cli import main

NAUTICAL_CONFIG = """\
version: '2.0'
boards:
- name: development
  preset: nautical
  path: kanban-work/
"""


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _findings(root: Path) -> list[dict]:
    res = CliRunner().invoke(main, ["upgrade-check", str(root), "--json"])
    assert res.exit_code in (0, 1), res.output
    return json.loads(res.stdout)["findings"]


def _removed(findings: list[dict], file: str, line: int) -> list[dict]:
    return [
        f for f in findings
        if f["file"] == file and f["line"] == line and f["kind"] == "removed-form"
    ]


def test_a_trailing_comment_with_backticks_is_low(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _write(root, "s.sh", "#!/usr/bin/env bash\nx=1  # see `yurtle-kanban move X done -a Air`\n")
    findings = _findings(root)
    removed = _removed(findings, "s.sh", 2)
    assert removed, findings
    assert all(f["confidence"] == "low" for f in removed), findings
    assert all(f["confidence"] == "low" for f in findings), findings


def test_a_quoted_heredoc_usage_block_is_low(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _write(root, "usage.sh", (
        "#!/usr/bin/env bash\n"
        "usage() {\n"
        "  cat <<'EOF'\n"
        "Usage: claim.sh ID\n"
        "  Runs `yurtle-kanban move ID in_progress -a AGENT` for you.\n"
        "EOF\n"
        "}\n"
    ))
    findings = _findings(root)
    removed = _removed(findings, "usage.sh", 5)
    assert removed, findings
    assert all(f["confidence"] == "low" for f in findings), findings


def test_double_quoted_and_backslash_and_dash_heredocs_are_low(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _write(root, "h.sh", (
        "#!/usr/bin/env bash\n"
        'cat <<"EOF"\n'
        "yurtle-kanban move X done -a A\n"
        "EOF\n"
        "cat <<\\EOF\n"
        "yurtle-kanban move X done -a A\n"
        "EOF\n"
        "cat <<-'EOF'\n"
        "\tyurtle-kanban move X done -a A\n"
        "\tEOF\n"
        "yurtle-kanban move Y done -a B\n"
    ))
    findings = _findings(root)
    for line in (3, 6, 9):
        hits = _removed(findings, "h.sh", line)
        assert hits and all(f["confidence"] == "low" for f in hits), (line, findings)
    after = _removed(findings, "h.sh", 11)
    assert after and all(f["confidence"] == "high" for f in after), findings


def test_a_quoted_heredoc_json_status_fixture_is_not_high(tmp_path: Path) -> None:
    """nusy-product-team test_ready_by_priority.sh: `{"id": …, "status": "backlog"}`
    lines in a `<<'JEOF'` fixture are data, not a status check."""
    root = tmp_path / "r"
    _write(root, ".kanban/config.yaml", NAUTICAL_CONFIG)
    _write(root, "scripts/tests/fixture.sh", (
        "#!/usr/bin/env bash\n"
        "cat > \"$tmp/items.json\" <<'JEOF'\n"
        "[\n"
        '  {"id": "EXP-1", "status": "backlog", "priority": "high"},\n'
        '  {"id": "EXP-2", "status": "in_progress", "priority": "low"}\n'
        "]\n"
        "JEOF\n"
    ))
    findings = _findings(root)
    assert [f for f in findings if f["confidence"] == "high"] == [], findings


def test_an_unquoted_heredoc_substitution_is_high(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _write(root, "u.sh", (
        "#!/usr/bin/env bash\n"
        "cat <<EOF\n"
        "result: $(yurtle-kanban move X done -a A)\n"
        "EOF\n"
    ))
    findings = _findings(root)
    removed = _removed(findings, "u.sh", 3)
    assert removed and all(f["confidence"] == "high" for f in removed), findings


def test_a_hash_inside_quotes_is_not_a_comment(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _write(root, "q.sh", '#!/usr/bin/env bash\necho "#"; yurtle-kanban move X done -a A\n')
    findings = _findings(root)
    removed = _removed(findings, "q.sh", 2)
    assert removed and all(f["confidence"] == "high" for f in removed), findings


# --- r1: one shell quote-state scanner (PR #1244 review follow-ups 1, 2, 3, 5) ---


def _confidences(root: Path, file: str, line: int) -> list[str]:
    findings = _findings(root)
    hits = _removed(findings, file, line)
    assert hits, findings
    return [f["confidence"] for f in hits]


def test_a_quote_inside_substitution_inside_double_quotes_does_not_close(tmp_path: Path) -> None:
    """Follow-up 1: the `"` around ` #` is inside `$(…)`, so the outer `"…"` stays
    open and the `#` is quoted; bash runs the move."""
    root = tmp_path / "r"
    _write(root, "s.sh", (
        "#!/usr/bin/env bash\n"
        'out="$(grep " #" f)"; yurtle-kanban move X done -a A\n'
    ))
    assert set(_confidences(root, "s.sh", 2)) == {"high"}


def test_an_escaped_quote_in_an_ansi_c_string_does_not_close(tmp_path: Path) -> None:
    """Follow-up 2: in `$'…'` a `\\'` is an escaped quote, so the `#` is quoted."""
    root = tmp_path / "r"
    _write(root, "s.sh", (
        "#!/usr/bin/env bash\n"
        "echo $'it\\'s #'; yurtle-kanban move X done -a A\n"
    ))
    assert set(_confidences(root, "s.sh", 2)) == {"high"}


def test_a_heredoc_operator_inside_quotes_starts_no_heredoc(tmp_path: Path) -> None:
    """Follow-up 3: both quote counts before `<<` are even, but the `<<` is inside
    `'…'`; no heredoc starts, so the next line's move is code."""
    root = tmp_path / "r"
    _write(root, "s.sh", (
        "#!/usr/bin/env bash\n"
        "echo \"don't\" 'x <<\"EOF\"'\n"
        "yurtle-kanban move X done -a A\n"
    ))
    assert set(_confidences(root, "s.sh", 3)) == {"high"}


def test_a_comment_after_a_semicolon_is_low(tmp_path: Path) -> None:
    """Follow-up 5: bash starts a comment at a `#` after `;` (also `&`, `|`, `(`, `)`)."""
    root = tmp_path / "r"
    _write(root, "s.sh", "#!/usr/bin/env bash\nx=1;# `yurtle-kanban move X done -a A`\n")
    assert set(_confidences(root, "s.sh", 2)) == {"low"}


def test_an_arithmetic_shift_starts_no_heredoc(tmp_path: Path) -> None:
    """Follow-up 4: `$((1<<2))` is a shift, not a heredoc with tag `2`; a later
    `<<'EOF'` usage block is still detected and low."""
    root = tmp_path / "r"
    _write(root, "s.sh", (
        "#!/usr/bin/env bash\n"
        "echo $((1<<2))\n"
        "cat <<'EOF'\n"
        "  Runs `yurtle-kanban move ID in_progress -a AGENT` for you.\n"
        "EOF\n"
    ))
    assert set(_confidences(root, "s.sh", 4)) == {"low"}
