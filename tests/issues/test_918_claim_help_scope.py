"""Issue #918: `claim --help` and `update --help` say what origin's config judges.

Since #865 the fetched item's parse, the move's legality and the written status
come from origin's config, themes and workflows too, not only WIP limits,
board paths and ignore patterns (#831). The help must say so, and that gates
stay local.
"""

from __future__ import annotations

from click.testing import CliRunner

from yurtle_kanban.cli import main


def _help(*cmd: str) -> str:
    result = CliRunner().invoke(main, [*cmd, "--help"])
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split())


def test_claim_help_names_origin_themes_and_workflows() -> None:
    text = _help("claim")
    for phrase in ("origin's own config", ".kanban/workflows/", "legal", "Gate checks"):
        assert phrase in text, (phrase, text)


def test_update_help_names_origin_config_for_push() -> None:
    text = _help("update")
    assert "origin's own config, themes and workflows" in text, text
