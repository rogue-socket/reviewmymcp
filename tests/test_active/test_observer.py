"""Tests for the active check observer."""

from reviewmymcp.active.agent_loop import AgentTurn, TaskSession, ToolCallRecord
from reviewmymcp.active.observer import SignalType, analyze_session
from reviewmymcp.active.task_library import AgentTask, TaskCategory


def _make_task(**kwargs) -> AgentTask:
    defaults = {
        "task_id": "test_task",
        "category": TaskCategory.SINGLE_TOOL,
        "instruction": "Test instruction",
        "expected_tools": ["search_documents"],
        "success_criteria": "success",
        "max_turns": 5,
    }
    defaults.update(kwargs)
    return AgentTask(**defaults)


def _make_session(task: AgentTask, turns: list[AgentTurn], completed: bool = True, gave_up: bool = False) -> TaskSession:
    total_calls = sum(len(t.tool_calls) for t in turns)
    unique = set()
    errors = 0
    for t in turns:
        for tc in t.tool_calls:
            unique.add(tc.tool_name)
            if tc.is_error:
                errors += 1

    return TaskSession(
        task=task,
        turns=turns,
        completed=completed,
        agent_gave_up=gave_up,
        total_tool_calls=total_calls,
        unique_tools_used=unique,
        errors_encountered=errors,
    )


def test_successful_single_tool():
    task = _make_task()
    turns = [
        AgentTurn(
            turn_number=1,
            reasoning="I should search",
            tool_calls=[
                ToolCallRecord(turn=1, tool_name="search_documents", arguments={"query": "test"}, is_error=False)
            ],
            chose_to_stop=True,
        ),
    ]
    session = _make_session(task, turns, completed=True)
    analysis = analyze_session(session)

    signals = {o.signal for o in analysis.observations}
    assert SignalType.TOOL_FOUND in signals
    assert SignalType.CORRECT_ARGS_FIRST_TRY in signals
    assert SignalType.TASK_COMPLETED in signals
    assert analysis.success


def test_tool_not_found():
    task = _make_task(expected_tools=["search_documents"])
    turns = [
        AgentTurn(
            turn_number=1,
            tool_calls=[
                ToolCallRecord(turn=1, tool_name="wrong_tool", arguments={}, is_error=False)
            ],
            chose_to_stop=True,
        ),
    ]
    session = _make_session(task, turns, completed=True)
    analysis = analyze_session(session)

    signals = {o.signal for o in analysis.observations}
    assert SignalType.TOOL_NOT_FOUND in signals
    assert SignalType.WRONG_TOOL_CHOSEN in signals


def test_arg_struggle_then_success():
    task = _make_task()
    turns = [
        AgentTurn(
            turn_number=1,
            tool_calls=[
                ToolCallRecord(turn=1, tool_name="search_documents", arguments={}, is_error=True)
            ],
        ),
        AgentTurn(
            turn_number=2,
            tool_calls=[
                ToolCallRecord(turn=2, tool_name="search_documents", arguments={"query": "test"}, is_error=False)
            ],
            chose_to_stop=True,
        ),
    ]
    session = _make_session(task, turns, completed=True)
    analysis = analyze_session(session)

    signals = {o.signal for o in analysis.observations}
    assert SignalType.ARG_STRUGGLE in signals
    assert SignalType.ERROR_RECOVERED in signals


def test_agent_gave_up():
    task = _make_task()
    turns = [
        AgentTurn(
            turn_number=1,
            tool_calls=[
                ToolCallRecord(turn=1, tool_name="search_documents", arguments={}, is_error=True)
            ],
        ),
    ]
    session = _make_session(task, turns, completed=False, gave_up=True)
    analysis = analyze_session(session)

    signals = {o.signal for o in analysis.observations}
    assert SignalType.GAVE_UP in signals
    assert not analysis.success


def test_unhelpful_error_detected():
    task = _make_task()
    turns = [
        AgentTurn(
            turn_number=1,
            tool_calls=[
                ToolCallRecord(
                    turn=1,
                    tool_name="search_documents",
                    arguments={},
                    is_error=True,
                    result={"error": {"code": -1, "message": "internal error"}},
                )
            ],
        ),
    ]
    session = _make_session(task, turns, completed=False, gave_up=True)
    analysis = analyze_session(session)

    signals = {o.signal for o in analysis.observations}
    assert SignalType.UNHELPFUL_ERROR in signals


def test_chaining_success():
    task = _make_task(
        task_id="chain_test",
        category=TaskCategory.MULTI_STEP,
        expected_tools=["search_documents", "get_document"],
    )
    turns = [
        AgentTurn(
            turn_number=1,
            tool_calls=[
                ToolCallRecord(turn=1, tool_name="search_documents", arguments={"query": "test"}, is_error=False)
            ],
        ),
        AgentTurn(
            turn_number=2,
            tool_calls=[
                ToolCallRecord(turn=2, tool_name="get_document", arguments={"document_id": "123"}, is_error=False)
            ],
            chose_to_stop=True,
        ),
    ]
    session = _make_session(task, turns, completed=True)
    analysis = analyze_session(session)

    signals = {o.signal for o in analysis.observations}
    assert SignalType.CHAINING_SUCCESS in signals


def test_chaining_failure():
    task = _make_task(
        task_id="chain_fail",
        category=TaskCategory.MULTI_STEP,
        expected_tools=["search_documents", "get_document"],
    )
    turns = [
        AgentTurn(
            turn_number=1,
            tool_calls=[
                ToolCallRecord(turn=1, tool_name="search_documents", arguments={"query": "test"}, is_error=False)
            ],
            chose_to_stop=True,
        ),
    ]
    session = _make_session(task, turns, completed=True)
    analysis = analyze_session(session)

    signals = {o.signal for o in analysis.observations}
    assert SignalType.CHAINING_FAILURE in signals
