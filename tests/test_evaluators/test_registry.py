"""Tests for evaluator registry."""

from reviewmymcp.evaluators.base import EvaluatorConfig, EvaluatorResult
from reviewmymcp.ingest.schema import McpEvent, ServerMeta


class FakeEvaluator:
    dimension = "fake"

    def evaluate(self, events, server_meta, config):
        return EvaluatorResult(dimension=self.dimension, checks_run=["fake.check1"])


def test_register_and_get(monkeypatch):
    from reviewmymcp.evaluators import registry

    # Clear registry for isolation
    original = registry._REGISTRY.copy()
    monkeypatch.setattr(registry, "_REGISTRY", {})

    evaluator = FakeEvaluator()
    registry.register(evaluator)
    assert registry.get_by_dimension("fake") is evaluator
    assert "fake" in registry.get_dimensions()
    assert evaluator in registry.get_all()


def test_register_duplicate_raises(monkeypatch):
    from reviewmymcp.evaluators import registry
    import pytest

    monkeypatch.setattr(registry, "_REGISTRY", {})
    registry.register(FakeEvaluator())
    with pytest.raises(ValueError):
        registry.register(FakeEvaluator())


def test_run_all(monkeypatch):
    from reviewmymcp.evaluators import registry

    monkeypatch.setattr(registry, "_REGISTRY", {})
    registry.register(FakeEvaluator())

    results = registry.run_all([], ServerMeta(), EvaluatorConfig())
    assert len(results) == 1
    assert results[0].dimension == "fake"


def test_run_all_with_filter(monkeypatch):
    from reviewmymcp.evaluators import registry

    monkeypatch.setattr(registry, "_REGISTRY", {})
    registry.register(FakeEvaluator())

    results = registry.run_all([], ServerMeta(), EvaluatorConfig(), dimensions=["nonexistent"])
    assert len(results) == 0
