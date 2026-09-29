"""The bashes a workflow `run:` step is tested under (#940): the first `bash` on
PATH (CI's ubuntu bash 5, or a Mac's Homebrew bash) and, when it is a different
binary, the system `/bin/bash`, which on macOS is bash 3.2. A step runner takes the
bash as a REQUIRED argument, which a test gets from the `bash` fixture: only tests that
spawn bash run once per bash, and a caller can't forget it (#981)."""

from __future__ import annotations

import os
import shutil

import pytest


def bashes() -> list[str]:
    found = ["bash"]
    first = shutil.which("bash")
    if os.path.exists("/bin/bash") and (
        first is None or os.path.realpath(first) != os.path.realpath("/bin/bash")
    ):
        found.append("/bin/bash")
    return found


@pytest.fixture(params=bashes())
def bash(request: pytest.FixtureRequest) -> str:
    """Each bash a workflow step runs under, for a test that spawns one (#940, #981)."""
    return str(request.param)
