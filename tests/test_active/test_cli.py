"""Tests for active checker CLI integration."""

from click.testing import CliRunner

from reviewmymcp.cli import cli


def test_active_audit_is_registered_with_top_level_cli():
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "active-audit" in result.output
