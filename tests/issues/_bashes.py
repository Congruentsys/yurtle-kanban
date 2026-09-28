"""The bashes a workflow `run:` step is tested under (#940): the first `bash` on
PATH (CI's ubuntu bash 5, or a Mac's Homebrew bash) and, when it is a different
binary, the system `/bin/bash`, which on macOS is bash 3.2. A module that imports
`each_bash` runs every test under each; its step runner reads `_bashes.BASH`."""

from __future__ import annotations

import os
import shutil

import pytest

BASH = "bash"


def bashes() -> list[str]:
    found = ["bash"]
    first = shutil.which("bash")
    if os.path.exists("/bin/bash") and (
        first is None or os.path.realpath(first) != os.path.realpath("/bin/bash")
    ):
        found.append("/bin/bash")
    return found


@pytest.fixture(autouse=True, params=bashes())
def each_bash(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(f"{__name__}.BASH", request.param)
    return str(request.param)
