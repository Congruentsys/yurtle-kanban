"""Issue #213 — `LineEndings.apply`: an anchor-work budget and a smaller difflib budget.

Follow-ups from round 2 of the PR #205 review (#202).

1. Quality (red now): the anchor split runs only once, so a large gap of repeated
   content with edits at both ends maps position for position and loses its
   endings. Unique anchors every 1001 lines; every gap holds the SAME 1000 lines
   (unique inside the gap, repeated across gaps); each gap gets a line inserted
   at its start and its last line deleted. At least 99% of the unchanged lines
   that were CRLF must keep CRLF (today 16 of about 16.7k).
2. Constant (red now): many gaps of identical lines just under the 250k-pair
   difflib bound (498-line gaps edited at both ends) are linear but slow. The
   bound here is tighter than the review's 1.5 s because this Mac mini already
   runs 50k lines in about 1.2 s (the reviewer measured 2.3 s); a 50k-pair
   segment budget gives about 5x less.
3. Staircase guard (control): the #202 staircase at 50k stays fast and flat.
4. Invariants (control): with a tiny difflib budget, random small edits
   round-trip, and every kept non-majority ending sits on an unchanged line.

Timing cases run in a subprocess with a hard timeout; a TimeoutExpired fails.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from yurtle_kanban.service import LineEndings

SRC = Path(__file__).resolve().parents[2] / "src"
HERE = Path(__file__).resolve().parent
TIMEOUT = 10  # seconds, for the whole subprocess

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def mixed(lines: list[str]) -> tuple[str, list[str]]:
    """(raw, endings): every 3rd line CRLF, the rest LF (LF is the majority)."""
    ends = ["\r\n" if i % 3 == 0 else "\n" for i in range(len(lines))]
    return "".join(line + end for line, end in zip(lines, ends)), ends


def endings_of(raw: str) -> list[str]:
    return re.split(r"(\r\n|\r|\n)", raw)[1::2]


def roundtrip(raw: str, edited: str) -> str:
    _, eol = LineEndings.read(raw)
    return eol.apply(edited)


def run_case(case: str, arg: int) -> str:
    """Run `case(arg)` from this module in a fresh interpreter; return its stdout."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC), str(HERE)])}
    code = f"import sys; from test_213_anchor_budget import {case}; {case}(sys.argv[1])"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, str(arg)],
            env=env, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{case}({arg}) took over {TIMEOUT}s")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def field(out: str, name: str) -> float:
    return float(out.split(f"{name} ")[-1].split()[0])


# ---------------------------------------------------------------------------
# 1. Repeated gaps with edits at both ends (red now: 16/16.7k CRLF kept)
# ---------------------------------------------------------------------------

GAP = 1000
QUALITY_BOUND = 2.0  # seconds
QUALITY_MIN = 0.99  # share of unchanged CRLF lines that keep CRLF


def repeated_gaps(blocks: int) -> tuple[str, str, list[str], list[tuple[int, int]]]:
    """(raw, edited, old endings, unchanged (old i, new j) pairs).

    Old: `blocks` times [unique anchor, g0 .. g999]. New: in every gap a line
    is inserted before g0 and g999 is deleted, so the gap shifts by one line.
    """
    gap = [f"g{k}" for k in range(GAP)]
    old: list[str] = []
    new: list[str] = []
    kept: list[tuple[int, int]] = []
    for b in range(blocks):
        kept.append((len(old), len(new)))
        old.append(f"anchor {b}")
        new.append(f"anchor {b}")
        new.append(f"inserted {b}")
        for k in range(GAP - 1):
            kept.append((len(old) + k, len(new)))
            new.append(gap[k])
        old.extend(gap)
    raw, ends = mixed(old)
    return raw, "\n".join(new) + "\n", ends, kept


def _quality_case(blocks_str: str) -> None:
    """Subprocess entry: print the CRLF-kept counts, round trip and time."""
    raw, edited, ends, kept = repeated_gaps(int(blocks_str))
    start = time.perf_counter()
    out = roundtrip(raw, edited)
    elapsed = time.perf_counter() - start
    text, eol = LineEndings.read(out)
    assert text == edited, "lost or merged lines"
    crlf = [(i, j) for i, j in kept if ends[i] == "\r\n"]
    still = sum(1 for _, j in crlf if eol.endings[j] == "\r\n")
    # no CRLF on an inserted line: every output CRLF is on an unchanged line
    unchanged_new = {j for _, j in kept}
    stray = sum(
        1 for j, e in enumerate(eol.endings) if e == "\r\n" and j not in unchanged_new
    )
    print(f"KEPT {still} TOTAL {len(crlf)} STRAY {stray} ELAPSED {elapsed:.3f}")


def test_repeated_gaps_edited_at_both_ends_keep_crlf() -> None:
    out = run_case("_quality_case", 50)  # 50 blocks of 1001 lines: 50,050 lines
    kept, total = field(out, "KEPT"), field(out, "TOTAL")
    elapsed = field(out, "ELAPSED")
    assert field(out, "STRAY") == 0, out
    assert kept >= QUALITY_MIN * total, f"kept {kept:.0f}/{total:.0f} CRLF"
    assert elapsed < QUALITY_BOUND, f"{elapsed:.2f}s >= {QUALITY_BOUND}s"


# ---------------------------------------------------------------------------
# 2. Many gaps just under the difflib bound (red now: ~1.2 s here, 2.3 s review)
# ---------------------------------------------------------------------------

NEAR = 498  # 498 x 498 = 248,004 pairs, just under the 250k bound
NEAR_BOUND = 0.6  # seconds per 50k lines


def near_bound_gaps(n: int) -> tuple[str, str]:
    """(raw, edited): unique anchors, then 498 identical lines, repeated to `n`.

    In the new text the first and last line of every gap are replaced.
    """
    old: list[str] = []
    new: list[str] = []
    b = 0
    while len(old) < n:
        old.append(f"anchor {b}")
        new.append(f"anchor {b}")
        old.extend(["same"] * NEAR)
        new.extend([f"edit A {b}"] + ["same"] * (NEAR - 2) + [f"edit B {b}"])
        b += 1
    raw, _ = mixed(old)
    return raw, "\n".join(new) + "\n"


def _near_case(n_str: str) -> None:
    raw, edited = near_bound_gaps(int(n_str))
    start = time.perf_counter()
    out = roundtrip(raw, edited)
    elapsed = time.perf_counter() - start
    assert LineEndings.read(out)[0] == edited, "lost or merged lines"
    print(f"ELAPSED {elapsed:.3f}")


@pytest.mark.parametrize("n", [50_000, 100_000])
def test_many_gaps_just_under_difflib_bound_are_fast(n: int) -> None:
    bound = NEAR_BOUND * n / 50_000
    elapsed = field(run_case("_near_case", n), "ELAPSED")
    assert elapsed < bound, f"{n} lines: {elapsed:.2f}s >= {bound:.2f}s"


# ---------------------------------------------------------------------------
# 3. Staircase guard (control: must stay green)
# ---------------------------------------------------------------------------

STAIR_BOUND = 2.0  # seconds


def staircase(n: int) -> tuple[str, str]:
    """(raw, edited) for the #202 staircase: old x1 j x2 j ..., new x2 x1 x3 x2 ..."""
    m = n // 2
    old = [line for k in range(1, m + 1) for line in (f"x{k}", "j")]
    new = [line for k in range(1, m + 1) for line in (f"x{k + 1}", f"x{k}")]
    raw, _ = mixed(old)
    return raw, "\n".join(new) + "\n"


def _staircase_case(n_str: str) -> None:
    raw, edited = staircase(int(n_str))
    start = time.perf_counter()
    out = roundtrip(raw, edited)  # a RecursionError here is a failure
    elapsed = time.perf_counter() - start
    assert LineEndings.read(out)[0] == edited, "lost or merged lines"
    print(f"ELAPSED {elapsed:.3f}")


def test_staircase_50k_stays_fast_and_roundtrips() -> None:
    elapsed = field(run_case("_staircase_case", 50_000), "ELAPSED")
    assert elapsed < STAIR_BOUND, f"staircase 50k: {elapsed:.2f}s >= {STAIR_BOUND}s"


# ---------------------------------------------------------------------------
# 4. Invariants under a tiny difflib budget (control: must stay green)
# ---------------------------------------------------------------------------

LINES = ["a", "a", "b", "c", "", "d", "e"]
ENDINGS = ["\n", "\r\n", "\r"]


def _random_raw(rng: random.Random) -> str:
    n = rng.randint(1, 30)
    pairs = [(rng.choice(LINES), rng.choice(ENDINGS)) for _ in range(n)]
    if rng.random() < 0.3:
        pairs[-1] = (pairs[-1][0], "")
    return "".join(line + end for line, end in pairs)


def _random_edit(rng: random.Random, text: str) -> str:
    lines = text.split("\n")
    for _ in range(rng.randint(1, 5)):
        op = rng.choice(["insert", "delete", "replace", "swap"])
        i = rng.randint(0, len(lines) - 1)
        if op == "insert":
            lines.insert(i, rng.choice(LINES + ["new"]))
        elif op == "delete" and len(lines) > 1:
            del lines[i]
        elif op == "swap" and len(lines) > 1:
            k = rng.randint(0, len(lines) - 1)
            lines[i], lines[k] = lines[k], lines[i]
        else:
            lines[i] = rng.choice(LINES + ["new"])
    return "\n".join(lines)


def _check_invariants(raw: str, edited: str, out: str) -> str | None:
    """None if `out` is valid for `raw` edited to `edited`, else what is wrong."""
    if LineEndings.read(out)[0] != edited:
        return "does not round-trip"
    _, old = LineEndings.read(raw)
    _, got = LineEndings.read(out)
    new_lines = edited.split("\n")
    have = Counter(zip(old.lines, old.endings))
    used = {e for e in old.endings if e}
    # a file with one ending throughout (e.g. all `\r`) keeps it on every line
    majority = next(iter(used)) if len(used) == 1 else old.majority
    for j, (line, end) in enumerate(zip(new_lines[:-1], got.endings)):
        if end == majority:
            continue
        # the #202 fix-up: a blank line after a kept `\r` ends `\r` too
        if end == "\r" and not line and j and got.endings[j - 1] == "\r":
            continue
        # any other non-majority ending is kept from an old line, used once
        if have[(line, end)] <= 0:
            return f"line {j} {line!r} ends {end!r} but no unused old line did"
        have[(line, end)] -= 1
    return None


@pytest.mark.parametrize("budget", [4, 1])
def test_random_edits_under_tiny_budget_keep_invariants(
    monkeypatch: pytest.MonkeyPatch, budget: int
) -> None:
    monkeypatch.setattr(LineEndings, "_DIFFLIB_MAX_PAIRS", budget)
    rng = random.Random(213 + budget)
    failures = []
    for _ in range(400):
        raw = _random_raw(rng)
        text, eol = LineEndings.read(raw)
        edited = _random_edit(rng, text)
        out = eol.apply(edited)
        assert set(endings_of(out)) <= {"\n", "\r\n", "\r"}
        problem = _check_invariants(raw, edited, out)
        if problem:
            failures.append((raw, edited, out, problem))
    assert not failures, f"{len(failures)} bad, e.g. {failures[:3]!r}"


def test_invariant_checker_catches_a_stray_crlf() -> None:
    # Mutant guard: the checker rejects a CRLF on a line no old CRLF line matches
    assert _check_invariants("a\r\nb\nc\n", "a\nb\nc\n", "a\r\nb\nc\n") is None
    assert _check_invariants("a\r\nb\nc\n", "a\nb\nc\n", "a\r\nb\r\nc\n") is not None
    assert _check_invariants("a\r\nb\nc\n", "a\na\nc\n", "a\r\na\r\nc\n") is not None
