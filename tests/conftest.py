"""Suite-wide fixtures: a hermetic identity for every test.

#580: the actor resolves `--agent` -> `$YURTLE_AGENT` -> git `user.name` -> error.
Neither of the last two may come from the machine running the suite:

- a fleet machine may export `YURTLE_AGENT`, so every test starts with it unset
  (a test that wants it sets it with `monkeypatch.setenv`);
- CI has no git identity while a dev machine has a global one, so the suite
  points git's GLOBAL config at a suite-owned file holding `user.name` /
  `user.email` and ignores the system config. Global scope sits below a repo's
  local config, so a fixture that sets its own `user.name` still wins, and git
  commits in fixtures work without any identity on the host.

A test that exercises identity resolution opts out by overriding the variable,
e.g. `monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)` (see `no_global_git`
in tests/test_580_input_identity.py).
"""

from pathlib import Path

import pytest

SUITE_GIT_USER = "test-git-user"
SUITE_GIT_EMAIL = "test-git-user@example.invalid"


@pytest.fixture(scope="session")
def _suite_gitconfig(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("gitconfig") / "gitconfig"
    # no background `gc --auto` / maintenance: a detached repack pruning loose
    # objects while a test walks or deletes its repo fails it at random (#841)
    path.write_text(
        f"[user]\n\tname = {SUITE_GIT_USER}\n\temail = {SUITE_GIT_EMAIL}\n"
        "[gc]\n\tauto = 0\n\tautoDetach = false\n[maintenance]\n\tauto = false\n"
    )
    return path


@pytest.fixture(autouse=True)
def _hermetic_identity(monkeypatch: pytest.MonkeyPatch, _suite_gitconfig: Path) -> None:
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_suite_gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


# --- the #574 claim world and env, shared with tests/issues (#892) ----------------------
# Defined here, not in tests/issues/conftest.py: pytest drops that conftest's fixtures
# when one run's paths go tests/issues → tests → tests/issues (#1046). Their helpers are
# imported inside the fixtures, so a run of top-level tests alone never loads them.


@pytest.fixture
def claim_env(monkeypatch: pytest.MonkeyPatch):  # -> Iterator[None]
    """No prompting git, no inherited `YURTLE_AGENT`, an empty theme cache. NOT
    autouse: a module asks for it with `pytestmark = pytest.mark.usefixtures("claim_env")`."""
    from yurtle_kanban import config as config_mod

    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path):  # -> tests.issues.test_585_create_push_loop.World
    """The #574 claim tests' world: origin and both clones hold EXP-001 at `ready`,
    unassigned. A module that defines its own `world` still gets its own."""
    from tests.issues.test_574_claim import ITEM, item_text, push_from_a
    from tests.issues.test_585_create_push_loop import World

    w = World(tmp_path)
    push_from_a(w, {ITEM: item_text("ready")}, "seed EXP-001")
    return w
