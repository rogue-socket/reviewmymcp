"""Evaluator discovery and registration."""

from __future__ import annotations

from reviewmymcp.evaluators.base import Evaluator, EvaluatorConfig, EvaluatorResult
from reviewmymcp.ingest.schema import McpEvent, ServerMeta

_REGISTRY: dict[str, Evaluator] = {}


def register(evaluator: Evaluator) -> Evaluator:
    """Register an evaluator instance. Returns it for use as a decorator target."""
    key = f"{evaluator.dimension}"
    if key in _REGISTRY:
        raise ValueError(f"Evaluator already registered for dimension: {key}")
    _REGISTRY[key] = evaluator
    return evaluator


def get_all() -> list[Evaluator]:
    return list(_REGISTRY.values())


def get_by_dimension(dimension: str) -> Evaluator | None:
    return _REGISTRY.get(dimension)


def get_dimensions() -> list[str]:
    return sorted(_REGISTRY.keys())


def run_all(
    events: list[McpEvent],
    server_meta: ServerMeta,
    config: EvaluatorConfig,
    dimensions: list[str] | None = None,
) -> list[EvaluatorResult]:
    """Run all registered evaluators (or a filtered subset) and return results."""
    evaluators = get_all()
    if dimensions:
        evaluators = [e for e in evaluators if e.dimension in dimensions]
    results = []
    for evaluator in evaluators:
        result = evaluator.evaluate(events, server_meta, config)
        results.append(result)
    return results
