"""Tests for audit configuration validation."""

from reviewmymcp.config import AuditConfig


def test_audit_config_accepts_efficiency_thresholds():
    config = AuditConfig.model_validate({"thresholds": {"description_bloat_single": 1}})

    assert config.thresholds == {"description_bloat_single": 1}
