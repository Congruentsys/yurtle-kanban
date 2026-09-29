"""Issue #1046: one pytest run whose paths go tests/issues → tests → tests/issues
used to lose tests/issues/conftest.py's fixtures (`claim_env`, `world`): "fixture
'claim_env' not found". They live in tests/conftest.py now; this runs that shape."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_mixed_path_order_finds_the_claim_fixtures() -> None:
    done = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "tests/issues/test_1046_probe_a.py",
            "tests/test_models.py",
            "tests/issues/test_823_takeover_one_rule.py",
        ],
        cwd=REPO, capture_output=True, text=True, timeout=600,
    )
    out = done.stdout + done.stderr
    assert "fixture 'claim_env' not found" not in out, out[-3000:]
    assert done.returncode == 0, out[-3000:]
