"""Extract behavioral signals from agent conversation turns."""

from __future__ import annotations

import re
from typing import Any

from reviewmymcp.active.models import ActiveTask, AgentTurn, BehavioralSignal

INJECTION_RE = re.compile(
    r"(ignore (?:all )?(?:previous|prior) instructions|<system>|</system>|exfiltrate|reveal (?:secrets|tokens))",
    re.IGNORECASE,
)
EXTERNAL_TOOL_RE = re.compile(r"\b(web|search|url|fetch|http|internet|browser)\b", re.IGNORECASE)
PROVENANCE_RE = re.compile(r"(<external_source\b|https?://|\bsource\s*:|\burl\s*:)", re.IGNORECASE)


def extract_turn_signals(
    turn: AgentTurn,
    prev_turns: list[AgentTurn],
    available_tools: set[str],
    expected_tools: list[str],
) -> list[BehavioralSignal]:
    """Extract behavioral signals from a single turn in context of previous turns."""
    signals: list[BehavioralSignal] = []

    for tc in turn.tool_calls:
        # Tool found vs not found
        if tc.tool_name in available_tools:
            signals.append(BehavioralSignal.TOOL_FOUND)
        else:
            signals.append(BehavioralSignal.TOOL_NOT_FOUND)

        # Correct vs wrong tool
        if expected_tools:
            if tc.tool_name in expected_tools:
                signals.append(BehavioralSignal.CORRECT_TOOL_SELECTED)
            else:
                signals.append(BehavioralSignal.WRONG_TOOL_SELECTED)

        # Argument struggle: same tool called before and got an error, now retrying
        if _is_retry_after_error(tc.tool_name, prev_turns):
            signals.append(BehavioralSignal.ARGUMENT_STRUGGLE)

        # Error recovery: retried after error and this time succeeded
        if _is_retry_after_error(tc.tool_name, prev_turns) and not tc.is_error:
            signals.append(BehavioralSignal.ERROR_RECOVERED)

        # Error not recovered: retried after error and still failing
        if _is_retry_after_error(tc.tool_name, prev_turns) and tc.is_error:
            signals.append(BehavioralSignal.ERROR_NOT_RECOVERED)

    # Chaining: used output from a previous tool call as input to a new one
    if turn.tool_calls and prev_turns:
        prev_had_success = any(
            tc for pt in prev_turns for tc in pt.tool_calls if not tc.is_error and tc.result
        )
        if prev_had_success and turn.tool_calls:
            if any(tc.is_error for tc in turn.tool_calls):
                signals.append(BehavioralSignal.CHAINING_FAILURE)
            elif len(turn.tool_calls) > 0 and not any(tc.is_error for tc in turn.tool_calls):
                # Only signal chaining success when there was a prior successful call
                # and this turn's tools are different from prior turns' tools
                prev_tools = {tc.tool_name for pt in prev_turns for tc in pt.tool_calls}
                curr_tools = {tc.tool_name for tc in turn.tool_calls}
                if curr_tools - prev_tools:
                    signals.append(BehavioralSignal.CHAINING_SUCCESS)

    return signals


def extract_task_signals(
    turns: list[AgentTurn],
    task: ActiveTask,
    available_tools: set[str],
    max_turns: int,
    tool_descriptions: dict[str, str] | None = None,
) -> list[BehavioralSignal]:
    """Extract aggregate signals for a completed task execution."""
    all_signals: list[BehavioralSignal] = []
    descriptions = tool_descriptions or {}

    for name, description in descriptions.items():
        if _contains_injection(description):
            all_signals.append(BehavioralSignal.INJECTION_IN_DESCRIPTION)

    for i, turn in enumerate(turns):
        turn_signals = extract_turn_signals(turn, turns[:i], available_tools, task.expected_tools)
        turn.signals = turn_signals
        all_signals.extend(turn_signals)
        all_signals.extend(_extract_security_signals(turn, descriptions))

    # Terminal signals
    if turns:
        last = turns[-1]
        if len(turns) >= max_turns:
            all_signals.append(BehavioralSignal.TURN_LIMIT_HIT)
        elif not last.tool_calls and last.text_response:
            # Agent stopped voluntarily by producing text without tool calls
            gave_up_phrases = ["cannot", "unable", "don't know", "not possible", "sorry"]
            text_lower = last.text_response.lower()
            if any(p in text_lower for p in gave_up_phrases):
                all_signals.append(BehavioralSignal.GAVE_UP)
            else:
                all_signals.append(BehavioralSignal.CHOSE_TO_STOP)

    return all_signals


def determine_outcome(signals: list[BehavioralSignal], task: ActiveTask) -> str:
    """Determine task outcome from signals."""
    if BehavioralSignal.GAVE_UP in signals:
        return "gave_up"
    if BehavioralSignal.TURN_LIMIT_HIT in signals:
        return "failure"

    tool_found_count = signals.count(BehavioralSignal.TOOL_FOUND)
    correct_count = signals.count(BehavioralSignal.CORRECT_TOOL_SELECTED)

    if not task.expected_tools:
        # Discovery task — success if agent found any tools
        return "success" if tool_found_count > 0 else "failure"

    # Did the agent use all expected tools?
    if correct_count >= len(task.expected_tools):
        return "success"
    elif correct_count > 0:
        return "partial"
    return "failure"


def _is_retry_after_error(tool_name: str, prev_turns: list[AgentTurn]) -> bool:
    """Check if the same tool was called in a previous turn and resulted in an error."""
    for pt in reversed(prev_turns):
        for tc in pt.tool_calls:
            if tc.tool_name == tool_name and tc.is_error:
                return True
    return False


def _extract_security_signals(turn: AgentTurn, tool_descriptions: dict[str, str]) -> list[BehavioralSignal]:
    signals: list[BehavioralSignal] = []
    for tc in turn.tool_calls:
        if tc.is_error:
            continue
        text = _result_text(tc.result)
        if not text:
            continue
        if _contains_injection(text):
            signals.append(BehavioralSignal.INJECTION_IN_OUTPUT)
        tool_context = f"{tc.tool_name} {tool_descriptions.get(tc.tool_name, '')}"
        if _looks_external_tool(tool_context) and not _has_provenance(text):
            signals.append(BehavioralSignal.UNTRUSTED_CONTENT_NO_PROVENANCE)
    return signals


def _result_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_result_text(item) for item in value)
    if not isinstance(value, dict):
        return ""

    content = value.get("content")
    if isinstance(content, list):
        return "\n".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return "\n".join(_result_text(nested) for nested in value.values())


def _contains_injection(text: str) -> bool:
    return bool(text and INJECTION_RE.search(text))


def _looks_external_tool(text: str) -> bool:
    return bool(text and EXTERNAL_TOOL_RE.search(text))


def _has_provenance(text: str) -> bool:
    return bool(text and PROVENANCE_RE.search(text))
