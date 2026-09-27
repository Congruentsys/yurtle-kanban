"""Suite-wide fixtures.

#580: a fleet machine may export `YURTLE_AGENT`. The actor identity now reads it
first, so a test that asserts `kb:by` or a comment heading would pass or fail
depending on the machine. Every test starts with it unset; a test that wants it
sets it with `monkeypatch.setenv`.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_yurtle_agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
