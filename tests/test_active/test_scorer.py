"""Tests for active audit scorer."""

from __future__ import annotations

from reviewmymcp.active.models import (
    ActiveTask,
    AgentTurn,
    BehavioralSignal,
    TaskCategory,
    TaskExecution,
    ToolCallAttempt,
)
from reviewmymcp.active.scorer import score_executions
from tests.conftest import make_server_meta


def _execution(signals, outcome="success", turns=None):
    return TaskExecution(
        task=ActiveTask(category=TaskCategory.SINGLE_TOOL, description="test"),
        turns=turns or [],
        signals=signals,
        outcome=outcome,
        total_turns=len(turns) if turns else 1,
    )


def _turn_with_calls(n_calls=1):
    return AgentTurn(
        turn_number=1,
        tool_calls=[ToolCallAttempt(tool_name=f"t{i}") for i in range(n_calls)],
    )


def test_all_successes_high_scores():
    execs = [
        _execution(
            [BehavioralSignal.TOOL_FOUND, BehavioralSignal.CORRECT_TOOL_SELECTED, BehavioralSignal.CHOSE_TO_STOP],
            outcome="success",
            turns=[_turn_with_calls(1)],
        )
        for _ in range(5)
    ]
    report = score_executions(execs, make_server_meta())

    assert report.total_tasks == 5
    for ds in report.dimension_scores:
        assert ds.score >= 90
        assert ds.grade == "A"


def test_all_failures_low_scores():
    execs = [
        _execution(
            [BehavioralSignal.TOOL_NOT_FOUND, BehavioralSignal.TURN_LIMIT_HIT],
            outcome="failure",
        )
        for _ in range(5)
    ]
    report = score_executions(execs, make_server_meta())

    # Tool discovery should be low (all not_found)
    discovery = next(ds for ds in report.dimension_scores if ds.dimension == "tool_discovery")
    assert discovery.grade == "F"
    assert discovery.score == 0

    # Task completion should be low (all failures)
    completion = next(ds for ds in report.dimension_scores if ds.dimension == "task_completion")
    assert completion.grade == "F"
    assert completion.score == 0


def test_mixed_outcomes():
    execs = [
        _execution(
            [BehavioralSignal.TOOL_FOUND, BehavioralSignal.CORRECT_TOOL_SELECTED],
            outcome="success",
            turns=[_turn_with_calls(1)],
        ),
        _execution(
            [BehavioralSignal.TOOL_FOUND],
            outcome="partial",
            turns=[_turn_with_calls(1)],
        ),
        _execution(
            [BehavioralSignal.TOOL_NOT_FOUND],
            outcome="failure",
        ),
    ]
    report = score_executions(execs, make_server_meta())

    # Task completion: 1 success + 0.5 partial = 1.5 / 3 = 50%
    completion = next(ds for ds in report.dimension_scores if ds.dimension == "task_completion")
    assert 49 <= completion.score <= 51
    assert completion.grade == "D"


def test_argument_struggle_reduces_quality():
    execs = [
        _execution(
            [BehavioralSignal.ARGUMENT_STRUGGLE, BehavioralSignal.ARGUMENT_STRUGGLE],
            outcome="success",
            turns=[_turn_with_calls(2)],
        ),
    ]
    report = score_executions(execs, make_server_meta())

    quality = next(ds for ds in report.dimension_scores if ds.dimension == "argument_quality")
    assert quality.score < 100


def test_error_recovery_scoring():
    execs = [
        _execution(
            [BehavioralSignal.ERROR_RECOVERED, BehavioralSignal.ERROR_NOT_RECOVERED],
            outcome="partial",
        ),
    ]
    report = score_executions(execs, make_server_meta())

    recovery = next(ds for ds in report.dimension_scores if ds.dimension == "error_recovery")
    assert recovery.score == 50.0
    assert recovery.grade == "D"


def test_no_executions():
    report = score_executions([], make_server_meta())
    assert report.total_tasks == 0
    for ds in report.dimension_scores:
        assert ds.score == 100.0
        assert ds.grade == "A"


def test_grade_thresholds():
    """Verify grade boundaries: A>=90, B>=75, C>=60, D>=40, F<40."""
    from reviewmymcp.active.scorer import _grade_from_score

    assert _grade_from_score(100) == "A"
    assert _grade_from_score(90) == "A"
    assert _grade_from_score(89.9) == "B"
    assert _grade_from_score(75) == "B"
    assert _grade_from_score(74.9) == "C"
    assert _grade_from_score(60) == "C"
    assert _grade_from_score(40) == "D"
    assert _grade_from_score(39.9) == "F"
    assert _grade_from_score(0) == "F"
