"""Tests for the active check scorer."""

import json

from reviewmymcp.active.observer import Observation, SessionAnalysis, SignalType
from reviewmymcp.active.scorer import compute_summary_stats, score_sessions
from reviewmymcp.active.task_library import TaskCategory
from reviewmymcp.ingest.schema import ServerMeta
from reviewmymcp.scoring.grader import Grade


def _make_analysis(
    task_id: str,
    success: bool,
    observations: list[Observation],
    turns: int = 3,
    tools: list[str] | None = None,
) -> SessionAnalysis:
    return SessionAnalysis(
        session_id=f"session_{task_id}",
        task_id=task_id,
        category=TaskCategory.SINGLE_TOOL,
        observations=observations,
        turns_used=turns,
        tools_called=tools or [],
        success=success,
    )


def test_all_positive_signals_gives_grade_a():
    analyses = [
        _make_analysis(
            "t1",
            success=True,
            observations=[
                Observation(
                    signal=SignalType.TOOL_FOUND,
                    task_id="t1",
                    category=TaskCategory.SINGLE_TOOL,
                    details="Found it",
                ),
                Observation(
                    signal=SignalType.TASK_COMPLETED,
                    task_id="t1",
                    category=TaskCategory.SINGLE_TOOL,
                    details="Done",
                ),
            ],
        ),
    ]
    meta = ServerMeta(server_name="test")
    report = score_sessions(analyses, meta)
    assert report.overall_grade == Grade.A


def test_negative_signals_lower_grade():
    observations = [
        Observation(
            signal=SignalType.TOOL_NOT_FOUND,
            task_id="t1",
            category=TaskCategory.SINGLE_TOOL,
            details="Could not find tool",
            affected_tools=["missing_tool"],
        ),
        Observation(
            signal=SignalType.ERROR_NOT_RECOVERED,
            task_id="t1",
            category=TaskCategory.SINGLE_TOOL,
            details="Unrecoverable",
        ),
        Observation(
            signal=SignalType.GAVE_UP,
            task_id="t1",
            category=TaskCategory.SINGLE_TOOL,
            details="Gave up",
        ),
    ]
    analyses = [_make_analysis("t1", success=False, observations=observations)]
    meta = ServerMeta(server_name="test")
    report = score_sessions(analyses, meta)
    assert report.overall_grade.value in ("B", "C", "D", "F")


def test_mixed_sessions():
    good = _make_analysis(
        "t1",
        success=True,
        observations=[
            Observation(signal=SignalType.TASK_COMPLETED, task_id="t1", category=TaskCategory.SINGLE_TOOL, details="ok"),
        ],
    )
    bad = _make_analysis(
        "t2",
        success=False,
        observations=[
            Observation(
                signal=SignalType.TOOL_NOT_FOUND,
                task_id="t2",
                category=TaskCategory.SINGLE_TOOL,
                details="not found",
                affected_tools=["x"],
            ),
        ],
    )
    meta = ServerMeta(server_name="test")
    report = score_sessions([good, bad], meta)
    assert len(report.dimension_scores) > 0


def test_summary_stats():
    analyses = [
        _make_analysis("t1", success=True, observations=[], turns=3, tools=["a", "b"]),
        _make_analysis("t2", success=False, observations=[], turns=5, tools=["a"]),
        _make_analysis("t3", success=True, observations=[], turns=2, tools=["c"]),
    ]
    stats = compute_summary_stats(analyses)
    assert stats["total_tasks"] == 3
    assert stats["completed"] == 2
    assert stats["failed"] == 1
    assert stats["completion_rate"] == 2 / 3
    assert set(stats["unique_tools_used"]) == {"a", "b", "c"}
    assert stats["avg_turns_per_task"] == round((3 + 5 + 2) / 3, 1)


def test_empty_analyses():
    meta = ServerMeta(server_name="test")
    report = score_sessions([], meta)
    assert report.overall_grade == Grade.A


def test_active_report_json_output_contract():
    observations = [
        Observation(
            signal=SignalType.INJECTION_IN_OUTPUT,
            task_id="security_task",
            category=TaskCategory.MULTI_STEP,
            details="Tool output contained prompt injection",
            evidence={"tool": "search_web", "patterns": ["<system>"]},
            affected_tools=["search_web"],
        ),
        Observation(
            signal=SignalType.ARG_STRUGGLE,
            task_id="security_task",
            category=TaskCategory.MULTI_STEP,
            details="Agent needed two attempts",
            evidence={"total_attempts": 2},
            affected_tools=["save_report"],
        ),
    ]
    report = score_sessions(
        [_make_analysis("security_task", success=True, observations=observations, turns=4, tools=["search_web", "save_report"])],
        ServerMeta(server_name="active-contract"),
    )

    parsed = json.loads(report.model_dump_json())

    assert set(parsed) == {
        "server_meta",
        "overall_grade",
        "dimension_scores",
        "top_findings",
        "total_events",
        "total_sessions",
        "timestamp",
    }
    assert parsed["overall_grade"] == "D"
    assert parsed["total_events"] == 0
    assert parsed["total_sessions"] == 1
    assert len(parsed["dimension_scores"]) == 6

    dimension = parsed["dimension_scores"][0]
    assert {
        "dimension",
        "grade",
        "critical_count",
        "high_count",
        "medium_count",
        "low_count",
        "info_count",
        "findings",
    } <= set(dimension)

    finding_ids = {f["check_id"] for f in parsed["top_findings"]}
    assert "active.injection-in-output" in finding_ids
    assert "active.argument-difficulty" in finding_ids

    first_finding = parsed["top_findings"][0]
    assert first_finding["check_id"] == "active.injection-in-output"
    assert first_finding["severity"] == "critical"
    assert {
        "check_id",
        "severity",
        "title",
        "description",
        "evidence",
        "remediation",
        "affected_entity",
    } <= set(first_finding)
