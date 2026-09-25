"""Issue #202 — `LineEndings.apply` follow-ups from the #181 review.

1. No-overlap bound (controls, green now): the unchanged suffix must never reuse
   a line the unchanged prefix already mapped, or one old ending is copied onto
   two new lines. `"a\\r\\nb\\n"` edited to `"a\\na\\nb\\n"` has exactly one CRLF.
2. Worst case (red now): a mixed-ending file of identical lines whose changed
   middle is large (lines 10 and n-10 edited) must still be fast. Run in a
   subprocess with a hard timeout; a TimeoutExpired is a failure.
3. Round trip (red now): the output, re-read, must give back the edited LF text.
   A kept lone `\\r` followed by an inserted blank line that takes `\\n` becomes
   `\\r\\n`, which re-reads as one line, so the blank line is lost.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from yurtle_kanban.service import LineEndings

SRC = Path(__file__).resolve().parents[2] / "src"
HERE = Path(__file__).resolve().parent
BOUND = 2.0  # seconds, per read()+apply()
TIMEOUT = 10  # seconds, for the whole subprocess

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def raw_of(pairs: list[tuple[str, str]]) -> str:
    """Raw file text from (line, ending) pairs; the ending may be ""."""
    return "".join(line + end for line, end in pairs)


def roundtrip(raw: str, edited: str) -> str:
    """read(raw), then apply() the already-edited LF text."""
    _, eol = LineEndings.read(raw)
    return eol.apply(edited)


def endings_of(raw: str) -> list[str]:
    return re.split(r"(\r\n|\r|\n)", raw)[1::2]


# ---------------------------------------------------------------------------
# 1. No-overlap bound (controls: pass now and after)
# ---------------------------------------------------------------------------


def test_duplicate_inserted_after_crlf_line_gets_one_crlf() -> None:
    out = roundtrip("a\r\nb\n", "a\na\nb\n")
    assert out.count("\r\n") == 1
    text, _ = LineEndings.read(out)
    assert text.split("\n") == ["a", "a", "b", ""]
    assert out in ("a\r\na\nb\n", "a\na\r\nb\n")


def test_duplicate_inserted_before_final_crlf_line_gets_one_crlf() -> None:
    out = roundtrip("a\nb\r\n", "a\nb\nb\n")
    assert out.count("\r\n") == 1
    text, _ = LineEndings.read(out)
    assert text.split("\n") == ["a", "b", "b", ""]


def test_identical_lines_one_inserted_keeps_endings_count() -> None:
    # 2 "x" lines, the first CRLF; a third "x" added — still exactly one CRLF
    out = roundtrip("x\r\nx\n", "x\nx\nx\n")
    assert out.count("\r\n") == 1
    assert out.replace("\r\n", "\n") == "x\nx\nx\n"


# ---------------------------------------------------------------------------
# 2. Large changed middle of identical lines (red on main: quadratic)
# ---------------------------------------------------------------------------


def big_identical(n: int) -> list[tuple[str, str]]:
    """`n` lines of "same" + LF, with CRLF sprinkled (incl. line n-10 and the last)."""
    pairs = [("same", "\n")] * n
    for i in (3, 9, 11, n // 3, n // 2, n - 11, n - 10, n - 1):
        pairs[i] = ("same", "\r\n")
    return pairs


def _timed_case(n_str: str) -> None:
    """Subprocess entry: edit lines 10 and n-10 of an n-line file, check bytes."""
    n = int(n_str)
    pairs = big_identical(n)
    raw = raw_of(pairs)
    lines = ["same"] * n + [""]
    lines[10] = "changed A"
    lines[n - 10] = "changed B"
    edited = "\n".join(lines)
    expected = list(pairs)
    expected[10] = ("changed A", "\n")  # was LF: majority LF
    expected[n - 10] = ("changed B", "\n")  # was CRLF: takes majority LF
    start = time.perf_counter()
    out = roundtrip(raw, edited)
    elapsed = time.perf_counter() - start
    assert out == raw_of(expected), "wrong bytes"
    print(f"ELAPSED {elapsed:.3f}")


def run_timed(n: int) -> float:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC), str(HERE)])}
    code = (
        "import sys; from test_202_lineendings_followups import _timed_case; "
        "_timed_case(sys.argv[1])"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, str(n)],
            env=env, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{n}-line identical mixed file took over {TIMEOUT}s (bound {BOUND}s)")
    assert proc.returncode == 0, proc.stderr
    return float(proc.stdout.split("ELAPSED")[-1])


@pytest.mark.parametrize("n", [20_000, 50_000])
def test_large_changed_middle_of_identical_lines_is_fast(n: int) -> None:
    elapsed = run_timed(n)
    assert elapsed < BOUND, f"{n} lines: {elapsed:.2f}s >= {BOUND}s"


# ---------------------------------------------------------------------------
# 3. The LF content round-trips (red on main: lone `\r` + inserted blank line)
# ---------------------------------------------------------------------------


def test_kept_lone_cr_then_inserted_blank_line_survives() -> None:
    edited = "a\n\nb\nc\n"
    out = roundtrip("a\rb\r\nc\n", edited)
    text, _ = LineEndings.read(out)
    assert text.split("\n") == ["a", "", "b", "c", ""]
    assert text == edited
    # no line's `\r` directly followed by the next line's `\n`
    assert not out.startswith("a\r\nb")


LINES = ["a", "a", "b", "", "", "c"]
ENDINGS = ["\n", "\r\n", "\r"]


def _random_raw(rng: random.Random) -> str:
    n = rng.randint(1, 8)
    pairs = [(rng.choice(LINES), rng.choice(ENDINGS)) for _ in range(n)]
    if rng.random() < 0.3:
        pairs[-1] = (pairs[-1][0], "")
    return raw_of(pairs)


def _random_edit(rng: random.Random, text: str) -> str:
    lines = text.split("\n")
    for _ in range(rng.randint(1, 3)):
        op = rng.choice(["insert", "delete", "replace"])
        i = rng.randint(0, len(lines) - 1)
        if op == "insert":
            lines.insert(i, rng.choice(LINES))
        elif op == "delete" and len(lines) > 1:
            del lines[i]
        else:
            lines[i] = rng.choice(LINES)
    return "\n".join(lines)


def test_random_edits_roundtrip_the_lf_text() -> None:
    rng = random.Random(202)
    failures = []
    for _ in range(400):
        raw = _random_raw(rng)
        text, eol = LineEndings.read(raw)
        edited = _random_edit(rng, text)
        out = eol.apply(edited)
        if LineEndings.read(out)[0] != edited:
            failures.append((raw, edited, out))
            continue
        # every ending is a real one, and comes from the old file or the majority
        assert set(endings_of(out)) <= {"\n", "\r\n", "\r"}
        assert set(endings_of(out)) <= set(endings_of(raw)) | {eol.majority}, (raw, out)
    assert not failures, f"{len(failures)} lost lines, e.g. {failures[:3]!r}"
